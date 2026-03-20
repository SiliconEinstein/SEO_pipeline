"""
Step 1 — Fetch & classify Google Search Console data.

Consolidates authentication, data fetching, page-type classification,
zero-click analysis, and ranking report generation into a single
pipeline step.  Output CSVs land in ``output_dir / "gsc/"``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

#: Page-type definitions — (url_prefixes, label).
PAGE_TYPES: list[tuple[list[str], str]] = [
    (["/paper-details/", "/en/paper-details/"], "Paper"),
    (["/scholar/", "/en/scholar/"], "Scholar"),
    (["/sciencepedia/", "/en/sciencepedia/"], "Sciencepedia"),
    (["/apps/", "/en/apps/"], "Apps"),
    (["/notebooks/"], "Notebooks"),
    (["/intro"], "Intro"),
    (["/blog/"], "Blog"),
]

RANKING_BINS: list[tuple[int, int, str]] = [
    (0, 3, "1-3 (首页顶部)"),
    (3, 5, "4-5"),
    (5, 10, "6-10 (首页底部)"),
    (10, 20, "11-20 (第2页)"),
    (20, 50, "21-50"),
    (50, 999, "50+"),
]

# ---------------------------------------------------------------------------
# Project-root resolver
# ---------------------------------------------------------------------------

#: Credentials are resolved relative to the current working directory,
#: so the user should run commands from the project root (seo_pipeline/).
def _cwd() -> Path:
    return Path.cwd()


# ===================================================================== #
#  Authentication helpers
# ===================================================================== #

def _get_gsc_service(credentials_file: str, token_file: str):
    """Return an authenticated Google Search Console API service object.

    On first run the user's browser is opened for OAuth consent.  The
    resulting token is persisted to *token_file* for subsequent calls.
    """
    if not os.path.exists(credentials_file):
        raise FileNotFoundError(
            f"OAuth credentials file not found: {credentials_file}"
        )

    creds: Credentials | None = None

    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except Exception:
            os.remove(token_file)
            creds = None

    if creds is None or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired OAuth token ...")
            try:
                creds.refresh(Request())
            except Exception:
                logger.warning("Token refresh failed — re-running OAuth flow.")
                creds = _run_oauth_flow(credentials_file)
        else:
            creds = _run_oauth_flow(credentials_file)

        with open(token_file, "w") as fh:
            fh.write(creds.to_json())
        logger.info("Token saved to %s", token_file)

    return build("searchconsole", "v1", credentials=creds)


def _run_oauth_flow(credentials_file: str) -> Credentials:
    """Execute the interactive OAuth2 browser flow."""
    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
    return flow.run_local_server(port=0)


def _list_sites(service) -> list[str]:
    """Return all site URLs accessible via *service*."""
    response = service.sites().list().execute()
    return [s["siteUrl"] for s in response.get("siteEntry", [])]


def _validate_site_url(service, site_url: str) -> None:
    """Raise ``ValueError`` if *site_url* is not accessible."""
    sites = _list_sites(service)
    if site_url not in sites:
        raise ValueError(
            f"Cannot access site '{site_url}'. "
            f"Accessible sites: {sites}"
        )


# ===================================================================== #
#  Date-range parsing
# ===================================================================== #

def _parse_date_range(date_range_str: str) -> tuple[str, str]:
    """Parse a human-friendly date-range string into (start, end) ISO dates.

    Supported formats: ``"7d"``, ``"28d"``, ``"3m"``, ``"6m"``,
    or ``"2026-01-01,2026-03-01"``.
    """
    today = datetime.now()
    end = today - timedelta(days=3)  # GSC data lags ~2-3 days

    if date_range_str.endswith("d"):
        days = int(date_range_str[:-1])
        start = end - timedelta(days=days)
    elif date_range_str.endswith("m"):
        months = int(date_range_str[:-1])
        start = end - timedelta(days=months * 30)
    elif "," in date_range_str:
        parts = date_range_str.split(",")
        return parts[0].strip(), parts[1].strip()
    else:
        raise ValueError(
            f"Unrecognised date_range format: '{date_range_str}'. "
            "Use 7d / 28d / 3m / 6m or 'YYYY-MM-DD,YYYY-MM-DD'."
        )

    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ===================================================================== #
#  Data fetching (paginated)
# ===================================================================== #

def _fetch_search_analytics(
    service,
    site_url: str,
    start_date: str,
    end_date: str,
    dimensions: list[str],
    row_limit: int = 25000,
) -> pd.DataFrame:
    """Paginate through the Search Analytics API and return a DataFrame."""
    all_rows: list[dict] = []
    start_row = 0

    while True:
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": dimensions,
            "rowLimit": row_limit,
            "startRow": start_row,
        }
        response = (
            service.searchanalytics()
            .query(siteUrl=site_url, body=body)
            .execute()
        )
        rows = response.get("rows", [])
        if not rows:
            break

        for row in rows:
            keys = row.get("keys", [])
            record = {
                dim: (keys[i] if i < len(keys) else "")
                for i, dim in enumerate(dimensions)
            }
            record["clicks"] = row.get("clicks", 0)
            record["impressions"] = row.get("impressions", 0)
            record["ctr"] = row.get("ctr", 0.0)
            record["position"] = row.get("position", 0.0)
            all_rows.append(record)

        if len(rows) < row_limit:
            break
        start_row += len(rows)

    if not all_rows:
        return pd.DataFrame(
            columns=dimensions + ["clicks", "impressions", "ctr", "position"]
        )
    return pd.DataFrame(all_rows)


# ===================================================================== #
#  Page-type classification  (importable by other steps)
# ===================================================================== #

def classify_page_type(path: str) -> str:
    """Map a URL path to a page-type label.

    ``"/"`` or ``"/en"`` -> ``"Homepage"``; prefix-matched against
    :data:`PAGE_TYPES`; otherwise ``"Other"``.
    """
    if path in ("/", "", "/en"):
        return "Homepage"
    for prefixes, name in PAGE_TYPES:
        for prefix in prefixes:
            if path.startswith(prefix) or path == prefix.rstrip("/"):
                return name
    return "Other"


# ===================================================================== #
#  Ranking helpers
# ===================================================================== #

def _rank_bin(position: float) -> str:
    for lo, hi, label in RANKING_BINS:
        if lo <= position < hi:
            return label
    return "50+"


def _priority_label(position: float) -> str:
    if position < 4:
        return "A-已在顶部"
    if position < 10:
        return "B-首页可优化"
    if position < 20:
        return "C-差一点上首页"
    return "D-需大幅优化"


def _add_ranking_labels(df: pd.DataFrame, site_url: str, base_url: str = "") -> pd.DataFrame:
    """Add ranking-bin, priority, and path columns to a *by-page* DataFrame.

    Returns a DataFrame with Chinese column names matching the output spec.
    """
    df = df.copy()

    # Derive the origin so we can strip it from full URLs to get paths.
    origin = _site_url_to_origin(site_url, base_url)
    df["路径"] = df["page"].str.replace(origin, "", regex=False)
    df["页面类型"] = df["路径"].apply(classify_page_type)
    df["排名段"] = df["position"].apply(_rank_bin)
    df["优先级"] = df["position"].apply(_priority_label)

    df = df.rename(columns={
        "page": "完整URL",
        "clicks": "点击",
        "impressions": "展示",
        "ctr": "CTR",
        "position": "平均排名",
    })

    return df[
        ["路径", "平均排名", "排名段", "优先级", "点击", "展示", "CTR", "完整URL", "页面类型"]
    ]


def _segment_pages(df: pd.DataFrame) -> pd.DataFrame:
    """Add a ``分群`` column based on click status and median impressions."""
    df = df.copy()
    median_imp = df["展示"].median()

    def _seg(row):
        if row["展示"] <= median_imp:
            return "无需求"
        if row["点击"] > 0:
            return "有需求有转化"
        return "有需求无转化"

    df["分群"] = df.apply(_seg, axis=1)
    return df


# ===================================================================== #
#  Zero-click report helpers
# ===================================================================== #

def _build_zero_click_report(
    df_qp: pd.DataFrame,
    site_url: str,
    page_filter: str | None,
    exclude_patterns: list[str],
    top_n: int = 10,
    base_url: str = "",
) -> pd.DataFrame:
    """Build the zero-click CSV from a raw query*page DataFrame.

    Steps: add path & page type -> filter -> compute page totals ->
    keep pages with zero clicks -> top-N queries per page.
    """
    origin = _site_url_to_origin(site_url, base_url)
    df = df_qp.copy()
    df["路径"] = df["page"].str.replace(origin, "", regex=False)
    df["页面类型"] = df["路径"].apply(classify_page_type)

    # Apply page_filter (path substring match)
    if page_filter:
        df = df[df["路径"].str.contains(page_filter, case=False)].copy()

    # Exclude patterns
    for pat in exclude_patterns:
        df = df[~df["路径"].str.contains(pat, case=False)].copy()

    if df.empty:
        return pd.DataFrame()

    # Per-page totals
    totals = (
        df.groupby("page")
        .agg(页面总展示=("impressions", "sum"), 页面总点击=("clicks", "sum"))
        .reset_index()
    )
    df = df.merge(totals, on="page")

    # Zero-click pages only
    zero = df[df["页面总点击"] == 0].copy()
    if zero.empty:
        return pd.DataFrame()

    result = (
        zero.sort_values("impressions", ascending=False)
        .groupby("page")
        .head(top_n)
        .sort_values(["页面总展示", "impressions"], ascending=[False, False])
    )

    return result[
        ["路径", "query", "impressions", "position", "页面总展示", "页面总点击"]
    ].rename(columns={
        "query": "查询词",
        "impressions": "展示",
        "position": "排名",
    })


# ===================================================================== #
#  Utility
# ===================================================================== #

def _site_url_to_origin(site_url: str, base_url: str = "") -> str:
    """Convert a GSC site_url to the URL origin used in search results.

    When *base_url* (from ``seo.base_url`` config) is provided it is used
    directly — this is the most reliable approach because ``sc-domain:``
    properties can appear with any subdomain in GSC results.

    ``base_url="https://www.bohrium.com"``  -> ``"https://www.bohrium.com"``
    ``"https://example.com/"``              -> ``"https://example.com"``
    """
    if base_url:
        return base_url.rstrip("/")
    if site_url.startswith("sc-domain:"):
        domain = site_url.split(":", 1)[1]
        return f"https://{domain}"
    return site_url.rstrip("/")


# ===================================================================== #
#  Public entry-point
# ===================================================================== #

def run(config: dict, output_dir: Path) -> dict:
    """Execute Step 1 of the SEO pipeline.

    Parameters
    ----------
    config : dict
        Parsed ``config.yaml``.  Expected keys::

            site_url          – e.g. ``"sc-domain:bohrium.com"``
            credentials_file  – OAuth client-secret JSON (relative to project root)
            date_range        – ``"28d"`` / ``"3m"`` / ``"YYYY-MM-DD,YYYY-MM-DD"``
            seo:
              page_filter       – path substring, e.g. ``"sciencepedia"``
              exclude_patterns  – list of path substrings to exclude

    output_dir : Path
        Root output directory.  Files are written to ``output_dir / "gsc/"``.

    Returns
    -------
    dict
        ``{"output_files": list[Path], "summary": dict}``
    """
    site_url: str = config["site_url"]
    date_range: str = config.get("date_range", "28d")
    seo_cfg: dict = config.get("seo", {})
    base_url: str = seo_cfg.get("base_url", "")
    page_filter: str | None = seo_cfg.get("page_filter")
    exclude_patterns: list[str] = [p for p in seo_cfg.get("exclude_patterns", []) if p]

    # --- resolve credential paths relative to working directory ---------
    cred_rel = config["credentials_file"]
    credentials_file = str(_cwd() / cred_rel)
    token_file = str(_cwd() / "token.json")

    # --- authenticate ---------------------------------------------------
    logger.info("Authenticating with Google Search Console ...")
    service = _get_gsc_service(credentials_file, token_file)
    _validate_site_url(service, site_url)
    logger.info("Authenticated.  Site URL: %s", site_url)

    # --- parse dates ----------------------------------------------------
    start_date, end_date = _parse_date_range(date_range)
    logger.info("Date range: %s -> %s", start_date, end_date)

    # --- fetch query x page data ----------------------------------------
    logger.info("Fetching query x page data ...")
    df_qp = _fetch_search_analytics(
        service, site_url, start_date, end_date, ["query", "page"]
    )
    logger.info("Fetched %d query x page rows.", len(df_qp))

    # --- fetch page-level data ------------------------------------------
    logger.info("Fetching page-level data ...")
    df_page = _fetch_search_analytics(
        service, site_url, start_date, end_date, ["page"]
    )
    logger.info("Fetched %d page rows.", len(df_page))

    # --- build filter tag for filenames ---------------------------------
    filter_tag = (
        page_filter.strip("/").replace("/", "_") if page_filter else "all"
    )

    # --- prepare output directory ---------------------------------------
    gsc_dir = output_dir / "gsc"
    gsc_dir.mkdir(parents=True, exist_ok=True)

    output_files: list[Path] = []

    # --- 1) zero-click report ------------------------------------------
    zero_report = _build_zero_click_report(
        df_qp, site_url, page_filter, exclude_patterns, base_url=base_url
    )
    zero_path = gsc_dir / f"query_page_zero_click_{filter_tag}_{end_date}.csv"
    if not zero_report.empty:
        zero_report.to_csv(zero_path, index=False, encoding="utf-8-sig")
        output_files.append(zero_path)
        logger.info("Zero-click report: %s (%d rows)", zero_path, len(zero_report))
    else:
        logger.info("No zero-click pages found; skipping zero-click CSV.")

    # --- 2) ranking pages report ----------------------------------------
    ranked = _add_ranking_labels(df_page, site_url, base_url)

    # Apply page_filter
    if page_filter:
        ranked = ranked[
            ranked["路径"].str.contains(page_filter, case=False)
        ].copy()

    # Exclude patterns
    for pat in exclude_patterns:
        ranked = ranked[~ranked["路径"].str.contains(pat, case=False)].copy()

    # Add segment column
    if not ranked.empty:
        ranked = _segment_pages(ranked)

    ranking_path = gsc_dir / f"ranking_pages_{filter_tag}_{end_date}.csv"
    if not ranked.empty:
        ranked.sort_values("展示", ascending=False).to_csv(
            ranking_path, index=False, encoding="utf-8-sig"
        )
        output_files.append(ranking_path)
        logger.info("Ranking report: %s (%d rows)", ranking_path, len(ranked))
    else:
        logger.info("No pages after filtering; skipping ranking CSV.")

    # --- 3) daily pages report (for evaluate step) ----------------------
    logger.info("Fetching date x page data ...")
    df_dp = _fetch_search_analytics(
        service, site_url, start_date, end_date, ["date", "page"]
    )
    logger.info("Fetched %d date x page rows.", len(df_dp))

    daily_path = gsc_dir / f"daily_pages_{filter_tag}_{end_date}.csv"
    if not df_dp.empty:
        df_daily = df_dp.copy()
        origin = _site_url_to_origin(site_url, base_url)
        df_daily["路径"] = df_daily["page"].str.replace(origin, "", regex=False)
        df_daily["页面类型"] = df_daily["路径"].apply(classify_page_type)

        # Apply page_filter
        if page_filter:
            df_daily = df_daily[
                df_daily["路径"].str.contains(page_filter, case=False)
            ].copy()

        # Exclude patterns
        for pat in exclude_patterns:
            df_daily = df_daily[
                ~df_daily["路径"].str.contains(pat, case=False)
            ].copy()

        df_daily = df_daily.rename(columns={
            "date": "日期",
            "clicks": "点击",
            "impressions": "展示",
            "ctr": "CTR",
            "position": "平均排名",
        })
        df_daily = df_daily[
            ["日期", "路径", "点击", "展示", "CTR", "平均排名", "页面类型"]
        ].sort_values(["日期", "路径"])

        df_daily.to_csv(daily_path, index=False, encoding="utf-8-sig")
        output_files.append(daily_path)
        logger.info("Daily pages report: %s (%d rows)", daily_path, len(df_daily))
    else:
        logger.info("No date x page data found; skipping daily CSV.")

    # --- summary --------------------------------------------------------
    summary: dict = {
        "site_url": site_url,
        "date_range": f"{start_date} ~ {end_date}",
        "total_query_page_rows": len(df_qp),
        "total_page_rows": len(df_page),
        "daily_page_rows": len(df_dp),
        "zero_click_report_rows": len(zero_report),
        "ranking_report_rows": len(ranked),
        "filter": filter_tag,
    }

    logger.info("Step 1 (fetch_gsc) complete.  Output files: %s", output_files)
    return {"output_files": output_files, "summary": summary}
