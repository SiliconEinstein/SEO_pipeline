"""
Step 4 — SEO Metadata Quality Audit

Audits existing SEO metadata and generates a per-page issue report
plus an aggregated summary.  No auto-fix suggestions are produced;
the downstream LLM rewrite step handles corrections.

Detection rules
    1. desc_too_long         description > max_desc_length (default 155)
    2. title_too_long        title > max_title_length (default 60)
    3. generic_opening       description starts with a generic verb
    4. language_mismatch     Chinese path but zero Chinese characters
    5. missing_keywords      Top-3 query terms not covered (60 % word match)
    6. schema issues         Article missing dates / course_article missing
                             LearningResource
"""

from __future__ import annotations

import csv
import json
import logging
import re
from collections import Counter
from collections import defaultdict
from pathlib import Path

from steps._classify import get_filter_tag

logger = logging.getLogger(__name__)

# ── generic openers shared by both Chinese and English pages ──────────────
GENERIC_OPENERS: list[str] = [
    "Explore ", "Learn ", "Discover ", "Master ", "Understand ",
    "Dive into ", "Uncover ", "Study ", "Examine ",
    "探索", "学习", "了解", "掌握", "深入",
]

TOP_K_QUERIES = 3
WORD_MATCH_THRESHOLD = 0.6


# ══════════════════════════════════════════════════════════════════════════
# Data loaders
# ══════════════════════════════════════════════════════════════════════════

def _load_metadata(seo_dir: Path) -> dict:
    """Load existing_metadata.json → {path: {title, meta_description, …}}."""
    path = seo_dir / "existing_metadata.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_query_data(gsc_dir: Path, filter_tag: str = "all") -> dict[str, list[dict]]:
    """Find the most recent query_page_zero_click CSV in *gsc_dir* and
    return ``{path: [{query, impressions}, …]}`` sorted by impressions desc.
    """
    candidates = sorted(gsc_dir.glob(f"query_page_zero_click_{filter_tag}_*.csv"))
    if not candidates:
        logger.warning("No query_page_zero_click CSV found — skipping keyword checks")
        return {}
    csv_path = candidates[-1]  # most recent by filename (date-stamped)
    logger.info("Using query CSV: %s", csv_path.name)

    page_queries: dict[str, list[dict]] = defaultdict(list)
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            page_queries[row["路径"]].append({
                "query": row["查询词"],
                "impressions": int(row["展示"]),
            })
    for path in page_queries:
        page_queries[path].sort(key=lambda x: x["impressions"], reverse=True)
    return dict(page_queries)


def _load_priority_ranked(seo_dir: Path) -> tuple[dict[str, int], dict[str, str]]:
    """Load priority_ranked.csv → (ranks, page_types).

    Returns:
        ranks: {path: 1-indexed rank}
        page_types: {path: seo_page_type}
    """
    csv_path = seo_dir / "priority_ranked.csv"
    ranks: dict[str, int] = {}
    page_types: dict[str, str] = {}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            path = row["路径"]
            ranks[path] = i
            page_types[path] = row.get("seo_page_type", "other")
    return ranks, page_types


# ══════════════════════════════════════════════════════════════════════════
# Detection rules
# ══════════════════════════════════════════════════════════════════════════

def _check_generic_opening(desc: str) -> str | None:
    """Return the matched opener string, or None."""
    for opener in GENERIC_OPENERS:
        if desc.startswith(opener):
            return opener.strip()
    return None


def _check_language_mismatch(path: str, title: str, desc: str) -> bool:
    """True if the path is Chinese (not /en/) but title+desc contain zero
    Chinese characters."""
    if path.startswith("/en/"):
        return False
    cn_title = len(re.findall(r"[\u4e00-\u9fff]", title))
    cn_desc = len(re.findall(r"[\u4e00-\u9fff]", desc))
    return cn_title == 0 and cn_desc == 0


def _check_keyword_coverage(
    title: str,
    desc: str,
    top_queries: list[dict],
    top_k: int = TOP_K_QUERIES,
) -> list[str]:
    """Return query strings from the top-k that are NOT covered
    (< 60 % word-level match) in title + description.

    meta_keywords is intentionally excluded — Google has not used it
    as a ranking signal since 2009.
    """
    if not top_queries:
        return []
    text = (title + " " + desc).lower()
    missing: list[str] = []
    for q in top_queries[:top_k]:
        query = q["query"].lower().strip()
        words = query.split()
        if not words:
            continue
        matched = sum(1 for w in words if w in text)
        if matched / len(words) < WORD_MATCH_THRESHOLD:
            missing.append(q["query"])
    return missing


def _check_schema_completeness(schemas: list[dict], page_type: str) -> list[str]:
    """Return a list of schema issue labels.

    Args:
        schemas: JSON-LD schema objects from the page.
        page_type: From ``seo_page_type`` column in priority_ranked.csv
                   (config-driven, e.g. 'course_article', 'keyword', 'other').
    """
    issues: list[str] = []
    if not schemas:
        issues.append("no_schema")
        return issues

    has_learning_resource = False
    for s in schemas:
        stype = s.get("@type", "")

        # Article-level date checks
        if stype == "Article" or (isinstance(stype, list) and "Article" in stype):
            if "datePublished" not in s:
                issues.append("missing_datePublished")
            if "dateModified" not in s:
                issues.append("missing_dateModified")

        # LearningResource detection
        if isinstance(stype, list):
            if "LearningResource" in stype:
                has_learning_resource = True
        elif stype in ("LearningResource", "Course"):
            has_learning_resource = True

    # course_article pages should carry LearningResource
    if page_type == "course_article" and not has_learning_resource:
        issues.append("course_missing_LearningResource")

    return issues


# ══════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════

def run(config: dict, output_dir: Path) -> dict:
    """Run the SEO metadata quality audit.

    Args:
        config: parsed config.yaml; relevant keys live under ``seo.*``.
        output_dir: root output directory (Path).

    Returns:
        ``{"output_files": list[Path], "summary": dict}``
    """
    seo_cfg = config.get("seo", {})
    max_title_len: int = seo_cfg.get("max_title_length", 60)
    max_desc_len: int = seo_cfg.get("max_desc_length", 155)

    seo_dir = output_dir / "seo"
    gsc_dir = output_dir / "gsc"

    # ── load data ──────────────────────────────────────────────────────
    logger.info("Loading data for audit …")
    metadata = _load_metadata(seo_dir)
    tag = get_filter_tag(config)
    query_data = _load_query_data(gsc_dir, filter_tag=tag)
    priority_ranks, page_types = _load_priority_ranked(seo_dir)

    total = len(metadata)
    logger.info("Metadata pages: %d", total)
    logger.info("Query data: %d pages with queries", len(query_data))
    logger.info("Priority ranked: %d pages", len(priority_ranks))

    # ── per-page audit ─────────────────────────────────────────────────
    issue_counts: Counter[str] = Counter()
    pages_with_issues = 0
    report_rows: list[dict] = []

    logger.info("Running 6 detection rules …")

    for path, meta in metadata.items():
        title = meta.get("title", "") or ""
        desc = meta.get("meta_description", "") or ""
        schemas = meta.get("schema_json_ld", []) or []
        rank = priority_ranks.get(path, 999)

        issues: list[str] = []
        generic_opener = ""
        missing_kw_str = ""
        schema_issues_str = ""

        # 1. desc_too_long
        desc_len = len(desc)
        if desc_len > max_desc_len:
            issues.append("desc_too_long")
            issue_counts["desc_too_long"] += 1

        # 2. title_too_long
        title_len = len(title)
        if title_len > max_title_len:
            issues.append("title_too_long")
            issue_counts["title_too_long"] += 1

        # 3. generic_opening
        opener = _check_generic_opening(desc)
        if opener:
            issues.append("generic_opening")
            issue_counts["generic_opening"] += 1
            generic_opener = opener

        # 4. language_mismatch
        if _check_language_mismatch(path, title, desc):
            issues.append("language_mismatch")
            issue_counts["language_mismatch"] += 1

        # 5. missing_keywords
        top_queries = query_data.get(path, [])
        missing_kw = _check_keyword_coverage(title, desc, top_queries)
        if missing_kw:
            issues.append("missing_keywords")
            issue_counts["missing_keywords"] += 1
            missing_kw_str = " | ".join(missing_kw)

        # 6. schema issues
        page_type = page_types.get(path, "other")
        schema_issues = _check_schema_completeness(schemas, page_type)
        if schema_issues:
            for si in schema_issues:
                issues.append(f"schema:{si}")
                issue_counts[f"schema:{si}"] += 1
            schema_issues_str = " | ".join(schema_issues)

        if issues:
            pages_with_issues += 1

        report_rows.append({
            "priority_rank": rank,
            "path": path,
            "issues_count": len(issues),
            "issues": ", ".join(issues),
            "title_length": title_len,
            "title_issue": "too_long" if title_len > max_title_len else "ok",
            "original_title": title,
            "desc_length": desc_len,
            "desc_issue": "too_long" if desc_len > max_desc_len else "ok",
            "original_description": desc,
            "generic_opener": generic_opener,
            "language_mismatch": "yes" if "language_mismatch" in issues else "",
            "missing_keywords": missing_kw_str,
            "schema_issues": schema_issues_str,
        })

    # sort by priority rank (lower = higher priority)
    report_rows.sort(key=lambda r: r["priority_rank"])

    # ── write audit_report.csv ─────────────────────────────────────────
    report_csv = seo_dir / "audit_report.csv"
    fieldnames = [
        "priority_rank", "path", "issues_count", "issues",
        "title_length", "title_issue", "original_title",
        "desc_length", "desc_issue", "original_description",
        "generic_opener", "language_mismatch",
        "missing_keywords", "schema_issues",
    ]
    with open(report_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(report_rows)
    logger.info("Audit report saved: %s", report_csv)

    # ── build summary ──────────────────────────────────────────────────
    desc_lengths = [len((m.get("meta_description", "") or "")) for m in metadata.values()]
    title_lengths = [len((m.get("title", "") or "")) for m in metadata.values()]

    summary = {
        "total_pages": total,
        "pages_with_issues": pages_with_issues,
        "pages_clean": total - pages_with_issues,
        "issue_breakdown": dict(Counter(issue_counts).most_common()),
        "desc_stats": {
            "too_long_count": issue_counts["desc_too_long"],
            "too_long_pct": round(issue_counts["desc_too_long"] / total * 100, 1) if total else 0,
            "max_length": max(desc_lengths) if desc_lengths else 0,
            "avg_length": round(sum(desc_lengths) / total, 1) if total else 0,
        },
        "title_stats": {
            "too_long_count": issue_counts["title_too_long"],
            "too_long_pct": round(issue_counts["title_too_long"] / total * 100, 1) if total else 0,
            "max_length": max(title_lengths) if title_lengths else 0,
            "avg_length": round(sum(title_lengths) / total, 1) if total else 0,
        },
    }

    # ── write audit_summary.json ───────────────────────────────────────
    summary_json = seo_dir / "audit_summary.json"
    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    logger.info("Audit summary saved: %s", summary_json)

    # ── console summary ────────────────────────────────────────────────
    print()
    print(f"{'=' * 60}")
    print(f"  SEO Metadata Audit  ({total} pages)")
    print(f"{'=' * 60}")
    print(f"  Pages with issues : {pages_with_issues}/{total}"
          f" ({pages_with_issues / total * 100:.1f}%)" if total else "")
    print(f"  Clean pages       : {total - pages_with_issues}/{total}")
    print()
    print("  Issue breakdown:")
    for issue, count in Counter(issue_counts).most_common():
        print(f"    {issue:40s}  {count:>4}  ({count / total * 100:.1f}%)")
    print()
    print(f"  Description — avg {summary['desc_stats']['avg_length']} chars, "
          f"max {summary['desc_stats']['max_length']} chars, "
          f"{issue_counts['desc_too_long']} too long (>{max_desc_len})")
    print(f"  Title       — avg {summary['title_stats']['avg_length']} chars, "
          f"max {summary['title_stats']['max_length']} chars, "
          f"{issue_counts['title_too_long']} too long (>{max_title_len})")
    print(f"{'=' * 60}")

    return {
        "output_files": [report_csv, summary_json],
        "summary": summary,
    }
