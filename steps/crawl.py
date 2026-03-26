"""
Step 3: Crawl existing SEO metadata from live pages.

Async-fetches each page listed in priority_ranked.csv, parses
<head> meta tags, Open Graph, Twitter Card, canonical, JSON-LD Schema.org,
and <h1> tags, then persists the results for downstream comparison.

Outputs (under output_dir/seo/):
    existing_metadata.json  - full SEO metadata keyed by path
    crawl_report.csv        - per-page fetch status & timing
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# HTML parsing
# ------------------------------------------------------------------

def extract_seo_metadata(html: str, path: str) -> dict:
    """Extract SEO metadata from an HTML document.

    Searches <head> for standard meta/link tags and the *entire* document
    for ``<script type="application/ld+json">`` blocks (Bohrium places
    Schema.org markup inside ``<body>``).
    """
    soup = BeautifulSoup(html, "html.parser")
    head = soup.find("head")
    if not head:
        return {"path": path, "error": "no <head> found"}

    meta: dict = {}

    # <title>
    title_tag = head.find("title")
    meta["title"] = title_tag.get_text(strip=True) if title_tag else None

    # Standard <meta> tags
    _meta_names = {
        "meta_description": "description",
        "meta_keywords": "keywords",
        "meta_robots": "robots",
        "meta_author": "author",
    }
    for key, name in _meta_names.items():
        tag = head.find("meta", attrs={"name": name})
        meta[key] = tag["content"] if tag and tag.get("content") else None

    # Open Graph
    og_fields = [
        "og:title", "og:description", "og:url", "og:type",
        "og:image", "og:site_name", "og:image:width", "og:image:height",
    ]
    for field in og_fields:
        tag = head.find("meta", attrs={"property": field})
        key = field.replace(":", "_")
        meta[key] = tag["content"] if tag and tag.get("content") else None

    # Twitter Card
    tw_fields = [
        "twitter:card", "twitter:title", "twitter:description",
        "twitter:image", "twitter:site",
    ]
    for field in tw_fields:
        tag = head.find("meta", attrs={"name": field})
        key = field.replace(":", "_")
        meta[key] = tag["content"] if tag and tag.get("content") else None

    # Canonical URL
    canonical = head.find("link", attrs={"rel": "canonical"})
    meta["canonical"] = canonical["href"] if canonical and canonical.get("href") else None

    # Alternate links (hreflang)
    alternates = head.find_all("link", attrs={"rel": "alternate"})
    meta["alternates"] = [
        {"href": a.get("href"), "hreflang": a.get("hreflang")}
        for a in alternates if a.get("href")
    ]

    # Schema.org JSON-LD  --  search the **entire** document, not just <head>,
    # because Bohrium injects structured data in <body>.
    schema_scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    schemas: list[dict] = []
    for script in schema_scripts:
        try:
            schemas.append(json.loads(script.string))
        except (json.JSONDecodeError, TypeError) as e:
            logger.debug("JSON-LD 解析失败，已跳过: %s", e)
    meta["schema_json_ld"] = schemas if schemas else None

    # <h1> tags (typically in <body>)
    h1_tags = soup.find_all("h1")
    meta["h1"] = [h.get_text(strip=True) for h in h1_tags if h.get_text(strip=True)]

    return meta


# ------------------------------------------------------------------
# Async fetching
# ------------------------------------------------------------------

async def _fetch_one(
    session: aiohttp.ClientSession,
    base_url: str,
    path: str,
    semaphore: asyncio.Semaphore,
) -> tuple[str, dict, int, float]:
    """Fetch a single page and return (path, metadata, status, elapsed_s)."""
    url = base_url + path
    async with semaphore:
        start = time.monotonic()
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                status = resp.status
                html = await resp.text()
                elapsed = time.monotonic() - start
                if status == 200:
                    metadata = extract_seo_metadata(html, path)
                else:
                    metadata = {"error": f"HTTP {status}"}
                return path, metadata, status, elapsed
        except Exception as exc:
            elapsed = time.monotonic() - start
            msg = str(exc).strip() or exc.__class__.__name__
            return path, {"error": msg}, 0, elapsed


async def _fetch_all(
    paths: list[str],
    base_url: str,
    concurrency: int,
) -> tuple[dict, list[dict]]:
    """Fetch all *paths* concurrently.

    Returns:
        (ordered_results, report_rows)
    """
    semaphore = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(
        limit=concurrency, limit_per_host=concurrency,
    )
    headers = {
        "User-Agent": f"Mozilla/5.0 (compatible; SEOAuditBot/1.0; +{base_url})",
        "Accept": "text/html",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    }

    results: dict = {}
    report_rows: list[dict] = []
    total = len(paths)
    done = 0
    errors = 0
    t0 = time.monotonic()

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        tasks = [_fetch_one(session, base_url, p, semaphore) for p in paths]
        for coro in asyncio.as_completed(tasks):
            path, metadata, status, elapsed = await coro
            done += 1
            results[path] = metadata

            has_title = bool(metadata.get("title"))
            has_desc = bool(metadata.get("meta_description"))
            has_schema = bool(metadata.get("schema_json_ld"))
            has_og = bool(metadata.get("og_title"))
            has_error = "error" in metadata

            if has_error:
                errors += 1

            report_rows.append({
                "path": path,
                "status": status,
                "elapsed_s": round(elapsed, 2),
                "has_title": has_title,
                "has_description": has_desc,
                "has_og": has_og,
                "has_schema": has_schema,
                "has_error": has_error,
                "title": metadata.get("title", ""),
                "error": metadata.get("error", ""),
            })

            # Progress every 50 pages (and on the last page)
            if done % 50 == 0 or done == total:
                elapsed_total = time.monotonic() - t0
                rate = done / elapsed_total if elapsed_total > 0 else 0
                print(
                    f"  [{done}/{total}] {rate:.1f} pages/s | "
                    f"errors: {errors}"
                )

    total_time = time.monotonic() - t0
    print(f"\nCrawl finished: {total} pages in {total_time:.1f}s, {errors} errors")

    # Preserve original priority ordering
    ordered = {p: results[p] for p in paths if p in results}

    # Sort report rows to match priority ordering
    path_index = {p: i for i, p in enumerate(paths)}
    report_rows.sort(key=lambda r: path_index.get(r["path"], len(paths)))

    return ordered, report_rows


# ------------------------------------------------------------------
# Coverage summary
# ------------------------------------------------------------------

def _print_coverage(data: dict) -> dict:
    """Print and return a coverage-statistics dict."""
    total = len(data)
    if total == 0:
        print("No pages to summarise.")
        return {}

    counters = {
        "title": sum(1 for v in data.values() if v.get("title")),
        "meta_description": sum(1 for v in data.values() if v.get("meta_description")),
        "meta_keywords": sum(1 for v in data.values() if v.get("meta_keywords")),
        "og_tags": sum(1 for v in data.values() if v.get("og_title")),
        "schema_json_ld": sum(1 for v in data.values() if v.get("schema_json_ld")),
        "canonical": sum(1 for v in data.values() if v.get("canonical")),
        "h1": sum(1 for v in data.values() if v.get("h1")),
        "errors": sum(1 for v in data.values() if "error" in v),
    }

    print(f"\n{'=' * 50}")
    print(f"SEO metadata coverage ({total} pages)")
    print(f"{'=' * 50}")
    for label, count in counters.items():
        pct = count / total * 100
        print(f"  {label:<20s} {count:>4}/{total}  ({pct:.1f}%)")

    # List pages missing title / description (first 10)
    missing_title = [p for p, v in data.items() if not v.get("title")]
    missing_desc = [p for p, v in data.items() if not v.get("meta_description")]

    if missing_title:
        print(f"\nPages missing title ({len(missing_title)}):")
        for p in missing_title[:10]:
            print(f"  {p}")
        if len(missing_title) > 10:
            print(f"  ... and {len(missing_title) - 10} more")

    if missing_desc:
        print(f"\nPages missing meta description ({len(missing_desc)}):")
        for p in missing_desc[:10]:
            print(f"  {p}")
        if len(missing_desc) > 10:
            print(f"  ... and {len(missing_desc) - 10} more")

    return {"total_pages": total, **counters}


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

def run(config: dict, output_dir: Path) -> dict:
    """Crawl existing SEO metadata for every page in priority_ranked.csv.

    Args:
        config: parsed config.yaml; uses ``seo.base_url`` and
                ``seo.crawl_concurrency``.
        output_dir: root output directory (``Path``).

    Returns:
        ``{"output_files": [Path, ...], "summary": dict}``
    """
    seo_cfg = config.get("seo", {})
    if "base_url" not in seo_cfg:
        raise ValueError("缺少必填配置 seo.base_url，请在 config.yaml 中设置")
    base_url: str = seo_cfg["base_url"].rstrip("/")
    concurrency: int = int(seo_cfg.get("crawl_concurrency", 20))

    seo_dir = output_dir / "seo"
    input_csv = seo_dir / "priority_ranked.csv"
    output_json = seo_dir / "existing_metadata.json"
    output_report = seo_dir / "crawl_report.csv"

    # 1. Load paths --------------------------------------------------
    if not input_csv.exists():
        raise FileNotFoundError(
            f"Input file not found: {input_csv}  "
            "(has Step 2 / priority ranking been run?)"
        )

    paths: list[str] = []
    with open(input_csv, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            paths.append(row["路径"])

    print(f"Step 3 — Crawl: {len(paths)} pages, concurrency={concurrency}")
    print(f"  base_url : {base_url}")
    print(f"  input    : {input_csv}")
    print(f"  output   : {output_json}")

    # 2. Async fetch -------------------------------------------------
    data, report_rows = asyncio.run(
        _fetch_all(paths, base_url, concurrency),
    )

    # 3. Persist results ---------------------------------------------
    seo_dir.mkdir(parents=True, exist_ok=True)

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Metadata saved: {output_json}")

    if report_rows:
        with open(output_report, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=report_rows[0].keys())
            writer.writeheader()
            writer.writerows(report_rows)
        print(f"Report saved:   {output_report}")

    # 4. Coverage summary -------------------------------------------
    summary = _print_coverage(data)

    output_files = [output_json, output_report]
    return {"output_files": output_files, "summary": summary}
