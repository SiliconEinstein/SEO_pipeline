#!/usr/bin/env python3
"""Minimal probe for /api/v1/wiki_inner/revision/batch_update."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv


def main() -> int:
    load_dotenv()

    base = (os.getenv("SEO_UPLOAD_API_BASE") or "").rstrip("/")
    if not base:
        print("SEO_UPLOAD_API_BASE is not set")
        return 1

    url = f"{base}/api/v1/wiki_inner/revision/batch_update"
    item = {
        "keyword_id": "__probe__",
        "language": "en-US",
        "style": "Feynman",
        "seo_title": "probe",
        "seo_description": "probe",
    }
    payload = {"items": [json.dumps(item, ensure_ascii=False)]}

    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    print(f"POST {url}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print("status:", resp.getcode())
            print(resp.read().decode("utf-8"))
            return 0
    except urllib.error.HTTPError as e:
        print("status:", e.code)
        print(e.read().decode("utf-8", errors="ignore"))
        return 2
    except Exception as e:  # pragma: no cover - network/runtime dependent
        print("error:", repr(e))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
