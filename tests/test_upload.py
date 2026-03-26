"""Tests for scripts/upload_optimized.py URL parsing and item building."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ── import upload_optimized from scripts/ ─────────────────────────────

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import upload_optimized as upload


# ── detect_language ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/en/sciencepedia/feynman/keyword/quantum", "en-US"),
        ("/sciencepedia/feynman/keyword/quantum", "zh-CN"),
        ("/en/sciencepedia/feynman/article_slug", "en-US"),
        ("/sciencepedia/feynman/article_slug", "zh-CN"),
        ("/entry", "zh-CN"),
    ],
    ids=["en_keyword", "zh_keyword", "en_article", "zh_article", "short_path"],
)
def test_detect_language(path, expected):
    assert upload.detect_language(path) == expected


# ── is_keyword_page ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/en/sciencepedia/feynman/keyword/quantum", True),
        ("/sciencepedia/feynman/keyword/liquid_mirror", True),
        ("/sciencepedia/feynman/article_slug", False),
        ("/en/sciencepedia/feynman/article_slug", False),
    ],
    ids=["en_keyword", "zh_keyword", "zh_article", "en_article"],
)
def test_is_keyword_page(path, expected):
    assert upload.is_keyword_page(path) == expected


# ── extract_keyword_id ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/en/sciencepedia/feynman/keyword/liquid_mirror_telescope", "liquid_mirror_telescope"),
        ("/sciencepedia/feynman/keyword/quantum", "quantum"),
        ("/sciencepedia/feynman/keyword/a-b-c", "a-b-c"),
    ],
    ids=["en", "zh", "hyphens"],
)
def test_extract_keyword_id(path, expected):
    assert upload.extract_keyword_id(path) == expected


def test_extract_keyword_id_invalid():
    with pytest.raises(ValueError, match="keyword_id"):
        upload.extract_keyword_id("/sciencepedia/feynman/article_slug")


# ── extract_entry_id ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path, expected",
    [
        (
            "/sciencepedia/feynman/principles_of_genetics_graduate-multiple_alleles",
            "principles_of_genetics_graduate-multiple_alleles",
        ),
        ("/en/sciencepedia/feynman/quantum_field_theory", "quantum_field_theory"),
    ],
    ids=["zh", "en"],
)
def test_extract_entry_id(path, expected):
    assert upload.extract_entry_id(path) == expected


def test_extract_entry_id_invalid():
    with pytest.raises(ValueError, match="entry_id"):
        upload.extract_entry_id("/sciencepedia/other/article_slug")


# ── build_items (keyword pages — no API call) ────────────────────────


def test_build_items_keyword_only():
    metadata = {
        "/en/sciencepedia/feynman/keyword/quantum": {
            "title": "Quantum | Brand",
            "meta_description": "Quantum desc",
        },
        "/sciencepedia/feynman/keyword/gravity": {
            "title": "Gravity",
            "meta_description": "Gravity desc",
        },
    }
    items, errors = upload.build_items(metadata, "https://api.example.com")
    assert len(errors) == 0
    assert len(items) == 2
    kw_ids = {it["keyword_id"] for it in items}
    assert kw_ids == {"quantum", "gravity"}
    # Check common fields
    for it in items:
        assert it["style"] == "Feynman"
        assert "seo_title" in it
        assert "seo_description" in it
        assert "node_id" not in it


# ── build_items (article pages — mock node_id lookup) ────────────────


def test_build_items_article_with_mock():
    metadata = {
        "/sciencepedia/feynman/slug_a": {
            "title": "Title A",
            "meta_description": "Desc A",
        },
    }

    def fake_fetch_node_id(api_base, entry_id, language):
        return f"node_{entry_id}"

    with patch.object(upload, "fetch_node_id", side_effect=fake_fetch_node_id):
        items, errors = upload.build_items(metadata, "https://api.example.com")

    assert len(errors) == 0
    assert len(items) == 1
    assert items[0]["node_id"] == "node_slug_a"
    assert items[0]["language"] == "zh-CN"
    assert "keyword_id" not in items[0]


def test_build_items_article_node_id_failure():
    metadata = {
        "/sciencepedia/feynman/slug_fail": {
            "title": "Title",
            "meta_description": "Desc",
        },
    }

    with patch.object(
        upload, "fetch_node_id", side_effect=RuntimeError("API error")
    ):
        items, errors = upload.build_items(metadata, "https://api.example.com")

    assert len(items) == 0
    assert len(errors) == 1
    assert "node_id lookup failed" in errors[0]


# ── build_items (mixed pages) ────────────────────────────────────────


def test_build_items_mixed():
    metadata = {
        "/en/sciencepedia/feynman/keyword/k1": {
            "title": "K1",
            "meta_description": "K1 desc",
        },
        "/sciencepedia/feynman/article_1": {
            "title": "A1",
            "meta_description": "A1 desc",
        },
    }

    with patch.object(upload, "fetch_node_id", return_value="node_123"):
        items, errors = upload.build_items(metadata, "https://api.example.com")

    assert len(errors) == 0
    assert len(items) == 2
    kw_item = next(it for it in items if "keyword_id" in it)
    art_item = next(it for it in items if "node_id" in it)
    assert kw_item["keyword_id"] == "k1"
    assert kw_item["language"] == "en-US"
    assert art_item["node_id"] == "node_123"
    assert art_item["language"] == "zh-CN"


# ── main() CLI ────────────────────────────────────────────────────────


def test_main_missing_args():
    assert upload.main(["prog"]) == 2


def test_main_no_api_base(tmp_path, monkeypatch):
    monkeypatch.delenv("SEO_UPLOAD_API_BASE", raising=False)
    seo_dir = tmp_path / "seo"
    seo_dir.mkdir()
    (seo_dir / "optimized_metadata.json").write_text("{}")
    assert upload.main(["prog", str(tmp_path), "run1", "30"]) == 1


def test_main_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SEO_UPLOAD_API_BASE", "https://api.example.com")
    assert upload.main(["prog", str(tmp_path), "run1", "30"]) == 1


def test_main_empty_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("SEO_UPLOAD_API_BASE", "https://api.example.com")
    seo_dir = tmp_path / "seo"
    seo_dir.mkdir()
    (seo_dir / "optimized_metadata.json").write_text("{}")
    assert upload.main(["prog", str(tmp_path), "run1", "30"]) == 0


def test_main_success(tmp_path, monkeypatch):
    monkeypatch.setenv("SEO_UPLOAD_API_BASE", "https://api.example.com")
    seo_dir = tmp_path / "seo"
    seo_dir.mkdir()
    metadata = {
        "/en/sciencepedia/feynman/keyword/q": {
            "title": "T",
            "meta_description": "D",
        },
    }
    (seo_dir / "optimized_metadata.json").write_text(json.dumps(metadata))

    with patch.object(upload, "batch_update", return_value={"code": 0}):
        assert upload.main(["prog", str(tmp_path), "run1", "30"]) == 0


def test_main_batch_update_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("SEO_UPLOAD_API_BASE", "https://api.example.com")
    seo_dir = tmp_path / "seo"
    seo_dir.mkdir()
    metadata = {
        "/en/sciencepedia/feynman/keyword/q": {
            "title": "T",
            "meta_description": "D",
        },
    }
    (seo_dir / "optimized_metadata.json").write_text(json.dumps(metadata))

    with patch.object(
        upload, "batch_update", side_effect=RuntimeError("API down")
    ):
        assert upload.main(["prog", str(tmp_path), "run1", "30"]) == 1


def test_main_partial_failure_returns_nonzero(tmp_path, monkeypatch):
    """When some pages are skipped (errors), main() must return 1 even if
    batch_update succeeds — contract: exit 0 only when fully completed."""
    monkeypatch.setenv("SEO_UPLOAD_API_BASE", "https://api.example.com")
    seo_dir = tmp_path / "seo"
    seo_dir.mkdir()
    metadata = {
        # This keyword page will succeed
        "/en/sciencepedia/feynman/keyword/ok": {
            "title": "OK",
            "meta_description": "OK desc",
        },
        # This article page will fail node_id lookup
        "/sciencepedia/feynman/will_fail": {
            "title": "Fail",
            "meta_description": "Fail desc",
        },
    }
    (seo_dir / "optimized_metadata.json").write_text(json.dumps(metadata))

    with patch.object(
        upload, "fetch_node_id", side_effect=RuntimeError("not found")
    ), patch.object(upload, "batch_update") as mock_batch:
        result = upload.main(["prog", str(tmp_path), "run1", "30"])

    # batch_update still called for the successful keyword page
    mock_batch.assert_called_once()
    # But exit code is 1 because one page was skipped
    assert result == 1


# ── batch_update chunking ────────────────────────────────────────────


def test_batch_update_chunks(monkeypatch):
    """batch_update splits items into chunks of chunk_size."""
    calls = []

    def fake_api_post(api_base, endpoint, body):
        calls.append(body["items"])
        return {"code": 0}

    monkeypatch.setattr(upload, "_api_post", fake_api_post)

    items = [{"id": i} for i in range(120)]
    upload.batch_update("https://api.example.com", items, chunk_size=50)

    assert len(calls) == 3
    assert len(calls[0]) == 50
    assert len(calls[1]) == 50
    assert len(calls[2]) == 20
    # Server contract: each item in payload must be a JSON string.
    assert all(isinstance(x, str) for batch in calls for x in batch)


def test_batch_update_raises_on_partial_failure(monkeypatch):
    """batch_update must fail when API reports failed_count > 0."""

    def fake_api_post(api_base, endpoint, body):
        return {"code": 0, "data": {"success_count": 0, "failed_count": 1}}

    monkeypatch.setattr(upload, "_api_post", fake_api_post)

    with pytest.raises(RuntimeError, match="partial failure"):
        upload.batch_update("https://api.example.com", [{"id": 1}], chunk_size=50)
