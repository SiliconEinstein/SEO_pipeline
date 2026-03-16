# API-Based SEO Rewrite Pipeline Design

## Goal

Decouple the LLM rewriting step from Claude Code, making the entire SEO pipeline (fetch → rank → crawl → audit → optimize) runnable as a single CLI command via `uv run python main.py all` or `uv run python main.py optimize`.

## Current State

- Steps 1-4 (fetch/rank/crawl/audit) are pure Python scripts in `steps/`, each exporting `run(config, output_dir) -> dict`.
- The LLM rewriting step lives in `.claude/skills/seo-optimize/` and depends on Claude Code's Agent tool to invoke the LLM.
- The skill's scripts (`prepare_contexts.py`, `merge_results.py`, `postprocess.py`) are standalone CLI scripts — their logic is mixed with `argparse` inside `main()`.

## Design

### Architecture

New file `steps/optimize.py` as the 5th pipeline step, with the same `run(config, output_dir)` interface.

The existing skill scripts under `.claude/skills/seo-optimize/scripts/` are **CLI tools** (argparse inside `main()`), not importable libraries. Rather than refactoring them or adding fragile `sys.path` hacks to import from `.claude/` paths, `steps/optimize.py` will **inline the needed logic directly** — the functions are straightforward data loading/processing and small enough to embed.

```
steps/optimize.py  run(config, output_dir)
    ├── 1. prepare_contexts()    — inline: load CSVs, build contexts, write batches
    ├── 2. rewrite_batches()     — asyncio + litellm.acompletion()
    ├── 3. merge_results()       — inline: glob batch results, json merge
    └── 4. postprocess()         — inline: validate, add OG/Twitter/Schema, write output
```

### Environment & Configuration

**`.env` file** (loaded via `python-dotenv`, added to `.gitignore`):
```
LITELLM_PROXY_API_BASE=https://your-litellm-proxy.example.com
LITELLM_PROXY_API_KEY=sk-xxx
```

**`config.yaml` new `optimize` section**:
```yaml
optimize:
  model: "claude-sonnet-4-20250514"   # litellm model name
  prompt_template: ".claude/skills/seo-optimize/templates/rewrite-prompt.md"
  top: 30                             # default Top N pages
  batch_size: 10                      # pages per batch
  concurrency: 3                      # max parallel API calls
  temperature: 0.3                    # generation temperature
  max_tokens: 4096                    # max output tokens per API call
  timeout: 120                        # per-request timeout in seconds
  max_retries: 2                      # retries per failed batch
```

Note: `prompt_template` path is relative to the project root.

### CLI Interface

The `optimize` step reads `--top` and `--range` from `config.yaml` by default. To support CLI overrides, `main.py` adds these as **global optional arguments** (not subcommand-specific), and `steps/optimize.py` reads them from config with CLI values taking precedence:

```bash
uv run python main.py optimize                # Top 30 (from config)
uv run python main.py optimize --top 50       # Override: Top 50
uv run python main.py optimize --range 31-60  # Override: range 31-60
uv run python main.py all                     # All 5 steps (optimize uses config defaults)
uv run python main.py all --skip fetch        # Skip GSC fetch, run rest
```

### `steps/optimize.py` — Detailed Design

#### 1. Data Preparation (`prepare_contexts`)

Inline the following logic (from `prepare_contexts.py`):
- `load_priority_ranked(path, start, end)` — read CSV slice
- `load_audit_report(path, target_paths)` — path → issues mapping
- `load_metadata(path, target_paths)` — path → metadata mapping
- `load_zero_click_queries(pattern, target_paths)` — path → top 5 queries
- `build_contexts(pages, audit, metadata, query_data, skipped)` — assemble context objects

Range parsing is done directly from config values (`top: int` and `range: str | None`), not from an argparse Namespace.

Output: batch files at `output/seo/tmp/seo_batch_*.json`, plus `seo_rewrite_contexts.json` and `seo_original_metadata.json`.

#### 2. API Calls (`rewrite_batches`)

```python
import asyncio
import glob
import json
import os
import re

import litellm
from dotenv import load_dotenv

load_dotenv()


def extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown code fences and prose."""
    # Try markdown code fence first
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())
    # Fallback: find outermost { ... } in the text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])
    return json.loads(text.strip())


def load_prompt_template(template_path: str) -> str:
    """Load prompt template, stripping the Agent-specific output section."""
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()
    # Strip from "## 输出保存" to "## 页面数据" (inclusive of both headers)
    content = re.sub(
        r"## 输出保存.*?(?=## 页面数据)", "", content, flags=re.DOTALL
    )
    # Strip the "## 页面数据" section and trailing text
    content = re.sub(r"## 页面数据.*$", "", content, flags=re.DOTALL)
    return content.rstrip()


async def rewrite_one_batch(batch_path, result_path, prompt_template, config):
    """Process one batch: read contexts, call LLM, save result."""
    with open(batch_path, "r", encoding="utf-8") as f:
        pages = json.load(f)

    prompt = prompt_template + "\n\n" + json.dumps(pages, ensure_ascii=False)

    response = await litellm.acompletion(
        model=config["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=config.get("temperature", 0.3),
        max_tokens=config.get("max_tokens", 4096),
        timeout=config.get("timeout", 120),
        api_base=os.environ.get("LITELLM_PROXY_API_BASE"),
        api_key=os.environ.get("LITELLM_PROXY_API_KEY"),
    )

    text = response.choices[0].message.content
    result = extract_json(text)

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


async def rewrite_batches(tmp_dir, prompt_template, config):
    """Process all batches with concurrency control and resume support."""
    sem = asyncio.Semaphore(config.get("concurrency", 3))
    batch_files = sorted(glob.glob(os.path.join(tmp_dir, "seo_batch_*.json")))
    batch_files = [f for f in batch_files if "_result" not in f]

    async def worker(batch_path):
        i = re.search(r"seo_batch_(\d+)\.json", os.path.basename(batch_path)).group(1)
        result_path = os.path.join(tmp_dir, f"seo_batch_{i}_result.json")

        # Resume support: skip if valid result already exists
        if os.path.exists(result_path):
            try:
                with open(result_path, "r", encoding="utf-8") as f:
                    json.load(f)
                print(f"  Batch {i}: skipped (result exists)")
                return (batch_path, True, None)
            except (json.JSONDecodeError, Exception):
                pass  # invalid result file, re-process

        async with sem:
            max_retries = config.get("max_retries", 2)
            for attempt in range(max_retries + 1):
                try:
                    await rewrite_one_batch(
                        batch_path, result_path, prompt_template, config
                    )
                    print(f"  Batch {i}: done")
                    return (batch_path, True, None)
                except Exception as e:
                    if attempt < max_retries:
                        print(f"  Batch {i}: retry {attempt+1}/{max_retries} ({e})")
                    else:
                        print(f"  Batch {i}: FAILED ({e})")
                        return (batch_path, False, str(e))

    results = await asyncio.gather(*[worker(f) for f in batch_files])
    return results
```

**Key points:**
- `extract_json()`: strips markdown code fences before `json.loads()`
- `load_prompt_template()`: removes the `## 输出保存` and `## 页面数据` sections (Agent-specific instructions)
- Resume support: if `seo_batch_N_result.json` exists and is valid JSON, skip that batch
- Timeout via `litellm.acompletion(timeout=...)` prevents hung requests
- `max_tokens=4096` ensures sufficient space for 10-page output (~200 tokens each)

#### 3. Merge Results

Inline: glob `seo_batch_*_result.json`, `json.load()` each, `dict.update()` into merged dict, write `seo_rewritten.json`. Report files with parse errors.

#### 4. Post-processing

Inline from `postprocess.py`:
- `smart_truncate(text, max_len, lang)` — word/sentence boundary truncation
- `ensure_brand_suffix(title, lang)` — append brand suffix, re-truncate
- `enhance_schema(schema_list, headline, desc, path, page_type, rewrite)` — Schema.org enrichment
- `postprocess_page(path, rewrite, orig, ctx)` — single page post-processing
- `merge_with_existing(output_path, new_data)` — incremental merge with prior runs

**Important**: `postprocess.py` hardcodes `BASE_URL`, `BRAND_SUFFIX`, `TITLE_MAX`, `DESC_MAX` as module constants. Inlined code must read these from config instead:
- `config["seo"]["base_url"]` → replaces `BASE_URL = "https://www.bohrium.com"`
- `config["seo"]["brand_suffix"]` → replaces `BRAND_SUFFIX = " | SciencePedia"`
- `config["seo"]["max_title_length"]` → replaces `TITLE_MAX = 60`
- `config["seo"]["max_desc_length"]` → replaces `DESC_MAX = 155`

Each inlined function group should include a source comment: `# Inlined from .claude/skills/seo-optimize/scripts/postprocess.py (2026-03-16)` to track divergence.

#### 5. Return Value

```python
return {
    "output_files": [optimized_path, backup_path],
    "summary": {
        "total_pages": total,
        "batches_succeeded": succeeded,
        "batches_failed": failed,
        "pages_processed": processed,
        "pages_skipped": skipped,
        "title_truncated": title_truncated_count,
        "desc_truncated": desc_truncated_count,
    },
}
```

### `main.py` Changes

1. Add `"optimize"` to `STEPS` list and `module_map`:
```python
STEPS = ["fetch", "rank", "crawl", "audit", "optimize"]

module_map = {
    "fetch": "fetch_gsc",
    "rank": "rank",
    "crawl": "crawl",
    "audit": "audit",
    "optimize": "optimize",
}
```

2. Add `--top` and `--range` as mutually exclusive optional arguments:
```python
opt_group = parser.add_mutually_exclusive_group()
opt_group.add_argument("--top", type=int, help="optimize: Top N pages")
opt_group.add_argument("--range", type=str, help="optimize: range e.g. 31-60")
```

3. Pass CLI args to `run()` via config override — before calling `_run_step("optimize", ...)`, inject CLI values into `config["optimize"]`:
```python
if args.top:
    config.setdefault("optimize", {})["top"] = args.top
if getattr(args, "range", None):
    config.setdefault("optimize", {})["range"] = args.range
```

### Breaking Change: `all` Command Now Includes `optimize`

Adding `optimize` to `STEPS` means `uv run python main.py all` will attempt to run the optimize step, which requires `.env` credentials. Users who previously ran `all` without LLM credentials will see an error.

**Mitigation**: The `optimize` step checks for env vars at the start of `run()`. If `LITELLM_PROXY_API_BASE` or `LITELLM_PROXY_API_KEY` is missing, it prints a warning and returns early with an empty result (not a hard error), so `all` still completes the other 4 steps:

```python
def run(config, output_dir):
    if not os.environ.get("LITELLM_PROXY_API_BASE") or not os.environ.get("LITELLM_PROXY_API_KEY"):
        print("  [optimize] 跳过: 未设置 LITELLM_PROXY_API_BASE / LITELLM_PROXY_API_KEY")
        print("  参考 .env.example 配置 LLM API 凭据")
        return {"output_files": [], "summary": {"skipped": True, "reason": "missing credentials"}}
    ...
```

Users can also explicitly skip: `uv run python main.py all --skip optimize`.

### Prompt Template

Reuse existing `templates/rewrite-prompt.md`. When loaded via API, strip:
- The entire `## 输出保存` section (Agent-specific "use Write tool" instruction)
- The `## 页面数据` header and trailing text (page data is appended programmatically)

The rest of the prompt (rules, format, examples) is used as-is.

### Dependency Changes

**`pyproject.toml`** additions:
```toml
litellm >= 1.30, < 2.0
python-dotenv >= 1.0
```

### File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `steps/optimize.py` | **Create** | 5th pipeline step: prepare + API + merge + postprocess (all logic inlined) |
| `main.py` | **Modify** | Add `optimize` to STEPS/module_map, add `--top`/`--range` args, inject into config |
| `config.yaml.example` | **Modify** | Add `optimize` section with model/concurrency/timeout/etc. |
| `pyproject.toml` | **Modify** | Add `litellm`, `python-dotenv` dependencies |
| `.env.example` | **Create** | Template with `LITELLM_PROXY_API_BASE`, `LITELLM_PROXY_API_KEY` |
| `.gitignore` | **Modify** | Add `.env` |

### What Stays Unchanged

- `steps/fetch_gsc.py`, `rank.py`, `crawl.py`, `audit.py` — no changes
- `.claude/skills/seo-optimize/` — preserved as-is for interactive Claude Code usage
- `templates/rewrite-prompt.md` — reused as prompt source (Agent sections stripped at load time)

### Data Flow

```
config.yaml + .env
        ↓
main.py optimize --top 30
        ↓
steps/optimize.py
        │
        ├── prepare_contexts (inline)
        │       ↓
        │   output/seo/tmp/seo_batch_0.json
        │   output/seo/tmp/seo_batch_1.json
        │   output/seo/tmp/seo_batch_2.json
        │
        ├── asyncio + litellm.acompletion()  (3 concurrent, resume-aware)
        │       ↓
        │   output/seo/tmp/seo_batch_0_result.json
        │   output/seo/tmp/seo_batch_1_result.json
        │   output/seo/tmp/seo_batch_2_result.json
        │
        ├── merge_results (inline)
        │       ↓
        │   output/seo/tmp/seo_rewritten.json
        │
        └── postprocess (inline)
                ↓
            output/seo/optimized_metadata.json
            output/seo/original_metadata_backup.json
```

### Error Handling

- Missing `.env` or env vars → clear error message: "请设置 LITELLM_PROXY_API_BASE 和 LITELLM_PROXY_API_KEY 环境变量，参考 .env.example"
- Missing pipeline outputs (audit_report.csv etc.) → "请先运行 `uv run python main.py all --skip fetch` 或对应步骤"
- API timeout → caught by litellm timeout param, triggers retry
- API call failure → retry up to `max_retries` times per batch, log and skip on final failure
- JSON parse failure (malformed LLM output) → retry, skip on final failure
- Interrupted run → resume support: existing valid result files are skipped on re-run
- Return summary: batches succeeded/failed, pages processed/skipped, truncation counts
