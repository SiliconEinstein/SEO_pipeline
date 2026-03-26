"""Tests for steps/optimize.py post-processing functions."""

import json

import pytest

from steps.optimize import (
    _enhance_schema,
    _ensure_brand_suffix,
    _extract_json,
    _is_content_schema,
    _merge_with_existing,
    _parse_range,
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


# ── _merge_with_existing ──────────────────────────────────────────────


def test_merge_no_existing(tmp_path):
    path = str(tmp_path / "out.json")
    new = {"a": 1, "b": 2}
    assert _merge_with_existing(path, new) == new


def test_merge_with_existing(tmp_path):
    path = str(tmp_path / "out.json")
    with open(path, "w") as f:
        json.dump({"a": 1, "old": 99}, f)
    new = {"a": 2, "b": 3}
    result = _merge_with_existing(path, new)
    assert result == {"a": 2, "old": 99, "b": 3}
