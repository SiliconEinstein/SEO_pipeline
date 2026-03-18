"""
Step: analyze — Site Analysis

Reads fetch output CSVs (ranking_pages + zero-click) and produces an
analysis report grouped by **auto-discovered subtypes** from URL path
structure.  This lets users see the data distribution before deciding
which subtypes to optimize via ``seo.include_subtypes``.

Output: output/analyze/site_analysis.csv, output/analyze/site_analysis.json
"""

import json
import logging
import re
from pathlib import Path

import pandas as pd

from steps._classify import discover_subtypes, find_latest_csv

logger = logging.getLogger(__name__)

GROUP_COL = "子类型"


# ── Data loading ─────────────────────────────────────────────────────


def _extract_date_range(ranking_path: Path) -> str:
    """Try to extract date range from the CSV filename."""
    name = ranking_path.stem
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", name)
    if len(dates) == 2:
        return f"{dates[0]} ~ {dates[1]}"
    if len(dates) == 1:
        return dates[0]
    return "unknown"


# ── Analysis functions ───────────────────────────────────────────────


def _subtype_distribution(ranking: pd.DataFrame) -> pd.DataFrame:
    """Per-subtype: count, impressions, clicks, weighted CTR + std, weighted rank, opportunity."""
    df = ranking.copy()
    df["_rank_x_imp"] = df["平均排名"] * df["展示"]

    grouped = df.groupby(GROUP_COL).agg(
        页面数=("路径", "count"),
        总展示=("展示", "sum"),
        总点击=("点击", "sum"),
        _rank_x_imp_sum=("_rank_x_imp", "sum"),
        CTR_std=("CTR", "std"),
    )
    grouped["加权CTR"] = (grouped["总点击"] / grouped["总展示"]).fillna(0)
    grouped["CTR_std"] = grouped["CTR_std"].fillna(0)
    grouped["机会分"] = (grouped["总展示"] - grouped["总点击"]).astype(int)
    grouped["加权排名"] = (grouped["_rank_x_imp_sum"] / grouped["总展示"]).round(1)
    grouped = grouped.drop(columns=["_rank_x_imp_sum"])
    grouped = grouped.sort_values("机会分", ascending=False).reset_index()
    return grouped


def _zero_click_analysis(
    ranking: pd.DataFrame, zero_click: pd.DataFrame
) -> pd.DataFrame:
    """Per-subtype: zero-click page count, percentage, total zero-click impressions.

    Uses subtype labels from *ranking* (already computed by caller) to ensure
    consistent grouping — do NOT call discover_subtypes separately here.
    """
    # Deduplicate zero-click to page level, get subtype from ranking
    subtype_map = ranking[["路径", GROUP_COL]].drop_duplicates(subset=["路径"])
    zc_pages = zero_click.drop_duplicates(subset=["路径"])[["路径"]].copy()
    zc_pages = zc_pages.merge(subtype_map, on="路径", how="left")
    zc_pages[GROUP_COL] = zc_pages[GROUP_COL].fillna("other")

    # Sum zero-click impressions per page
    if "页面总展示" in zero_click.columns:
        zc_impressions = (
            zero_click.drop_duplicates(subset=["路径"])[["路径", "页面总展示"]]
            .rename(columns={"页面总展示": "零点击总展示"})
        )
    else:
        zc_impressions = (
            zero_click.groupby("路径")["展示"]
            .sum()
            .reset_index()
            .rename(columns={"展示": "零点击总展示"})
        )

    zc_merged = zc_pages.merge(zc_impressions, on="路径", how="left")

    zc_by_type = zc_merged.groupby(GROUP_COL).agg(
        零点击数=("路径", "count"),
        零点击总展示=("零点击总展示", "sum"),
    )

    # Total pages per subtype from ranking; pages not in ranking are 100% zero-click
    total_by_type = ranking.groupby(GROUP_COL)["路径"].count().rename("该类型总数")
    zc_by_type = zc_by_type.join(total_by_type, how="left")
    zc_by_type["该类型总数"] = zc_by_type["该类型总数"].fillna(zc_by_type["零点击数"])
    zc_by_type["占该类型%"] = (zc_by_type["零点击数"] / zc_by_type["该类型总数"] * 100).round(1)
    zc_by_type["零点击总展示"] = zc_by_type["零点击总展示"].astype(int)
    zc_by_type = zc_by_type.sort_values("零点击总展示", ascending=False).reset_index()

    return zc_by_type[[GROUP_COL, "零点击数", "占该类型%", "零点击总展示"]]


def _ranking_segment_distribution(ranking: pd.DataFrame) -> pd.DataFrame:
    """Pivot table: subtype × ranking segment counts."""
    bin_order = ["1-3", "4-10", "11-20", "21-50", "50+"]

    # Map the lower bound of each ranking segment to the short bin label.
    # Robust to any label format as long as it starts with a number (e.g.
    # "1-3 (首页顶部)", "4-5", "6-10 (首页底部)", "11-20", "50+").
    _BOUND_TO_BIN = {1: "1-3", 4: "4-10", 5: "4-10", 6: "4-10",
                     11: "11-20", 21: "21-50", 50: "50+"}

    def _short_bin(seg: str) -> str:
        m = re.match(r"(\d+)", str(seg))
        if m:
            lower = int(m.group(1))
            if lower in _BOUND_TO_BIN:
                return _BOUND_TO_BIN[lower]
        return "50+"

    df = ranking.copy()
    df["排名段_short"] = df["排名段"].apply(_short_bin)

    pivot = pd.crosstab(df[GROUP_COL], df["排名段_short"])
    for col in bin_order:
        if col not in pivot.columns:
            pivot[col] = 0
    pivot = pivot[bin_order].reset_index()
    return pivot


def _opportunity_score_preview(ranking: pd.DataFrame) -> pd.DataFrame:
    """Per-subtype opportunity score = total impressions - total clicks."""
    grouped = (
        ranking.groupby(GROUP_COL)
        .agg(总展示=("展示", "sum"), 总点击=("点击", "sum"), 页面数=("路径", "count"))
        .reset_index()
    )
    grouped["机会分总和"] = (grouped["总展示"] - grouped["总点击"]).astype(int)
    grouped = grouped.drop(columns=["总展示", "总点击"])
    grouped = grouped.sort_values("机会分总和", ascending=False).reset_index(drop=True)
    return grouped


def _language_distribution(ranking: pd.DataFrame) -> dict:
    """Count Chinese vs English pages based on /en/ prefix."""
    en_count = int(ranking["路径"].str.startswith("/en/").sum())
    zh_count = len(ranking) - en_count
    return {"zh": zh_count, "en": en_count}


# ── Console output ───────────────────────────────────────────────────


def _print_report(
    total_pages: int,
    date_range: str,
    type_dist: pd.DataFrame,
    zc_analysis: pd.DataFrame,
    rank_dist: pd.DataFrame,
    lang_dist: dict,
) -> None:
    """Pretty-print the analysis report to console."""
    print(f"\n{'='*60}")
    print(f"  Site Analysis  ({total_pages} pages, {date_range})")
    print(f"{'='*60}")

    # Subtype distribution
    print("\n  子类型分布:")
    print(
        f"    {'子类型':<20s} {'页面数':>6s} {'总展示':>9s} {'总点击':>8s}"
        f" {'加权CTR':>10s} {'CTR_std':>9s} {'加权排名':>8s} {'机会分':>9s}"
    )
    for _, row in type_dist.iterrows():
        ctr_str = f"{row['加权CTR']*100:.2f}%"
        std_str = f"{row['CTR_std']*100:.2f}%"
        print(
            f"    {row[GROUP_COL]:<20s} {row['页面数']:>6d} {row['总展示']:>9d}"
            f" {row['总点击']:>8d} {ctr_str:>10s} {std_str:>9s} {row['加权排名']:>8.1f}"
            f" {row['机会分']:>9d}"
        )

    # Zero-click analysis
    if not zc_analysis.empty:
        print("\n  零点击页面:")
        print(f"    {'子类型':<20s} {'零点击数':>8s} {'占该类型%':>9s} {'零点击总展示':>12s}")
        for _, row in zc_analysis.iterrows():
            print(
                f"    {row[GROUP_COL]:<20s} {row['零点击数']:>8d}"
                f" {row['占该类型%']:>8.1f}% {row['零点击总展示']:>12d}"
            )

    # Ranking segment distribution
    print("\n  排名段分布:")
    seg_cols = [c for c in rank_dist.columns if c != GROUP_COL]
    header = f"    {'子类型':<20s}" + "".join(f" {c:>6s}" for c in seg_cols)
    print(header)
    for _, row in rank_dist.iterrows():
        vals = "".join(f" {int(row[c]):>6d}" for c in seg_cols)
        print(f"    {row[GROUP_COL]:<20s}{vals}")

    # Language distribution
    print(f"\n  语言分布: 中文 {lang_dist['zh']} 页, 英文 {lang_dist['en']} 页")

    # Recommendation
    if not type_dist.empty:
        top = type_dist.iloc[0]
        zc_pct = ""
        if not zc_analysis.empty:
            match = zc_analysis[zc_analysis[GROUP_COL] == top[GROUP_COL]]
            if not match.empty:
                zc_pct = f"，零点击占比 {match.iloc[0]['占该类型%']}%"
        print(
            f"\n  建议: {top[GROUP_COL]} 机会分最高 ({top['机会分']})"
            f"{zc_pct}，建议设置 include_subtypes 优先优化。"
        )

    print(f"{'='*60}")


# ── Step entry point ─────────────────────────────────────────────────


def run(config: dict, output_dir: Path) -> dict:
    """Execute the analyze step.

    Args:
        config: Parsed config.yaml contents.
        output_dir: Root output directory (e.g. ``Path("output")``).

    Returns:
        A dict with ``output_files`` (list[Path]) and ``summary`` (dict).
    """
    gsc_dir = output_dir / "gsc"
    analyze_dir = output_dir / "analyze"
    analyze_dir.mkdir(parents=True, exist_ok=True)

    # Load CSVs
    ranking_path = find_latest_csv(gsc_dir, "ranking_pages_*.csv")

    logger.info("Loading ranking data from %s", ranking_path)
    ranking = pd.read_csv(ranking_path, encoding="utf-8-sig")

    try:
        zero_click_path = find_latest_csv(gsc_dir, "query_page_zero_click_*.csv")
        logger.info("Loading zero-click data from %s", zero_click_path)
        zero_click = pd.read_csv(zero_click_path, encoding="utf-8-sig")
    except FileNotFoundError:
        logger.warning("No zero-click CSV found — zero-click analysis will be skipped")
        zero_click = pd.DataFrame()

    date_range = _extract_date_range(ranking_path)
    total_pages = len(ranking)

    # Auto-discover subtypes from URL paths
    ranking[GROUP_COL] = discover_subtypes(ranking["路径"])

    # Run analyses
    type_dist = _subtype_distribution(ranking)
    zc_analysis = _zero_click_analysis(ranking, zero_click) if not zero_click.empty else pd.DataFrame()
    rank_dist = _ranking_segment_distribution(ranking)
    opp_preview = _opportunity_score_preview(ranking)
    lang_dist = _language_distribution(ranking)

    # Console report
    _print_report(total_pages, date_range, type_dist, zc_analysis, rank_dist, lang_dist)

    # Note current config in output
    seo_cfg = config.get("seo", {})
    page_filter = seo_cfg.get("page_filter", "")
    include_subtypes = seo_cfg.get("include_subtypes", [])

    # Save CSV — subtype summary
    csv_path = analyze_dir / "site_analysis.csv"
    type_dist.to_csv(csv_path, index=False, encoding="utf-8-sig")
    logger.info("Site analysis CSV saved to %s", csv_path)

    # Save JSON — full analysis
    analysis_result = {
        "date_range": date_range,
        "total_pages": total_pages,
        "current_page_filter": page_filter,
        "current_include_subtypes": include_subtypes,
        "subtype_distribution": type_dist.to_dict("records"),
        "zero_click_analysis": zc_analysis.to_dict("records"),
        "ranking_segment_distribution": rank_dist.to_dict("records"),
        "opportunity_score_preview": opp_preview.to_dict("records"),
        "language_distribution": lang_dist,
    }
    json_path = analyze_dir / "site_analysis.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(analysis_result, f, ensure_ascii=False, indent=2)
    logger.info("Site analysis JSON saved to %s", json_path)

    summary = {
        "total_pages": total_pages,
        "date_range": date_range,
        "subtypes": len(type_dist),
        "zero_click_pages": int(zc_analysis["零点击数"].sum()) if not zc_analysis.empty else 0,
        "language": lang_dist,
    }

    return {
        "output_files": [csv_path, json_path],
        "summary": summary,
    }
