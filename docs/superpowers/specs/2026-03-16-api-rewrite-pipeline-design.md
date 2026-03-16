# API-Based SEO Rewrite Pipeline Design

## Goal

Decouple the LLM rewriting step from Claude Code, making the entire SEO pipeline (fetch → rank → crawl → audit → optimize) runnable as a single CLI command via `uv run python main.py all` or `uv run python main.py optimize`.

## Current State

- Steps 1-4 (fetch/rank/crawl/audit) are pure Python scripts in `steps/`.
- The LLM rewriting step lives in `.claude/skills/seo-optimize/` and depends on Claude Code's Agent tool to invoke the LLM.
- The skill's scripts (`prepare_contexts.py`, `merge_results.py`, `postprocess.py`) are already standalone Python — only the API call is missing.

## Design

### Architecture

New file `steps/optimize.py` as the 5th pipeline step, with the same `run(config, output_dir)` interface:

```
steps/optimize.py  run(config, output_dir)
    ├── 1. prepare_contexts()    — import from existing script
    ├── 2. rewrite_batches()     — asyncio + litellm acompletion()
    ├── 3. merge_results()       — import from existing script
    └── 4. postprocess()         — import from existing script
```

### Environment & Configuration

**`.env` file** (loaded via `python-dotenv`):
```
LITELLM_PROXY_API_BASE=https://your-litellm-proxy.example.com
LITELLM_PROXY_API_KEY=sk-xxx
```

**`config.yaml` new `optimize` section**:
```yaml
optimize:
  model: "claude-sonnet-4-20250514"   # litellm model name
  top: 30                             # default Top N pages
  batch_size: 10                      # pages per batch
  concurrency: 3                      # max parallel API calls
  temperature: 0.3                    # generation temperature
  max_retries: 2                      # retries per failed batch
```

### CLI Interface

```bash
uv run python main.py optimize                # Top 30 (config default)
uv run python main.py optimize --top 50       # Top 50
uv run python main.py optimize --range 31-60  # Incremental 31-60
uv run python main.py all                     # All 5 steps
uv run python main.py all --skip fetch        # Skip GSC fetch, run rest
```

### `steps/optimize.py` — Detailed Design

#### 1. Data Preparation

Import and call functions from `.claude/skills/seo-optimize/scripts/prepare_contexts.py`:
- `parse_range()`, `load_priority_ranked()`, `load_audit_report()`, `load_metadata()`, `load_zero_click_queries()`, `build_contexts()`

Output: batch files at `output/seo/tmp/seo_batch_*.json` + context/metadata files.

#### 2. API Calls (`rewrite_batches`)

```python
import litellm
from dotenv import load_dotenv

load_dotenv()

async def rewrite_one_batch(batch_path, result_path, prompt_template, config):
    """Process one batch: read contexts, call LLM, save result."""
    with open(batch_path) as f:
        pages = json.load(f)

    prompt = prompt_template + "\n\n" + json.dumps(pages, ensure_ascii=False)

    response = await litellm.acompletion(
        model=config["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=config.get("temperature", 0.3),
        api_base=os.environ.get("LITELLM_PROXY_API_BASE"),
        api_key=os.environ.get("LITELLM_PROXY_API_KEY"),
    )

    text = response.choices[0].message.content
    result = extract_json(text)  # handle markdown code fences

    with open(result_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

async def rewrite_batches(tmp_dir, prompt_template, config):
    """Process all batches with concurrency control."""
    sem = asyncio.Semaphore(config.get("concurrency", 3))
    batch_files = sorted(glob.glob(f"{tmp_dir}/seo_batch_*.json"))
    # exclude existing result files
    batch_files = [f for f in batch_files if "_result" not in f]

    async def worker(batch_path):
        async with sem:
            i = # extract batch index from filename
            result_path = f"{tmp_dir}/seo_batch_{i}_result.json"
            for attempt in range(config.get("max_retries", 2) + 1):
                try:
                    await rewrite_one_batch(batch_path, result_path, prompt_template, config)
                    return (batch_path, True, None)
                except Exception as e:
                    if attempt == config.get("max_retries", 2):
                        return (batch_path, False, str(e))

    results = await asyncio.gather(*[worker(f) for f in batch_files])
    return results
```

**JSON extraction**: LLM responses may wrap JSON in ` ```json ... ``` `. Use regex to strip code fences before `json.loads()`.

**Retry logic**: Each batch retries up to `max_retries` times on failure (network error, JSON parse error). Failed batches are logged but don't block others.

#### 3. Merge Results

Import and call `merge_results.py`'s logic, or invoke it as subprocess:
```python
from scripts.merge_results import main as merge_main
```

#### 4. Post-processing

Import and call `postprocess.py`'s `postprocess_page()` and `merge_with_existing()`.

### Prompt Template

Reuse existing `templates/rewrite-prompt.md` as-is. The `{{OUTPUT_PATH}}` placeholder section is ignored when called via API (Agent-specific instruction). The prompt is loaded, the "output save" section is stripped, and page data is appended.

### `main.py` Changes

```python
# Register optimize subcommand
subparsers.add_parser("optimize", help="LLM rewrite SEO metadata")

# Add to STEPS for 'all' command
STEPS = ["fetch", "rank", "crawl", "audit", "optimize"]
```

The `optimize` subcommand accepts `--top N` and `--range start-end` arguments, passed through to `steps/optimize.py`.

### Dependency Changes

**`pyproject.toml`** additions:
```toml
litellm >= 1.30
python-dotenv >= 1.0
```

### File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `steps/optimize.py` | **Create** | 5th pipeline step: prepare + API + merge + postprocess |
| `main.py` | **Modify** | Register `optimize` subcommand, add to `all` flow |
| `config.yaml.example` | **Modify** | Add `optimize` section |
| `pyproject.toml` | **Modify** | Add `litellm`, `python-dotenv` dependencies |
| `.env.example` | **Create** | Template with `LITELLM_PROXY_API_BASE`, `LITELLM_PROXY_API_KEY` |
| `.gitignore` | **Modify** | Add `.env` |

### What Stays Unchanged

- `steps/fetch_gsc.py`, `rank.py`, `crawl.py`, `audit.py` — no changes
- `.claude/skills/seo-optimize/` — preserved for interactive Claude Code usage
- `scripts/prepare_contexts.py`, `merge_results.py`, `postprocess.py` — reused via import
- `templates/rewrite-prompt.md` — reused as prompt template

### Data Flow

```
config.yaml + .env
        ↓
main.py optimize --top 30
        ↓
steps/optimize.py
        │
        ├── prepare_contexts functions
        │       ↓
        │   output/seo/tmp/seo_batch_0.json
        │   output/seo/tmp/seo_batch_1.json
        │   output/seo/tmp/seo_batch_2.json
        │
        ├── asyncio + litellm.acompletion()  (3 concurrent)
        │       ↓
        │   output/seo/tmp/seo_batch_0_result.json
        │   output/seo/tmp/seo_batch_1_result.json
        │   output/seo/tmp/seo_batch_2_result.json
        │
        ├── merge_results functions
        │       ↓
        │   output/seo/tmp/seo_rewritten.json
        │
        └── postprocess functions
                ↓
            output/seo/optimized_metadata.json
            output/seo/original_metadata_backup.json
```

### Error Handling

- Missing `.env` or env vars → clear error message with setup instructions
- API call failure → retry N times per batch, log and skip on final failure
- JSON parse failure → retry (LLM may return malformed JSON), skip on final failure
- Missing pipeline outputs → prompt user to run prerequisite steps first
- Print summary: N batches succeeded, M failed, pages processed/skipped
