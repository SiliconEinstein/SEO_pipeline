"""
Step 2: SEO Priority Ranking

Takes GSC data (zero-click CSV + ranking CSV) from output/gsc/,
classifies page subtypes, computes priority scores, and produces
a ranked list of pages for SEO optimization.

Filtering is driven by ``seo.include_subtypes`` in config.yaml.
Run the **analyze** step first to discover available subtypes.

Output: output/seo/priority_ranked.csv
"""

import logging
import re
from pathlib import Path

import pandas as pd

from steps._classify import discover_subtypes, find_latest_csv, get_filter_tag

logger = logging.getLogger(__name__)


# ── Utility functions ────────────────────────────────────────────────


def classify_page_type(subtype: str, subtype_page_types: dict[str, str]) -> str:
    """Map a subtype label to a page_type using the config-driven mapping.

    Args:
        subtype: Auto-discovered subtype label (e.g. "feynman", "feynman/keyword").
        subtype_page_types: Mapping from ``seo.subtype_page_types`` in config.yaml.

    Returns: Page type string (e.g. 'course_article', 'keyword') or 'other'.
    """
    return subtype_page_types.get(subtype, "other")


def detect_language(path: str) -> str:
    """Detect language from the URL path prefix."""
    return "en" if path.startswith("/en/") else "zh"


# ── Data loading ─────────────────────────────────────────────────────


def load_and_merge_data(gsc_dir: Path, filter_tag: str = "all") -> pd.DataFrame:
    """Load zero-click and ranking CSVs from *gsc_dir* and merge them.

    Returns a page-level DataFrame with columns from the ranking CSV
    plus ``top_queries`` (list[dict]) and ``query_count`` (int).

    If the zero-click CSV is missing (no zero-click pages found by fetch),
    ranking data is returned with empty query columns.
    """
    ranking_path = find_latest_csv(gsc_dir, f"ranking_pages_{filter_tag}_*.csv")
    logger.info("Loading ranking data from %s", ranking_path)
    ranking = pd.read_csv(ranking_path, encoding="utf-8-sig")

    # Zero-click CSV is optional — fetch skips it when no zero-click pages exist
    try:
        zero_click_path = find_latest_csv(gsc_dir, f"query_page_zero_click_{filter_tag}_*.csv")
    except FileNotFoundError:
        logger.warning("零点击 CSV 不存在，跳过查询词合并")
        ranking["top_queries"] = [[] for _ in range(len(ranking))]
        ranking["query_count"] = 0
        return ranking

    # Validate date consistency between the two CSVs
    zc_date = re.search(r"\d{4}-\d{2}-\d{2}", zero_click_path.name)
    rk_date = re.search(r"\d{4}-\d{2}-\d{2}", ranking_path.name)
    if zc_date and rk_date and zc_date.group() != rk_date.group():
        raise ValueError(
            f"Date mismatch between GSC files: "
            f"{zero_click_path.name} ({zc_date.group()}) vs "
            f"{ranking_path.name} ({rk_date.group()}). "
            f"Re-run 'main.py fetch' to generate consistent data."
        )

    logger.info("Loading zero-click data from %s", zero_click_path)
    zero_click = pd.read_csv(zero_click_path, encoding="utf-8-sig")
    if zero_click.empty:
        logger.warning("零点击 CSV 为空，跳过查询词合并")
        ranking["top_queries"] = [[] for _ in range(len(ranking))]
        ranking["query_count"] = 0
        return ranking

    # Build per-page query lists from zero-click data
    queries_by_page = (
        zero_click.groupby("路径")
        .apply(
            lambda g: g.nlargest(10, "展示")[["查询词", "展示", "排名"]].to_dict("records"),
            include_groups=False,
        )
        .reset_index()
        .rename(columns={0: "top_queries"})
    )

    query_counts = (
        zero_click.groupby("路径")["查询词"]
        .nunique()
        .reset_index()
        .rename(columns={"查询词": "query_count"})
    )

    # Merge onto ranking data
    merged = ranking.merge(queries_by_page, on="路径", how="left")
    merged = merged.merge(query_counts, on="路径", how="left")

    # Fill pages that have no zero-click queries
    merged["top_queries"] = merged["top_queries"].apply(
        lambda x: x if isinstance(x, list) else []
    )
    merged["query_count"] = merged["query_count"].fillna(0).astype(int)

    return merged


# ── Scoring ──────────────────────────────────────────────────────────


def compute_priority_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Compute priority scores using the Opportunity Score formula.

    Formula:
        priority_score = impressions × (1 - CTR)

    This is the standard SEO "opportunity score": pages with high
    impressions but low click-through rate represent the biggest
    untapped potential.  For zero-click pages (CTR = 0) this
    simplifies to sorting by impressions alone.
    """
    df = df.copy()
    df["priority_score"] = df["展示"] * (1 - df["CTR"])
    return df


# ── Filtering & ranking ─────────────────────────────────────────────


def filter_and_rank(
    df: pd.DataFrame,
    include_subtypes: list[str] | None = None,
    subtype_page_types: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Classify pages, filter by config-driven subtypes, score, and sort.

    Args:
        df: Merged page-level DataFrame from :func:`load_and_merge_data`.
        include_subtypes: Subtype labels to keep (from ``seo.include_subtypes``).
            Empty list or ``None`` means keep all subtypes.
        subtype_page_types: Mapping from subtype to page_type
            (from ``seo.subtype_page_types``).
    """
    if subtype_page_types is None:
        subtype_page_types = {}
    df = df.copy()
    # Auto-discover subtypes from URL paths (same algorithm as analyze step)
    df["subtype"] = discover_subtypes(df["路径"])
    # Map subtype → seo_page_type via config (for Schema.org enhancement in optimize)
    df["seo_page_type"] = df["subtype"].apply(
        lambda s: classify_page_type(s, subtype_page_types)
    )
    df["language"] = df["路径"].apply(detect_language)

    # Build filter mask — only require query_count > 0 when zero-click data exists
    has_any_queries = df["query_count"].sum() > 0
    if has_any_queries:
        mask = df["query_count"] > 0
    else:
        mask = pd.Series(True, index=df.index)
    if include_subtypes:
        mask = mask & df["subtype"].isin(include_subtypes)
        logger.info("Filtering to subtypes: %s", include_subtypes)

    filtered = df[mask].copy()

    filtered = compute_priority_scores(filtered)
    filtered = filtered.sort_values("priority_score", ascending=False).reset_index(drop=True)

    return filtered


# ── Step entry point ─────────────────────────────────────────────────

SAVE_COLUMNS = [
    "路径",
    "subtype",
    "seo_page_type",
    "language",
    "priority_score",
    "展示",
    "CTR",
    "平均排名",
    "优先级",
    "query_count",
]


def run(config: dict, output_dir: Path) -> dict:
    """Execute the ranking step.

    Args:
        config: Parsed config.yaml contents.  Relevant keys:
                 ``seo.include_subtypes`` — list of subtype labels to keep.
        output_dir: Root output directory (e.g. ``Path("output")``).

    Returns:
        A dict with ``output_files`` (list[Path]) and ``summary`` (dict).
    """
    gsc_dir = output_dir / "gsc"
    seo_dir = output_dir / "seo"
    seo_dir.mkdir(parents=True, exist_ok=True)

    seo_cfg = config.get("seo", {})
    include_subtypes = seo_cfg.get("include_subtypes", [])
    subtype_page_types = seo_cfg.get("subtype_page_types", {})

    # 1. Load and merge GSC data
    tag = get_filter_tag(config)
    logger.info("Loading GSC data from %s", gsc_dir)
    data = load_and_merge_data(gsc_dir, filter_tag=tag)
    logger.info("Loaded %d pages from ranking data", len(data))

    # 2-4. Classify, score, filter, rank
    ranked = filter_and_rank(
        data,
        include_subtypes=include_subtypes,
        subtype_page_types=subtype_page_types,
    )
    logger.info("After filtering: %d actionable pages", len(ranked))

    # 5. Save output
    out_path = seo_dir / "priority_ranked.csv"
    ranked[SAVE_COLUMNS].to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info("Priority ranking saved to %s", out_path)

    # Build summary
    type_stats = (
        ranked.groupby("subtype")
        .agg(
            count=("路径", "count"),
            avg_score=("priority_score", "mean"),
            total_impressions=("展示", "sum"),
        )
        .to_dict("index")
    )
    lang_stats = ranked.groupby("language")["路径"].count().to_dict()

    summary = {
        "total_pages": len(data),
        "ranked_pages": len(ranked),
        "type_stats": type_stats,
        "lang_stats": lang_stats,
        "top_score": float(ranked["priority_score"].max()) if len(ranked) > 0 else 0.0,
    }

    return {
        "output_files": [out_path],
        "summary": summary,
    }
