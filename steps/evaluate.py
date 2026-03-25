"""
Step 7 — Evaluate optimisation effectiveness via GSC performance tracking.

Two independent analyses:
1. **Trend analysis** (always runs when daily CSV exists) — overall + per-subtype
   daily metrics for all pages in the daily CSV.
2. **Optimised-page comparison** (requires ``--deploy-date``) — before/after
   CTR / clicks / impressions / position deltas for optimised pages only.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from steps._classify import discover_subtypes, find_latest_csv, get_filter_tag

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# Data loaders
# ══════════════════════════════════════════════════════════════════════════

def _load_daily_csv(gsc_dir: Path, filter_tag: str = "all") -> pd.DataFrame | None:
    """Load the most recent daily_pages_*.csv, or return None."""
    try:
        csv_path = find_latest_csv(gsc_dir, f"daily_pages_{filter_tag}_*.csv")
    except FileNotFoundError:
        return None
    logger.info("Using daily CSV: %s", csv_path.name)
    return pd.read_csv(csv_path, encoding="utf-8-sig")


def _load_optimized_paths(seo_dir: Path) -> set[str]:
    """Return the set of paths present in optimized_metadata.json."""
    opt_path = seo_dir / "optimized_metadata.json"
    if not opt_path.exists():
        return set()
    with open(opt_path, "r", encoding="utf-8") as f:
        return set(json.load(f).keys())


# ══════════════════════════════════════════════════════════════════════════
# Trend analysis (overall + per-subtype)
# ══════════════════════════════════════════════════════════════════════════

def _compute_trends(daily_df: pd.DataFrame, optimized_paths: set[str] | None = None) -> dict:
    """Compute daily trends for overall, per-subtype, and optionally optimised pages.

    Parameters
    ----------
    daily_df : DataFrame
        Daily pages data with columns: 日期, 路径, 点击, 展示, CTR, 平均排名.
    optimized_paths : set[str] | None
        If provided, also compute an ``_optimized_`` board for these paths.

    Returns
    -------
    dict
        ``{"overall": {...}, "by_subtype": {"label": {...}, ...}}``
        Each value has keys: dates, clicks, impressions, avg_ctr, avg_position, page_count.
    """
    df = daily_df.copy()
    df["日期"] = pd.to_datetime(df["日期"])

    # Discover subtypes from all unique paths
    unique_paths = df[["路径"]].drop_duplicates()
    unique_paths["subtype"] = discover_subtypes(unique_paths["路径"])
    df = df.merge(unique_paths, on="路径", how="left")

    def _agg_daily(group_df: pd.DataFrame) -> dict:
        """Aggregate a group into daily time-series dict."""
        agg = group_df.groupby("日期").agg(
            clicks=("点击", "sum"),
            impressions=("展示", "sum"),
            avg_position=("平均排名", "mean"),
            page_count=("路径", "nunique"),
            ctr_std=("CTR", "std"),
        ).sort_index()
        # 加权 CTR = 总点击 / 总展示
        agg["avg_ctr"] = (agg["clicks"] / agg["impressions"]).fillna(0)
        agg["ctr_std"] = agg["ctr_std"].fillna(0)
        return {
            "dates": [d.strftime("%Y-%m-%d") for d in agg.index],
            "clicks": [int(v) for v in agg["clicks"]],
            "impressions": [int(v) for v in agg["impressions"]],
            "avg_ctr": [round(v, 6) for v in agg["avg_ctr"]],
            "ctr_std": [round(v, 6) for v in agg["ctr_std"]],
            "avg_position": [round(v, 2) for v in agg["avg_position"]],
            "page_count": [int(v) for v in agg["page_count"]],
        }

    # Overall
    overall = _agg_daily(df)

    # Per-subtype
    by_subtype: dict[str, dict] = {}
    for st, sub_df in df.groupby("subtype"):
        by_subtype[st] = _agg_daily(sub_df)

    result: dict = {"overall": overall, "by_subtype": by_subtype}

    # Optimised pages board
    if optimized_paths:
        opt_df = df[df["路径"].isin(optimized_paths)]
        if not opt_df.empty:
            result["by_subtype"]["_optimized_"] = _agg_daily(opt_df)

    return result


def _write_trend_csv(seo_dir: Path, trends: dict) -> Path:
    """Write trend_report.csv from the trends dict.

    Columns: 日期, 板块, 点击, 展示, 平均CTR, 平均排名, 页面数
    """
    report_path = seo_dir / "trend_report.csv"
    rows: list[dict] = []

    # Overall
    overall = trends["overall"]
    for i, date in enumerate(overall["dates"]):
        rows.append({
            "日期": date,
            "板块": "_overall_",
            "点击": overall["clicks"][i],
            "展示": overall["impressions"][i],
            "平均CTR": overall["avg_ctr"][i],
            "平均排名": overall["avg_position"][i],
            "页面数": overall["page_count"][i],
        })

    # Per-subtype (including _optimized_ if present)
    for st_label, st_data in trends["by_subtype"].items():
        for i, date in enumerate(st_data["dates"]):
            rows.append({
                "日期": date,
                "板块": st_label,
                "点击": st_data["clicks"][i],
                "展示": st_data["impressions"][i],
                "平均CTR": st_data["avg_ctr"][i],
                "平均排名": st_data["avg_position"][i],
                "页面数": st_data["page_count"][i],
            })

    fieldnames = ["日期", "板块", "点击", "展示", "平均CTR", "平均排名", "页面数"]
    with open(report_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return report_path


# ══════════════════════════════════════════════════════════════════════════
# Trend visualisation
# ══════════════════════════════════════════════════════════════════════════

#: Minimum data points for a subtype to appear on charts.
_MIN_DATAPOINTS = 10

#: Subtypes to exclude from the CTR ±1σ subplot (too few pages, noisy std).
_CTR_CHART_EXCLUDE = {"sciencepedia", "sciencepedia/agent-tools", "总体"}

#: Chinese labels for chart display.
_METRIC_LABELS = {
    "page_count": "有展示页面数",
    "clicks": "点击",
    "impressions": "展示",
    "avg_ctr": "加权 CTR",
    "avg_position": "平均排名",
}


def _plot_trends(seo_dir: Path, trends: dict, deploy_date: str | None = None) -> Path:
    """Generate a 3+2 trend chart (page_count, clicks, impressions, CTR, position).

    Layout: 3 rows × 2 columns, last cell left empty.

    Parameters
    ----------
    seo_dir : Path
        Output directory for the PNG.
    trends : dict
        Output of :func:`_compute_trends`.
    deploy_date : str | None
        If provided, draw a vertical dashed line at deploy date.

    Returns
    -------
    Path
        Path to the saved PNG file.
    """
    plt.rcParams.update({
        "font.sans-serif": ["PingFang SC", "Heiti SC", "Microsoft YaHei",
                            "SimHei", "Arial Unicode MS", "sans-serif"],
        "axes.unicode_minus": False,
    })

    metrics = ["page_count", "impressions", "clicks", "avg_ctr", "avg_position"]
    fig, axes = plt.subplots(3, 2, figsize=(16, 14), sharex=True)
    axes = axes.flatten()

    # Collect series: overall, subtypes (with enough data), _optimized_
    series: list[tuple[str, dict, dict]] = []  # (label, data, style)

    overall = trends["overall"]
    series.append(("总体", overall, {"linewidth": 2.5, "color": "#1f77b4", "zorder": 10}))

    # Subtypes sorted by total impressions (descending), with fixed colors
    _SUBTYPE_COLORS = ["#ff7f0e", "#2ca02c", "#9467bd", "#8c564b",
                       "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]
    subtypes = {
        k: v for k, v in trends["by_subtype"].items()
        if k != "_optimized_" and len(v["dates"]) >= _MIN_DATAPOINTS
    }
    sorted_subs = sorted(
        subtypes.items(),
        key=lambda kv: sum(kv[1]["impressions"]),
        reverse=True,
    )
    for i, (label, data) in enumerate(sorted_subs):
        color = _SUBTYPE_COLORS[i % len(_SUBTYPE_COLORS)]
        series.append((label, data, {"linewidth": 1.2, "alpha": 0.8, "color": color}))

    # Optimised pages
    if "_optimized_" in trends["by_subtype"]:
        opt = trends["by_subtype"]["_optimized_"]
        if len(opt["dates"]) >= _MIN_DATAPOINTS:
            series.append(("已优化页面", opt, {
                "linewidth": 2, "linestyle": "--", "color": "#d62728", "zorder": 9,
            }))

    for ax, metric in zip(axes[:len(metrics)], metrics):
        for label, data, style in series:
            dates = pd.to_datetime(data["dates"])
            values = data[metric]
            ax.plot(dates, values, label=label, **style)

        ax.set_ylabel(_METRIC_LABELS[metric])
        ax.grid(True, alpha=0.3)

        # Position: lower = better → invert y-axis
        if metric == "avg_position":
            ax.invert_yaxis()

        # Deploy date marker
        if deploy_date:
            deploy_dt = pd.to_datetime(deploy_date)
            ax.axvline(deploy_dt, color="gray", linestyle=":", linewidth=1, alpha=0.7)

    # 6th subplot: CTR with ±1σ error bands (exclude noisy boards)
    import numpy as np

    ax_ctr = axes[-1]
    for label, data, style in series:
        if label in _CTR_CHART_EXCLUDE:
            continue
        dates = pd.to_datetime(data["dates"])
        ctr = np.array(data["avg_ctr"])
        line = ax_ctr.plot(dates, ctr, label=label, **style)
        if "ctr_std" in data:
            std = np.array(data["ctr_std"])
            color = line[0].get_color()
            ax_ctr.fill_between(dates,
                                np.maximum(ctr - std, 0),
                                ctr + std,
                                color=color, alpha=0.1)
    ax_ctr.set_ylabel("CTR (±1σ)")
    ax_ctr.grid(True, alpha=0.3)
    if deploy_date:
        ax_ctr.axvline(pd.to_datetime(deploy_date),
                       color="gray", linestyle=":", linewidth=1, alpha=0.7)

    # Single legend at bottom
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=min(len(labels), 5),
               fontsize=9, frameon=False, bbox_to_anchor=(0.5, -0.02))

    fig.suptitle("SEO 趋势跟踪", fontsize=14, fontweight="bold")
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout(rect=[0, 0.04, 1, 0.96])

    out_path = seo_dir / "trend_chart.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path



# ══════════════════════════════════════════════════════════════════════════
# GSC performance evaluation (before/after deploy-date)
# ══════════════════════════════════════════════════════════════════════════

def _evaluate_gsc_performance(
    daily_df: pd.DataFrame,
    deploy_date: str,
    optimized_paths: set[str],
) -> dict | None:
    """Compare GSC metrics before/after deployment for optimised pages.

    Returns a summary dict, or None if data is insufficient.
    """
    daily_df = daily_df.copy()
    daily_df["日期"] = pd.to_datetime(daily_df["日期"])
    deploy_dt = pd.to_datetime(deploy_date)

    # Only keep optimised pages
    daily_df = daily_df[daily_df["路径"].isin(optimized_paths)]
    if daily_df.empty:
        logger.warning("No optimised pages found in daily CSV")
        return None

    before = daily_df[daily_df["日期"] < deploy_dt]
    after = daily_df[daily_df["日期"] > deploy_dt]

    if before.empty or after.empty:
        logger.warning(
            "Insufficient data around deploy date %s "
            "(before: %d rows, after: %d rows)",
            deploy_date, len(before), len(after),
        )
        return None

    before_days = before["日期"].nunique()
    after_days = after["日期"].nunique()
    before_window = f"{before['日期'].min().date()} ~ {before['日期'].max().date()}"
    after_window = f"{after['日期'].min().date()} ~ {after['日期'].max().date()}"

    # Per-page aggregation for before/after
    def _agg(df: pd.DataFrame, n_days: int) -> pd.DataFrame:
        agg = df.groupby("路径").agg(
            total_clicks=("点击", "sum"),
            total_impressions=("展示", "sum"),
            avg_ctr=("CTR", "mean"),
            avg_position=("平均排名", "mean"),
        ).reset_index()
        agg["daily_clicks"] = agg["total_clicks"] / n_days
        agg["daily_impressions"] = agg["total_impressions"] / n_days
        return agg

    before_agg = _agg(before, before_days)
    after_agg = _agg(after, after_days)

    # Inner join: only pages with data in both windows
    merged = before_agg.merge(
        after_agg, on="路径", suffixes=("_before", "_after")
    )
    if merged.empty:
        logger.warning("No optimised pages have data in both before and after windows")
        return None

    # Compute deltas
    merged["ΔCTR"] = merged["avg_ctr_after"] - merged["avg_ctr_before"]
    merged["Δclicks_daily"] = merged["daily_clicks_after"] - merged["daily_clicks_before"]
    merged["Δimpressions_daily"] = merged["daily_impressions_after"] - merged["daily_impressions_before"]
    merged["Δposition"] = merged["avg_position_before"] - merged["avg_position_after"]  # positive = improved

    if len(merged) < 10:
        logger.warning(
            "Only %d optimised pages in both windows — sample size may be too small",
            len(merged),
        )

    # Aggregate stats
    stats = {
        "count": len(merged),
        "avg_ΔCTR": round(merged["ΔCTR"].mean(), 6),
        "median_ΔCTR": round(merged["ΔCTR"].median(), 6),
        "total_Δclicks_daily": round(merged["Δclicks_daily"].sum(), 2),
        "total_Δimpressions_daily": round(merged["Δimpressions_daily"].sum(), 2),
        "avg_Δposition": round(merged["Δposition"].mean(), 2),
        "improved_count": int((merged["ΔCTR"] > 0).sum()),
        "declined_count": int((merged["ΔCTR"] < 0).sum()),
        "unchanged_count": int((merged["ΔCTR"] == 0).sum()),
    }

    # Pages not found in after window
    before_only = set(before_agg["路径"]) - set(after_agg["路径"])

    # Top improvers / decliners
    sorted_df = merged.sort_values("ΔCTR", ascending=False)
    top_improved = [
        {"path": r["路径"], "ΔCTR": round(r["ΔCTR"], 6), "Δposition": round(r["Δposition"], 2)}
        for _, r in sorted_df.head(5).iterrows()
    ]
    top_declined = [
        {"path": r["路径"], "ΔCTR": round(r["ΔCTR"], 6), "Δposition": round(r["Δposition"], 2)}
        for _, r in sorted_df.tail(3).iterrows()
        if r["ΔCTR"] < 0
    ]

    # Page-level report rows
    page_rows: list[dict] = []
    for _, row in sorted_df.iterrows():
        page_rows.append({
            "path": row["路径"],
            "CTR_before": round(row["avg_ctr_before"], 6),
            "CTR_after": round(row["avg_ctr_after"], 6),
            "ΔCTR": round(row["ΔCTR"], 6),
            "clicks_before_daily": round(row["daily_clicks_before"], 2),
            "clicks_after_daily": round(row["daily_clicks_after"], 2),
            "Δclicks_daily": round(row["Δclicks_daily"], 2),
            "impressions_before_daily": round(row["daily_impressions_before"], 2),
            "impressions_after_daily": round(row["daily_impressions_after"], 2),
            "position_before": round(row["avg_position_before"], 2),
            "position_after": round(row["avg_position_after"], 2),
            "Δposition": round(row["Δposition"], 2),
        })

    return {
        "deploy_date": deploy_date,
        "before_window": before_window,
        "after_window": after_window,
        "before_days": before_days,
        "after_days": after_days,
        "stats": stats,
        "missing_after": sorted(before_only),
        "top_improved": top_improved,
        "top_declined": top_declined,
        "page_rows": page_rows,
    }


# ══════════════════════════════════════════════════════════════════════════
# Output helpers
# ══════════════════════════════════════════════════════════════════════════

def _write_report_csv(seo_dir: Path, gsc_perf: dict | None) -> Path:
    """Write evaluation_report.csv with per-page GSC deltas."""
    report_path = seo_dir / "evaluation_report.csv"

    if not gsc_perf or not gsc_perf["page_rows"]:
        with open(report_path, "w", newline="", encoding="utf-8-sig") as f:
            f.write("path,message\n")
            f.write(",no data available\n")
        return report_path

    rows = gsc_perf["page_rows"]
    fieldnames = list(rows[0].keys())
    with open(report_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return report_path


def _print_console_report(
    gsc_perf: dict | None,
    trends: dict | None,
    trend_skip_reason: str | None = None,
) -> None:
    """Print a human-readable console summary."""
    print()
    print(f"{'=' * 60}")
    print(f"  SEO Optimisation Evaluation")
    print(f"{'=' * 60}")

    # ── Trend summary ──────────────────────────────────────────────
    if trends:
        overall = trends["overall"]
        n_dates = len(overall["dates"])
        if n_dates > 0:
            print()
            print(f"  ── Trend Summary ({overall['dates'][0]} ~ {overall['dates'][-1]}) ──")
            print()
            total_clicks = sum(overall["clicks"])
            total_impressions = sum(overall["impressions"])
            avg_ctr = total_clicks / total_impressions if total_impressions else 0
            avg_pos = sum(overall["avg_position"]) / n_dates
            max_pages = max(overall["page_count"])
            print(f"  Overall ({n_dates} days, {max_pages} pages max):")
            print(f"    total clicks     : {total_clicks:,}")
            print(f"    total impressions: {total_impressions:,}")
            print(f"    avg CTR          : {avg_ctr:.4f}")
            print(f"    avg position     : {avg_pos:.1f}")

            # Per-subtype summary (exclude _optimized_)
            subtypes = {k: v for k, v in trends["by_subtype"].items() if k != "_optimized_"}
            if subtypes:
                print()
                print(f"  By subtype:")
                for st_label, st_data in sorted(subtypes.items()):
                    st_clicks = sum(st_data["clicks"])
                    st_impressions = sum(st_data["impressions"])
                    st_ctr = st_clicks / st_impressions if st_impressions else 0
                    st_pages = max(st_data["page_count"])
                    print(f"    {st_label:30s}  pages={st_pages:<4d}  clicks={st_clicks:<6,d}  imp={st_impressions:<8,d}  CTR={st_ctr:.4f}")

            # Optimised pages trend summary
            if "_optimized_" in trends["by_subtype"]:
                opt = trends["by_subtype"]["_optimized_"]
                opt_clicks = sum(opt["clicks"])
                opt_impressions = sum(opt["impressions"])
                opt_ctr = opt_clicks / opt_impressions if opt_impressions else 0
                opt_pages = max(opt["page_count"])
                print()
                print(f"  Optimised pages ({opt_pages} pages):")
                print(f"    total clicks     : {opt_clicks:,}")
                print(f"    total impressions: {opt_impressions:,}")
                print(f"    avg CTR          : {opt_ctr:.4f}")
    else:
        print()
        if trend_skip_reason == "empty_csv":
            print("  (daily_pages CSV is empty — trend analysis skipped)")
        else:
            print("  (no daily CSV — trend analysis skipped)")

    # ── GSC before/after comparison ────────────────────────────────
    print()
    print(f"  ── GSC Before/After Comparison ──")

    if not gsc_perf:
        print()
        print("  (skipped: no --deploy-date or insufficient data)")
    else:
        gp = gsc_perf
        s = gp["stats"]

        print()
        print(f"  Deploy date        : {gp['deploy_date']}")
        print(f"  Before window      : {gp['before_window']} ({gp['before_days']} days)")
        print(f"  After window       : {gp['after_window']} ({gp['after_days']} days)")
        print(f"  Pages tracked      : {s['count']}")
        print()
        print(f"  avg ΔCTR           : {s['avg_ΔCTR']:+.4f}")
        print(f"  median ΔCTR        : {s['median_ΔCTR']:+.4f}")
        print(f"  Δclicks/day        : {s['total_Δclicks_daily']:+.1f}")
        print(f"  Δimpressions/day   : {s['total_Δimpressions_daily']:+.1f}")
        print(f"  avg Δposition      : {s['avg_Δposition']:+.1f}")
        print(f"  improved/declined  : {s['improved_count']}/{s['declined_count']}")

        if gp["missing_after"]:
            print()
            print(f"  Pages missing in after window ({len(gp['missing_after'])}):")
            for p in gp["missing_after"][:5]:
                print(f"    {p}")
            if len(gp["missing_after"]) > 5:
                print(f"    ... and {len(gp['missing_after']) - 5} more")

        if gp["top_improved"]:
            print()
            print(f"  Top improved:")
            for item in gp["top_improved"]:
                print(f"    {item['path'][:50]:50s}  ΔCTR={item['ΔCTR']:+.4f}  Δpos={item['Δposition']:+.1f}")
        if gp["top_declined"]:
            print(f"  Top declined:")
            for item in gp["top_declined"]:
                print(f"    {item['path'][:50]:50s}  ΔCTR={item['ΔCTR']:+.4f}  Δpos={item['Δposition']:+.1f}")

    print(f"{'=' * 60}")


# ══════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════

def run(config: dict, output_dir: Path) -> dict:
    """Run the evaluate step.

    Parameters
    ----------
    config : dict
        Parsed config.yaml. Relevant keys:
        - ``evaluate.deploy_date`` (optional, e.g. ``"2026-03-20"``)

    output_dir : Path
        Root output directory.

    Returns
    -------
    dict
        ``{"output_files": list[Path], "summary": dict}``
    """
    eval_cfg = config.get("evaluate", {})
    deploy_date: str | None = eval_cfg.get("deploy_date")

    seo_dir = output_dir / "seo"
    gsc_dir = output_dir / "gsc"
    seo_dir.mkdir(parents=True, exist_ok=True)

    output_files: list[Path] = []

    # ── Load daily CSV (shared by trends and GSC comparison) ──────
    tag = get_filter_tag(config)
    daily_df = _load_daily_csv(gsc_dir, filter_tag=tag)
    optimized_paths = _load_optimized_paths(seo_dir)

    # ── Trend analysis (always runs when daily CSV exists) ────────
    trends: dict | None = None
    trend_skip_reason: str | None = None
    if daily_df is not None and not daily_df.empty:
        trends = _compute_trends(
            daily_df,
            optimized_paths=optimized_paths if optimized_paths else None,
        )
        trend_csv = _write_trend_csv(seo_dir, trends)
        output_files.append(trend_csv)
        logger.info("Trend report saved: %s", trend_csv)

        trend_png = _plot_trends(seo_dir, trends, deploy_date)
        output_files.append(trend_png)
        logger.info("Trend chart saved: %s", trend_png)
    else:
        if daily_df is None:
            logger.warning("No daily_pages CSV found — run 'fetch' first to generate daily data")
            trend_skip_reason = "missing_csv"
        else:
            logger.warning("daily_pages CSV is empty — trend analysis skipped")
            trend_skip_reason = "empty_csv"

    # ── GSC before/after comparison (requires --deploy-date) ──────
    gsc_perf: dict | None = None
    if deploy_date:
        if daily_df is not None and not daily_df.empty:
            if optimized_paths:
                gsc_perf = _evaluate_gsc_performance(
                    daily_df, deploy_date, optimized_paths
                )
            else:
                logger.warning("No optimized_metadata.json — cannot determine which pages to track")
        else:
            logger.warning("No daily CSV for GSC comparison")
    else:
        logger.info("No --deploy-date — skipping before/after comparison")

    # ── Write evaluation_report.csv ────────────────────────────────
    report_csv = _write_report_csv(seo_dir, gsc_perf)
    output_files.append(report_csv)
    logger.info("Evaluation report saved: %s", report_csv)

    # ── Build full summary (for JSON file) ───────────────────────────
    full_summary: dict = {}
    if trends:
        full_summary["trends"] = trends
    if gsc_perf:
        perf_summary = {k: v for k, v in gsc_perf.items() if k != "page_rows"}
        full_summary["deploy_date"] = perf_summary.pop("deploy_date", deploy_date)
        full_summary["stats"] = perf_summary.pop("stats", {})
        full_summary.update(perf_summary)

    # ── Write evaluation_summary.json ──────────────────────────────
    summary_json = seo_dir / "evaluation_summary.json"
    with open(summary_json, "w", encoding="utf-8") as f:
        json.dump(full_summary, f, ensure_ascii=False, indent=2)
    output_files.append(summary_json)
    logger.info("Evaluation summary saved: %s", summary_json)

    # ── Console report ─────────────────────────────────────────────
    _print_console_report(gsc_perf, trends, trend_skip_reason=trend_skip_reason)

    # ── CLI summary (aggregated only, no time-series arrays) ───────
    cli_summary: dict = {}
    if trends:
        overall = trends.get("overall", {})
        n_days = len(overall.get("dates", []))
        n_subtypes = len(trends.get("by_subtype", {}))
        cli_summary["trend_days"] = n_days
        cli_summary["subtypes_tracked"] = n_subtypes
        if n_days:
            cli_summary["date_range"] = f"{overall['dates'][0]} ~ {overall['dates'][-1]}"
    if gsc_perf:
        cli_summary["deploy_date"] = full_summary.get("deploy_date", deploy_date)
        cli_summary["stats"] = full_summary.get("stats", {})

    return {"output_files": output_files, "summary": cli_summary}
