"""Regression tests for steps/crawl.py."""

import asyncio

from steps.crawl import _fetch_one, extract_seo_metadata


def test_extract_seo_metadata_ignores_invalid_jsonld():
    """Invalid JSON-LD should be skipped, not crash metadata extraction."""
    html = """
    <html>
      <head>
        <title>Demo</title>
        <meta name="description" content="desc" />
        <script type="application/ld+json">{bad json</script>
      </head>
      <body><h1>Hello</h1></body>
    </html>
    """
    meta = extract_seo_metadata(html, "/demo")
    assert meta["title"] == "Demo"
    assert meta["meta_description"] == "desc"
    assert meta["schema_json_ld"] is None


class _RaisingSession:
    """Session stub that raises TimeoutError with empty message."""

    def get(self, *args, **kwargs):
        raise asyncio.TimeoutError()


def test_fetch_one_returns_nonempty_error_message_on_timeout():
    semaphore = asyncio.Semaphore(1)
    path, metadata, status, _elapsed = asyncio.run(
        _fetch_one(_RaisingSession(), "https://example.com", "/demo", semaphore)
    )
    assert path == "/demo"
    assert status == 0
    assert "error" in metadata
    assert metadata["error"]
    assert "TimeoutError" in metadata["error"]
