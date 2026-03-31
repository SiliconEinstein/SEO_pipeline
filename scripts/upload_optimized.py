#!/usr/bin/env python3
"""Upload hook: push optimized SEO metadata to the wiki API.

Usage (called by run_pipeline_scheduled.sh):
    uv run python scripts/upload_optimized.py <OUTPUT_DIR> <RUN_ID> <TOP_N>

Contract:
    - Exit code 0 only when upload is fully completed.
    - Any non-zero exit code will stop archive rotation; output/ will be kept.

Requires:
    SEO_UPLOAD_API_BASE  environment variable or .env entry
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import urllib.error
from urllib.parse import unquote
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (same directory resolution as main.py)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# ------------------------------------------------------------------
# URL parsing helpers
# ------------------------------------------------------------------

def detect_language(path: str) -> str:
    """Return API language code from URL path.

    >>> detect_language("/en/sciencepedia/feynman/keyword/quantum")
    'en-US'
    >>> detect_language("/sciencepedia/feynman/keyword/quantum")
    'zh-CN'
    """
    return "en-US" if path.startswith("/en/") else "zh-CN"


def is_keyword_page(path: str) -> bool:
    """Check whether *path* is a keyword page (contains ``/keyword/``)."""
    return "/keyword/" in path


def extract_keyword_id(path: str) -> str:
    """Extract the keyword slug after ``/keyword/``.

    >>> extract_keyword_id("/en/sciencepedia/feynman/keyword/liquid_mirror_telescope")
    'liquid_mirror_telescope'
    """
    match = re.search(r"/keyword/(.+?)/?$", path)
    if not match:
        raise ValueError(f"Cannot extract keyword_id from: {path}")
    return unquote(match.group(1))


def extract_entry_id(path: str) -> str:
    """Extract the entry slug after ``/feynman/`` for article pages.

    >>> extract_entry_id("/sciencepedia/feynman/principles_of_genetics")
    'principles_of_genetics'
    >>> extract_entry_id("/en/sciencepedia/feynman/quantum_field_theory")
    'quantum_field_theory'
    """
    match = re.search(r"/feynman/(.+?)/?$", path)
    if not match:
        raise ValueError(f"Cannot extract entry_id from: {path}")
    return unquote(match.group(1))


# ------------------------------------------------------------------
# API helpers
# ------------------------------------------------------------------

def _api_post(api_base: str, endpoint: str, body: dict) -> dict:
    """POST JSON to *api_base/endpoint* and return the parsed response."""
    url = api_base.rstrip("/") + endpoint
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_node_id(api_base: str, entry_id: str, language: str) -> str:
    """Query the wiki API to resolve *entry_id* → node_id.

    Raises ``RuntimeError`` when the API does not return a valid node_id.
    """
    body = {"entry_id": entry_id, "language": language, "style": "Feynman"}
    resp = _api_post(api_base, "/api/v1/wiki_v2/article", body)
    code = resp.get("code")
    node_id = (resp.get("data") or {}).get("node_id")
    if code != 0 or not node_id:
        raise RuntimeError(
            f"node_id lookup failed for entry_id={entry_id!r}: {resp}"
        )
    return node_id


def batch_update(api_base: str, items: list[dict], chunk_size: int = 200) -> None:
    """Push SEO updates to the wiki API in chunks.

    Splits *items* into batches of *chunk_size* and sends each separately.
    Raises ``RuntimeError`` on the first API-level failure:
      - response code != 0
      - or code == 0 but failed_count > 0
    """
    for i in range(0, len(items), chunk_size):
        chunk = items[i : i + chunk_size]
        # Server contract: items is []string, each element is a JSON object string.
        payload_items = [json.dumps(x, ensure_ascii=False) for x in chunk]
        resp = _api_post(
            api_base,
            "/api/v1/wiki_inner/revision/batch_update",
            {"items": payload_items},
        )
        if resp.get("code") != 0:
            raise RuntimeError(
                f"batch_update failed on chunk {i // chunk_size + 1}: {resp}"
            )
        data = resp.get("data") or {}
        failed_count = int(data.get("failed_count", 0) or 0)
        if failed_count > 0:
            raise RuntimeError(
                "batch_update partial failure on chunk "
                f"{i // chunk_size + 1}: failed_count={failed_count}, resp={resp}"
            )


# ------------------------------------------------------------------
# Core logic
# ------------------------------------------------------------------

def build_items(
    metadata: dict[str, dict],
    api_base: str,
) -> tuple[list[dict], list[str]]:
    """Convert *metadata* (path → SEO fields) into API item dicts.

    Returns:
        (items, errors) – *errors* contains human-readable messages for
        paths that could not be processed (node_id lookup failures, etc.).
    """
    items: list[dict] = []
    errors: list[str] = []

    for path, fields in metadata.items():
        language = detect_language(path)
        seo_title = fields.get("title", "")
        seo_desc = fields.get("meta_description", "")

        base = {
            "language": language,
            "style": "Feynman",
            "seo_title": seo_title,
            "seo_description": seo_desc,
        }

        if is_keyword_page(path):
            try:
                base["keyword_id"] = extract_keyword_id(path)
            except ValueError as exc:
                errors.append(f"{path}: {exc}")
                continue
        else:
            try:
                entry_id = extract_entry_id(path)
            except ValueError as exc:
                errors.append(f"{path}: {exc}")
                continue
            try:
                base["node_id"] = fetch_node_id(api_base, entry_id, language)
            except Exception as exc:
                errors.append(f"{path}: node_id lookup failed – {exc}")
                continue

        items.append(base)

    return items, errors


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: upload_optimized.py <OUTPUT_DIR> <RUN_ID> <TOP_N>",
            file=sys.stderr,
        )
        return 2

    output_dir = Path(argv[1])
    run_id = argv[2]
    top_n = argv[3]

    api_base = os.environ.get("SEO_UPLOAD_API_BASE", "")
    if not api_base:
        print("upload: SEO_UPLOAD_API_BASE not set", file=sys.stderr)
        return 1

    opt_file = output_dir / "seo" / "optimized_metadata.json"
    if not opt_file.exists():
        print(f"upload: file not found: {opt_file}", file=sys.stderr)
        return 1

    with open(opt_file, "r", encoding="utf-8") as f:
        metadata: dict = json.load(f)

    if not metadata:
        print("upload: no pages to upload (empty metadata)")
        return 0

    print(f"upload: run_id={run_id}, top_n={top_n}, pages={len(metadata)}")

    # Build upload items (node_id lookups happen here)
    items, errors = build_items(metadata, api_base)

    for err in errors:
        print(f"upload: SKIP {err}", file=sys.stderr)

    if not items:
        print("upload: no uploadable items after processing", file=sys.stderr)
        return 1

    # Send batch update (chunked)
    print(f"upload: sending {len(items)} items to batch_update ...")
    try:
        batch_update(api_base, items)
    except Exception as exc:
        print(f"upload: batch_update failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"upload: done – {len(items)} uploaded, "
        f"{len(errors)} skipped"
    )

    # Contract: exit 0 only when ALL pages are uploaded successfully.
    if errors:
        print(
            f"upload: exiting with error – {len(errors)} pages could not be processed",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
