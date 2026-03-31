"""Tests for steps/optimize.py post-processing functions."""

import json

import pytest

import steps._lance as lance_mod
from steps.optimize import (
    _enhance_schema,
    _ensure_brand_suffix,
    _extract_json,
    _is_content_schema,
    _parse_range,
    _postprocess_all,
    _postprocess_page,
    _smart_truncate,
)


# ── _parse_range ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "range_str, top, expected",
    [
        (None, 30, (0, 30)),
        ("31-60", 30, (30, 60)),
        ("1-10", 30, (0, 10)),
    ],
    ids=["none", "range_31_60", "range_1_10"],
)
def test_parse_range(range_str, top, expected):
    assert _parse_range(range_str, top) == expected


@pytest.mark.parametrize(
    "range_str",
    ["31_60", "31"],
    ids=["underscore", "single_number"],
)
def test_parse_range_invalid(range_str):
    with pytest.raises(ValueError):
        _parse_range(range_str, 30)


# ── _extract_json ─────────────────────────────────────────────────────


def test_extract_json_markdown_fence():
    text = '```json\n{"title": "Hello"}\n```'
    assert _extract_json(text) == {"title": "Hello"}


def test_extract_json_bare():
    text = '{"title": "Hello"}'
    assert _extract_json(text) == {"title": "Hello"}


def test_extract_json_with_prose():
    text = 'Here is the result: {"title": "Hello"} Hope this helps!'
    assert _extract_json(text) == {"title": "Hello"}


def test_extract_json_fence_no_tag():
    text = '```\n{"title": "Hello"}\n```'
    assert _extract_json(text) == {"title": "Hello"}


def test_extract_json_invalid():
    with pytest.raises(json.JSONDecodeError):
        _extract_json("not json at all")


def test_extract_json_nested_braces():
    text = '{"title": "Hello {world}"}'
    result = _extract_json(text)
    assert result["title"] == "Hello {world}"


# ── _smart_truncate ───────────────────────────────────────────────────


def test_smart_truncate_under_limit():
    assert _smart_truncate("Short text", 100, "en") == "Short text"


def test_smart_truncate_english_period():
    text = "First sentence. Second sentence. Third long sentence that pushes us over the limit."
    result = _smart_truncate(text, 40, "en")
    assert len(result) <= 40
    assert result.endswith("sentence")  # truncated at period boundary


def test_smart_truncate_english_space():
    text = "word1 word2 word3 word4 word5 word6 word7 word8 word9 word10"
    result = _smart_truncate(text, 30, "en")
    assert len(result) <= 30
    assert " " not in result[-1:]  # no trailing space


def test_smart_truncate_chinese():
    text = "第一句话。第二句话。第三句很长的话来测试截断的效果如何"
    result = _smart_truncate(text, 12, "zh")
    assert len(result) <= 12


def test_smart_truncate_no_boundary():
    text = "a" * 50
    result = _smart_truncate(text, 20, "en")
    assert len(result) <= 20


def test_smart_truncate_exact_limit():
    text = "Exactly fits"
    assert _smart_truncate(text, len(text), "en") == text


def test_smart_truncate_removes_dangling_connector():
    text = "Liquid Mirror Telescope: Focal Length & Rotation"
    result = _smart_truncate(text, 45, "en")
    assert result == "Liquid Mirror Telescope: Focal Length"


# ── _ensure_brand_suffix ──────────────────────────────────────────────


def test_ensure_brand_suffix_already_has():
    result = _ensure_brand_suffix("My Title | Brand", "en", " | Brand", 60)
    assert result == "My Title | Brand"


def test_ensure_brand_suffix_missing():
    result = _ensure_brand_suffix("My Title", "en", " | Brand", 60)
    assert result == "My Title | Brand"


def test_ensure_brand_suffix_too_long():
    long_title = "A very long title that needs truncation for SEO purposes"
    result = _ensure_brand_suffix(long_title, "en", " | Brand", 60)
    assert result.endswith(" | Brand")
    assert len(result) <= 60


def test_ensure_brand_suffix_duplicate():
    # If title already ends with brand suffix, function returns as-is (no dedup)
    result = _ensure_brand_suffix("Title | Brand extra | Brand", "en", " | Brand", 60)
    assert result.endswith(" | Brand")
    # Suffix not at end → replace all occurrences, append once
    result2 = _ensure_brand_suffix("Title | Brand extra text", "en", " | Brand", 60)
    assert result2.endswith(" | Brand")
    assert result2 == "Title extra text | Brand"


def test_ensure_brand_suffix_replaces_legacy_brand():
    result = _ensure_brand_suffix(
        "Liquid Mirror Telescope | Bohrium",
        "en",
        " | SciencePedia",
        60,
    )
    assert result == "Liquid Mirror Telescope | SciencePedia"


# ── _is_content_schema ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "schema, expected",
    [
        ({"@type": "Article"}, True),
        ({"@type": "LearningResource"}, True),
        ({"@type": "WebPage"}, True),
        ({"@type": "BreadcrumbList"}, False),
        ({"@type": "Organization"}, False),
        ({"@type": ["Article", "WebPage"]}, True),
        ({"@type": ["BreadcrumbList", "ItemList"]}, False),
        ({}, False),
    ],
    ids=[
        "article", "learning_resource", "webpage",
        "breadcrumb", "organization",
        "list_content", "list_structural", "no_type",
    ],
)
def test_is_content_schema(schema, expected):
    assert _is_content_schema(schema) == expected


# ── _enhance_schema ───────────────────────────────────────────────────


def test_enhance_schema_skips_structural():
    schemas = [
        {"@type": "BreadcrumbList", "name": "nav"},
        {"@type": "Article", "headline": "old", "description": "old"},
    ]
    result = _enhance_schema(schemas, "new headline", "new desc", "/path", "other")
    assert result[0]["name"] == "nav"  # BreadcrumbList untouched
    assert result[1]["headline"] == "new headline"
    assert result[1]["description"] == "new desc"


def test_enhance_schema_adds_learning_resource():
    schemas = [{"@type": "Article", "headline": "old"}]
    result = _enhance_schema(schemas, "headline", "desc", "/path", "course_article")
    assert "LearningResource" in result[0]["@type"]


def test_enhance_schema_no_duplicate_lr():
    schemas = [{"@type": ["Article", "LearningResource"], "headline": "old"}]
    result = _enhance_schema(schemas, "headline", "desc", "/path", "course_article")
    assert result[0]["@type"].count("LearningResource") == 1


def test_enhance_schema_adds_defined_term():
    schemas = [{"@type": "Article", "headline": "old"}]
    rewrite = {"schema_term_name": "Quantum", "schema_subject": "Physics"}
    result = _enhance_schema(schemas, "headline", "desc", "/path", "keyword", rewrite)
    assert result[0]["about"]["@type"] == "DefinedTerm"
    assert result[0]["about"]["name"] == "Quantum"


def test_enhance_schema_adds_is_part_of():
    schemas = [{"@type": "Article", "headline": "old"}]
    rewrite = {"schema_course_name": "Feynman Lectures"}
    result = _enhance_schema(schemas, "headline", "desc", "/path", "course_article", rewrite)
    assert result[0]["isPartOf"]["@type"] == "Course"
    assert result[0]["isPartOf"]["name"] == "Feynman Lectures"


def test_enhance_schema_preserves_date_modified():
    schemas = [{"@type": "Article", "dateModified": "2026-01-01", "headline": "old"}]
    result = _enhance_schema(schemas, "new", "desc", "/path", "other")
    assert result[0]["dateModified"] == "2026-01-01"


def test_enhance_schema_empty_list():
    assert _enhance_schema([], "headline", "desc", "/path", "other") == []


# ── _postprocess_page ─────────────────────────────────────────────────


def _make_orig():
    return {
        "title": "Old Title",
        "meta_description": "Old desc",
        "og_title": "Old OG",
        "og_description": "Old OG desc",
        "og_url": "",
        "og_type": "website",
        "twitter_title": "Old TW",
        "twitter_description": "Old TW desc",
        "meta_keywords": "old,keywords",
        "schema_json_ld": [{"@type": "Article", "headline": "Old"}],
        "canonical": "https://example.com/page",
    }


def test_postprocess_page_happy_path():
    rewrite = {"title": "New Title", "meta_description": "New description for the page"}
    ctx = {"language": "en", "page_type": "other", "top_queries": []}
    seo_config = {"base_url": "https://www.example.com", "brand_suffix": " | Brand"}
    opt, stats = _postprocess_page("/page", rewrite, _make_orig(), ctx, seo_config)
    assert opt["title"].endswith(" | Brand")
    assert opt["og_title"] == opt["title"]
    assert opt["twitter_title"] == opt["title"]
    assert opt["og_description"] == opt["meta_description"]
    assert opt["meta_keywords"] == ""
    assert opt["og_url"] == "https://www.example.com/page"
    assert opt["canonical"] == "https://example.com/page"  # preserved from orig


def test_postprocess_page_og_type_preserved():
    rewrite = {"title": "Title", "meta_description": "Desc"}
    ctx = {"language": "en"}
    seo_config = {"base_url": "https://example.com", "brand_suffix": " | B"}
    orig = _make_orig()
    orig["og_type"] = "website"
    opt, _ = _postprocess_page("/p", rewrite, orig, ctx, seo_config)
    assert opt["og_type"] == "website"


def test_postprocess_page_og_type_default():
    rewrite = {"title": "Title", "meta_description": "Desc"}
    ctx = {"language": "en"}
    seo_config = {"base_url": "https://example.com", "brand_suffix": " | B"}
    orig = _make_orig()
    orig.pop("og_type", None)
    opt, _ = _postprocess_page("/p", rewrite, orig, ctx, seo_config)
    assert opt["og_type"] == "article"


def test_postprocess_page_missing_base_url():
    with pytest.raises(ValueError, match="base_url"):
        _postprocess_page("/p", {"title": "T", "meta_description": "D"}, _make_orig(), {}, {})


def test_postprocess_page_keeps_original_title_without_title_issues():
    rewrite = {"title": "New Optimized Title", "meta_description": "New desc"}
    ctx = {"language": "en", "issues": ["desc_too_long"]}
    seo_config = {"base_url": "https://example.com", "brand_suffix": " | Brand"}
    opt, _ = _postprocess_page("/p", rewrite, _make_orig(), ctx, seo_config)
    assert opt["title"] == "Old Title | Brand"


def test_postprocess_page_uses_rewrite_title_with_title_issue():
    rewrite = {"title": "New Optimized Title", "meta_description": "New desc"}
    ctx = {"language": "en", "issues": ["title_too_long"]}
    seo_config = {"base_url": "https://example.com", "brand_suffix": " | Brand"}
    opt, _ = _postprocess_page("/p", rewrite, _make_orig(), ctx, seo_config)
    assert opt["title"] == "New Optimized Title | Brand"


def test_postprocess_all_overwrites_existing_optimized_file(tmp_path):
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    output_dir = tmp_path / "out"
    (output_dir / "seo").mkdir(parents=True)

    # Pre-existing optimized output should be overwritten, not merged.
    (output_dir / "seo" / "optimized_metadata.json").write_text(
        json.dumps({"/old": {"title": "Old", "meta_description": "Old"}}),
        encoding="utf-8",
    )

    contexts = [{
        "path": "/new",
        "issues": [],
        "priority_score": 1.0,
        "subtype": "sciencepedia/feynman",
        "language": "en",
    }]
    original_metadata = {
        "/new": {
            "title": "Old New",
            "meta_description": "Old New Desc",
            "schema_json_ld": [{"@type": "Article", "headline": "old"}],
        }
    }
    rewritten = {
        "/new": {
            "title": "New Title",
            "meta_description": "New Description",
        }
    }

    (tmp_dir / "seo_rewrite_contexts.json").write_text(
        json.dumps(contexts, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_dir / "seo_original_metadata.json").write_text(
        json.dumps(original_metadata, ensure_ascii=False), encoding="utf-8"
    )

    _postprocess_all(
        rewritten,
        str(tmp_dir),
        {"base_url": "https://example.com", "brand_suffix": " | Brand"},
        output_dir,
        config={"lance": {"enabled": False}},
        prompt_template="prompt",
    )

    written = json.loads(
        (output_dir / "seo" / "optimized_metadata.json").read_text(encoding="utf-8")
    )
    assert "/old" not in written
    assert "/new" in written


def test_postprocess_all_lance_does_not_store_original_fields(tmp_path, monkeypatch):
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    contexts = [{
        "path": "/p",
        "issues": ["desc_too_long"],
        "priority_score": 10.0,
        "subtype": "sciencepedia/feynman",
    }]
    original_metadata = {
        "/p": {
            "title": "Old Title",
            "meta_description": "Old Description",
            "schema_json_ld": [{"@type": "Article", "headline": "old"}],
        }
    }
    rewritten = {
        "/p": {
            "title": "New Title",
            "meta_description": "New Description",
        }
    }

    (tmp_dir / "seo_rewrite_contexts.json").write_text(
        json.dumps(contexts, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_dir / "seo_original_metadata.json").write_text(
        json.dumps(original_metadata, ensure_ascii=False), encoding="utf-8"
    )

    captured = {}

    class _FakeStore:
        def __init__(self, _cfg):
            pass

        def save_prompt_template(self, _content):
            return "hash123"

        def record_optimizations(self, records):
            captured["records"] = records

    monkeypatch.setattr(lance_mod, "LanceStore", _FakeStore)

    _postprocess_all(
        rewritten,
        str(tmp_dir),
        {"base_url": "https://example.com", "brand_suffix": " | Brand"},
        output_dir,
        config={"lance": {"enabled": True}, "optimize": {"model": "m"}},
        prompt_template="prompt",
    )

    records = captured["records"]
    assert len(records) == 1
    rec = records[0]
    assert "original_title" not in rec
    assert "original_description" not in rec
    assert "original_schema_json_ld" not in rec
