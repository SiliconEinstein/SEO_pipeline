"""
optimize.py — 第 5 步: LLM 重写 SEO 元数据

通过 litellm API 批量调用 LLM，重写 title/description/keywords/Schema.org。
整合 prepare_contexts + API rewrite + merge + postprocess 四个阶段。
"""

from __future__ import annotations

import asyncio
import copy
import csv
import glob
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import litellm
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# 1. Data preparation
# Inlined from .claude/skills/seo-optimize/scripts/prepare_contexts.py (2026-03-16)
# ---------------------------------------------------------------------------


def _parse_range(range_str: str | None, top: int) -> tuple[int, int]:
    """Parse range config, return (start, end) 0-based."""
    if range_str:
        parts = range_str.split("-")
        if len(parts) != 2:
            raise ValueError(f"无法解析范围 '{range_str}'，格式应为 start-end，如 31-60")
        return int(parts[0]) - 1, int(parts[1])
    return 0, top


def _load_priority_ranked(path: str, start: int, end: int) -> list[dict]:
    """Load priority_ranked.csv rows in [start, end) range."""
    pages = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i < start:
                continue
            if i >= end:
                break
            pages.append(row)
    return pages


def _load_audit_report(path: str, target_paths: set[str]) -> dict[str, list[str]]:
    """Load audit_report.csv, return path -> issues mapping."""
    audit = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            p = row["path"]
            if p in target_paths:
                issues_str = row.get("issues", "")
                audit[p] = [x.strip() for x in issues_str.split(",") if x.strip()]
    return audit


def _load_metadata(path: str, target_paths: list[str]) -> tuple[dict, list[str]]:
    """Load existing_metadata.json, return matched metadata and skipped paths."""
    with open(path, "r", encoding="utf-8") as f:
        all_meta = json.load(f)
    metadata, skipped = {}, []
    for p in target_paths:
        if p in all_meta:
            metadata[p] = all_meta[p]
        else:
            skipped.append(p)
    return metadata, skipped


def _load_zero_click_queries(pattern: str, target_paths: set[str]) -> dict:
    """Load latest zero-click CSV, return path -> top 5 queries."""
    zc_files = sorted(glob.glob(pattern))
    query_data: dict[str, list] = {}
    if not zc_files:
        return query_data
    latest = zc_files[-1]
    with open(latest, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            page = row.get("路径", "")
            if page.startswith("http"):
                page = urlparse(page).path
            if page in target_paths:
                if page not in query_data:
                    query_data[page] = []
                query_data[page].append({
                    "query": row.get("查询词", ""),
                    "impressions": int(float(row.get("展示", 0))),
                })
    for p in query_data:
        query_data[p] = sorted(
            query_data[p], key=lambda x: x["impressions"], reverse=True
        )[:5]
    return query_data


def _build_contexts(pages, audit, metadata, query_data, skipped):
    """Build rewrite context for each target page."""
    contexts = []
    for p in pages:
        path = p["路径"]
        if path in skipped:
            continue
        meta = metadata.get(path, {})
        contexts.append({
            "path": path,
            "current_title": meta.get("title", ""),
            "current_description": meta.get("meta_description", ""),
            "current_keywords": meta.get("meta_keywords", ""),
            "issues": audit.get(path, []),
            "top_queries": query_data.get(path, []),
            "page_type": p.get("seo_page_type", ""),
            "language": p.get("language", ""),
            "avg_position": float(p.get("平均排名", 0)),
        })
    return contexts


def _prepare_contexts(config: dict, output_dir: Path) -> str:
    """Run full data preparation, write batch files. Return tmp_dir path."""
    opt_config = config.get("optimize", {})
    seo_dir = str(output_dir / "seo")
    tmp_dir = os.path.join(seo_dir, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    # Parse range
    range_str = opt_config.get("range")
    top = opt_config.get("top", 30)
    start, end = _parse_range(range_str, top)
    batch_size = opt_config.get("batch_size", 10)

    # Clean old batch input files (preserve _result files for resume)
    for old in glob.glob(os.path.join(tmp_dir, "seo_batch_*.json")):
        if "_result" not in old:
            os.remove(old)

    # Load data
    pages = _load_priority_ranked(f"{seo_dir}/priority_ranked.csv", start, end)
    if not pages:
        raise RuntimeError("没有找到目标页面，请检查范围参数")
    if len(pages) < (end - start):
        print(f"  警告: 请求 {end - start} 个页面，实际只有 {len(pages)} 个")

    paths = [p["路径"] for p in pages]
    target_set = set(paths)
    print(f"  目标页面数: {len(pages)}")

    audit = _load_audit_report(f"{seo_dir}/audit_report.csv", target_set)
    metadata, skipped = _load_metadata(f"{seo_dir}/existing_metadata.json", paths)
    gsc_dir = str(output_dir / "gsc")
    query_data = _load_zero_click_queries(
        f"{gsc_dir}/query_page_zero_click_*.csv", target_set
    )

    print(
        f"  审计数据匹配: {len(audit)} 页, "
        f"元数据匹配: {len(metadata)} 页, "
        f"查询词匹配: {len(query_data)} 页"
    )
    if skipped:
        print(f"  跳过 (元数据缺失): {skipped}")

    # Build contexts and write batches
    contexts = _build_contexts(pages, audit, metadata, query_data, skipped)
    num_batches = (len(contexts) + batch_size - 1) // batch_size
    for i in range(num_batches):
        batch = contexts[i * batch_size : (i + 1) * batch_size]
        batch_path = os.path.join(tmp_dir, f"seo_batch_{i}.json")
        with open(batch_path, "w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False, indent=2)
        print(f"    Batch {i}: {len(batch)} 页")

    # Write full contexts and original metadata
    with open(
        os.path.join(tmp_dir, "seo_rewrite_contexts.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(contexts, f, ensure_ascii=False, indent=2)
    original_meta = {
        c["path"]: metadata[c["path"]] for c in contexts if c["path"] in metadata
    }
    with open(
        os.path.join(tmp_dir, "seo_original_metadata.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(original_meta, f, ensure_ascii=False, indent=2)

    print(f"  上下文准备完成: {len(contexts)} 页, {num_batches} 批")
    return tmp_dir


# ---------------------------------------------------------------------------
# 2. LLM API calls
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict:
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


def _load_prompt_template(template_path: str) -> str:
    """Load prompt template."""
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read().rstrip()


async def _rewrite_one_batch(
    batch_path: str, result_path: str, prompt_template: str, config: dict
):
    """Process one batch: read contexts, call LLM, save result."""
    with open(batch_path, "r", encoding="utf-8") as f:
        pages = json.load(f)

    prompt = prompt_template + "\n\n" + json.dumps(pages, ensure_ascii=False)
    opt = config.get("optimize", {})

    response = await litellm.acompletion(
        model=opt["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=opt.get("temperature", 0.3),
        max_tokens=opt.get("max_tokens", 4096),
        timeout=opt.get("timeout", 120),
        api_base=os.environ.get("LITELLM_PROXY_API_BASE"),
        api_key=os.environ.get("LITELLM_PROXY_API_KEY"),
    )

    text = response.choices[0].message.content
    result = _extract_json(text)

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


async def _rewrite_batches(
    tmp_dir: str, prompt_template: str, config: dict
) -> list[tuple]:
    """Process all batches with concurrency control and resume support."""
    opt = config.get("optimize", {})
    sem = asyncio.Semaphore(opt.get("concurrency", 3))
    batch_files = sorted(glob.glob(os.path.join(tmp_dir, "seo_batch_*.json")))
    batch_files = [f for f in batch_files if "_result" not in f]

    async def worker(batch_path):
        m = re.search(r"seo_batch_(\d+)\.json", os.path.basename(batch_path))
        i = m.group(1)
        result_path = os.path.join(tmp_dir, f"seo_batch_{i}_result.json")

        # Resume: skip if valid result exists
        if os.path.exists(result_path):
            try:
                with open(result_path, "r", encoding="utf-8") as f:
                    json.load(f)
                print(f"    Batch {i}: skipped (result exists)")
                return (batch_path, True, None)
            except (json.JSONDecodeError, Exception):
                pass

        async with sem:
            max_retries = opt.get("max_retries", 2)
            for attempt in range(max_retries + 1):
                try:
                    await _rewrite_one_batch(
                        batch_path, result_path, prompt_template, config
                    )
                    print(f"    Batch {i}: done")
                    return (batch_path, True, None)
                except Exception as e:
                    if attempt < max_retries:
                        print(
                            f"    Batch {i}: retry {attempt + 1}/{max_retries} ({e})"
                        )
                        await asyncio.sleep(2**attempt)  # exponential backoff
                    else:
                        print(f"    Batch {i}: FAILED ({e})")
                        return (batch_path, False, str(e))

    results = await asyncio.gather(*[worker(f) for f in batch_files])
    return results


# ---------------------------------------------------------------------------
# 3. Merge results
# Inlined from .claude/skills/seo-optimize/scripts/merge_results.py (2026-03-16)
# ---------------------------------------------------------------------------


def _merge_results(tmp_dir: str) -> tuple[dict, list]:
    """Merge all batch result files into one dict. Return (merged, errors)."""
    pattern = os.path.join(tmp_dir, "seo_batch_*_result.json")
    result_files = sorted(glob.glob(pattern))

    merged = {}
    errors = []
    for fpath in result_files:
        batch_name = os.path.basename(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                errors.append((batch_name, "JSON 顶层不是 dict"))
                continue
            merged.update(data)
            print(f"    {batch_name}: {len(data)} 页")
        except json.JSONDecodeError as e:
            errors.append((batch_name, f"JSON 解析失败: {e}"))
        except Exception as e:
            errors.append((batch_name, str(e)))

    output_path = os.path.join(tmp_dir, "seo_rewritten.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"  合并完成: {len(merged)} 页")
    if errors:
        for name, err in errors:
            print(f"    警告: {name}: {err}")

    return merged, errors


# ---------------------------------------------------------------------------
# 4. Post-processing
# Inlined from .claude/skills/seo-optimize/scripts/postprocess.py (2026-03-16)
# ---------------------------------------------------------------------------

TODAY = date.today().isoformat()


def _smart_truncate(text: str, max_len: int, lang: str = "en") -> str:
    """Truncate at word/sentence boundary."""
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    if lang == "en":
        for sep in [". ", ", ", " — ", " - ", "; ", " "]:
            idx = truncated.rfind(sep)
            if idx > max_len * 0.6:
                return truncated[:idx].rstrip(".,;: ")
    else:
        for sep in ["。", "，", "；", "、", " "]:
            idx = truncated.rfind(sep)
            if idx > max_len * 0.6:
                return truncated[:idx].rstrip("。，；、 ")
    return truncated.rstrip()


def _ensure_brand_suffix(
    title: str, lang: str, brand_suffix: str, title_max: int
) -> str:
    """Ensure title ends with brand suffix and fits within max length."""
    if not title.endswith(brand_suffix):
        content = title.replace(brand_suffix, "").strip()
        title = content + brand_suffix
    if len(title) > title_max:
        max_content = title_max - len(brand_suffix)
        content = title.replace(brand_suffix, "").strip()
        content = _smart_truncate(content, max_content, lang)
        title = content + brand_suffix
    return title


def _enhance_schema(schema_list, headline, desc, path, page_type, rewrite=None):
    """Enhance Schema.org structured data with LLM-generated semantic fields."""
    if rewrite is None:
        rewrite = {}
    if not schema_list:
        schema_list = [{"@context": "https://schema.org", "@type": "Article"}]

    term_name = rewrite.get("schema_term_name", headline)
    subject = rewrite.get("schema_subject", "Science")
    course_name = rewrite.get("schema_course_name")

    for schema in schema_list:
        schema["headline"] = headline
        schema["description"] = desc
        if "datePublished" not in schema:
            schema["datePublished"] = "2024-01-15"
        schema["dateModified"] = TODAY

        if page_type == "course_article":
            types = schema.get("@type", "Article")
            if isinstance(types, str):
                types = [types]
            if "LearningResource" not in types:
                types.append("LearningResource")
            schema["@type"] = types
            if "educationalLevel" not in schema:
                if "graduate" in path.lower().split("-")[0]:
                    schema["educationalLevel"] = "Graduate"
                else:
                    schema["educationalLevel"] = "Undergraduate"
            if course_name:
                schema["isPartOf"] = {"@type": "Course", "name": course_name}
            schema["about"] = {
                "@type": "DefinedTerm",
                "name": term_name,
                "inDefinedTermSet": subject,
            }
        elif page_type == "keyword":
            schema["about"] = {
                "@type": "DefinedTerm",
                "name": term_name,
                "inDefinedTermSet": subject,
            }

    return schema_list


def _postprocess_page(path, rewrite, orig, ctx, seo_config):
    """Post-process a single page. Returns (optimized_metadata, stats)."""
    base_url = seo_config.get("base_url", "https://www.bohrium.com")
    brand_suffix = seo_config.get("brand_suffix", " | SciencePedia")
    title_max = seo_config.get("max_title_length", 60)
    desc_max = seo_config.get("max_desc_length", 155)

    lang = ctx.get("language", "en")
    page_type = ctx.get("page_type", "")
    top_queries = ctx.get("top_queries", [])

    title = rewrite["title"]
    desc = rewrite["meta_description"]
    stats = {"title_truncated": False, "desc_truncated": False}

    title = _ensure_brand_suffix(title, lang, brand_suffix, title_max)
    if len(title) > title_max:
        stats["title_truncated"] = True

    if len(desc) > desc_max:
        desc = _smart_truncate(desc, desc_max, lang)
        stats["desc_truncated"] = True

    opt = copy.deepcopy(orig)
    opt["title"] = title
    opt["meta_description"] = desc
    opt["og_title"] = title
    opt["og_description"] = desc
    opt["og_url"] = base_url + path
    opt["og_type"] = "article"
    opt["twitter_title"] = title
    opt["twitter_description"] = desc

    if rewrite.get("meta_keywords"):
        opt["meta_keywords"] = rewrite["meta_keywords"]
    elif top_queries:
        opt["meta_keywords"] = ",".join(q["query"] for q in top_queries[:5])

    headline = title.replace(brand_suffix, "").strip()
    opt["schema_json_ld"] = _enhance_schema(
        opt.get("schema_json_ld", []), headline, desc, path, page_type, rewrite
    )
    return opt, stats


def _merge_with_existing(output_path: str, new_data: dict) -> dict:
    """Incremental merge: if output file exists, merge new data into it."""
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing.update(new_data)
        return existing
    return new_data


def _postprocess_all(rewritten, tmp_dir, seo_config, output_dir):
    """Run post-processing on all rewritten pages. Return (output_files, summary)."""
    with open(
        os.path.join(tmp_dir, "seo_rewrite_contexts.json"), "r", encoding="utf-8"
    ) as f:
        contexts = json.load(f)
    with open(
        os.path.join(tmp_dir, "seo_original_metadata.json"), "r", encoding="utf-8"
    ) as f:
        original_metadata = json.load(f)

    ctx_lookup = {c["path"]: c for c in contexts}

    total = 0
    skipped = []
    issues_fixed = {}
    title_truncated_count = 0
    desc_truncated_count = 0
    optimized = {}
    original_backup = {}

    for path, rw in rewritten.items():
        if path not in original_metadata:
            skipped.append(path)
            continue
        if "title" not in rw or "meta_description" not in rw:
            skipped.append(path)
            print(f"    警告: {path} 缺少 title/meta_description，跳过")
            continue
        total += 1
        orig = original_metadata[path]
        ctx = ctx_lookup.get(path, {})

        opt, stats = _postprocess_page(path, rw, orig, ctx, seo_config)

        if stats["title_truncated"]:
            title_truncated_count += 1
        if stats["desc_truncated"]:
            desc_truncated_count += 1
        for issue in ctx.get("issues", []):
            issues_fixed[issue] = issues_fixed.get(issue, 0) + 1

        optimized[path] = opt
        original_backup[path] = orig

    # Write output
    seo_out = os.path.join(str(output_dir), "seo")
    os.makedirs(seo_out, exist_ok=True)

    optimized_path = os.path.join(seo_out, "optimized_metadata.json")
    merged = _merge_with_existing(optimized_path, optimized)
    with open(optimized_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    backup_path = os.path.join(seo_out, "original_metadata_backup.json")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(original_backup, f, ensure_ascii=False, indent=2)

    # Print summary
    print(f"  处理页数: {total}, 跳过: {len(skipped)}")
    if issues_fixed:
        print(f"  问题修复: {issues_fixed}")
    print(f"  截断: title={title_truncated_count}, desc={desc_truncated_count}")

    return (
        [optimized_path, backup_path],
        {
            "pages_processed": total,
            "pages_skipped": len(skipped),
            "title_truncated": title_truncated_count,
            "desc_truncated": desc_truncated_count,
            "issues_fixed": issues_fixed,
            "output_total": len(merged),
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(config: dict, output_dir: Path) -> dict:
    """Run the optimize step: prepare → API rewrite → merge → postprocess."""
    # Credential check — graceful skip if missing
    if not os.environ.get("LITELLM_PROXY_API_BASE") or not os.environ.get(
        "LITELLM_PROXY_API_KEY"
    ):
        print("  [optimize] 跳过: 未设置 LITELLM_PROXY_API_BASE / LITELLM_PROXY_API_KEY")
        print("  参考 .env.example 配置 LLM API 凭据")
        return {
            "output_files": [],
            "summary": {"skipped": True, "reason": "missing credentials"},
        }

    opt_config = config.get("optimize", {})
    seo_config = config.get("seo", {})

    # Check prerequisites
    seo_dir = output_dir / "seo"
    required = ["priority_ranked.csv", "existing_metadata.json", "audit_report.csv"]
    missing = [f for f in required if not (seo_dir / f).exists()]
    if missing:
        print(f"  错误: 缺少前置文件: {missing}")
        print("  请先运行: uv run python main.py all --skip fetch")
        return {
            "output_files": [],
            "summary": {"skipped": True, "reason": f"missing files: {missing}"},
        }

    # 1. Prepare contexts
    print("  [1/4] 准备重写上下文...")
    tmp_dir = _prepare_contexts(config, output_dir)

    # 2. Load prompt and call API
    print("  [2/4] 调用 LLM API 重写...")
    template_path = opt_config.get(
        "prompt_template", ".claude/skills/seo-optimize/templates/rewrite-prompt.md"
    )
    prompt_template = _load_prompt_template(template_path)
    api_results = asyncio.run(_rewrite_batches(tmp_dir, prompt_template, config))

    succeeded = sum(1 for _, ok, _ in api_results if ok)
    failed = sum(1 for _, ok, _ in api_results if not ok)
    print(f"  API 完成: {succeeded} 成功, {failed} 失败")

    if succeeded == 0:
        print("  错误: 所有 batch 均失败")
        return {
            "output_files": [],
            "summary": {"batches_succeeded": 0, "batches_failed": failed},
        }

    # 3. Merge results
    print("  [3/4] 合并重写结果...")
    rewritten, merge_errors = _merge_results(tmp_dir)

    if not rewritten:
        print("  错误: 合并后无有效结果")
        return {
            "output_files": [],
            "summary": {
                "batches_succeeded": succeeded,
                "batches_failed": failed,
                "merge_errors": len(merge_errors),
            },
        }

    # 4. Post-process
    print("  [4/4] 后处理...")
    output_files, pp_summary = _postprocess_all(
        rewritten, tmp_dir, seo_config, output_dir
    )

    return {
        "output_files": output_files,
        "summary": {
            "batches_succeeded": succeeded,
            "batches_failed": failed,
            **pp_summary,
        },
    }
