"""
Step 2: SEO Priority Ranking

Takes GSC data (zero-click CSV + ranking CSV) from output/gsc/,
classifies page types, computes priority scores, and produces
a ranked list of pages for SEO optimization.

Output: output/seo/priority_ranked.csv
"""

import glob
import logging
import re
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


# ── Utility functions ────────────────────────────────────────────────


def classify_sciencepedia_type(path: str) -> str:
    """Classify a sciencepedia URL path into a page type.

    Returns: 'course_article', 'keyword', 'agent_tool', or 'other'.
    """
    clean = re.sub(r"^/en/", "/", path)

    if "/agent-tools/" in clean:
        return "agent_tool"
    if "/feynman/keyword/" in clean:
        return "keyword"
    if "/feynman/" in clean:
        return "course_article"
    return "other"


def detect_language(path: str) -> str:
    """Detect language from the URL path prefix."""
    return "en" if path.startswith("/en/") else "zh"


# ── Data loading ─────────────────────────────────────────────────────


def _find_latest_csv(directory: Path, pattern: str) -> Path:
    """Find the most recent CSV matching *pattern* inside *directory*.

    Files are assumed to contain a date component in the name; the
    lexicographically last match is treated as the newest file.
    """
    matches = sorted(glob.glob(str(directory / pattern)))
    if not matches:
        raise FileNotFoundError(
            f"No CSV files matching '{pattern}' found in {directory}"
        )
    return Path(matches[-1])


def load_and_merge_data(gsc_dir: Path) -> pd.DataFrame:
    """Load zero-click and ranking CSVs from *gsc_dir* and merge them.

    Returns a page-level DataFrame with columns from the ranking CSV
    plus ``top_queries`` (list[dict]) and ``query_count`` (int).
    """
    zero_click_path = _find_latest_csv(gsc_dir, "query_page_zero_click_*.csv")
    ranking_path = _find_latest_csv(gsc_dir, "ranking_pages_*.csv")

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
    logger.info("Loading ranking data from %s", ranking_path)

    ranking = pd.read_csv(ranking_path, encoding="utf-8-sig")
    zero_click = pd.read_csv(zero_click_path, encoding="utf-8-sig")

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


def filter_and_rank(df: pd.DataFrame) -> pd.DataFrame:
    """Classify pages, filter to actionable types, score, and sort.

    Keeps: course_article, keyword (with query_count > 0).
    Excludes: agent_tool, other.
    """
    df = df.copy()
    df["seo_page_type"] = df["路径"].apply(classify_sciencepedia_type)
    df["language"] = df["路径"].apply(detect_language)

    mask = (
        df["seo_page_type"].isin(["course_article", "keyword"])
        & (df["query_count"] > 0)
    )
    filtered = df[mask].copy()

    filtered = compute_priority_scores(filtered)
    filtered = filtered.sort_values("priority_score", ascending=False).reset_index(drop=True)

    return filtered


# ── Step entry point ─────────────────────────────────────────────────

SAVE_COLUMNS = [
    "路径",
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
        config: Parsed config.yaml contents.  Relevant keys live under
                 ``seo.page_filter`` and ``seo.exclude_patterns``.
        output_dir: Root output directory (e.g. ``Path("output")``).

    Returns:
        A dict with ``output_files`` (list[Path]) and ``summary`` (dict).
    """
    gsc_dir = output_dir / "gsc"
    seo_dir = output_dir / "seo"
    seo_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load and merge GSC data
    logger.info("Loading GSC data from %s", gsc_dir)
    data = load_and_merge_data(gsc_dir)
    logger.info("Loaded %d pages from ranking data", len(data))

    # 2-4. Classify, score, filter, rank
    ranked = filter_and_rank(data)
    logger.info("After filtering: %d actionable pages", len(ranked))

    # 5. Save output
    out_path = seo_dir / "priority_ranked.csv"
    ranked[SAVE_COLUMNS].to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info("Priority ranking saved to %s", out_path)

    # Build summary
    type_stats = (
        ranked.groupby("seo_page_type")
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
