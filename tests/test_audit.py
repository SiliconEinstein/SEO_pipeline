"""Tests for steps/audit.py detection rules."""

import pytest

from steps.audit import (
    _check_generic_opening,
    _check_keyword_coverage,
    _check_language_mismatch,
    _check_schema_completeness,
)


# ── _check_generic_opening ────────────────────────────────────────────


@pytest.mark.parametrize(
    "desc, expected",
    [
        ("Explore the world of quantum", "Explore"),
        ("探索量子世界", "探索"),
        ("Learn about machine learning", "Learn"),
        ("Quantum computing explained", None),
        ("", None),
        ("explore the world", None),  # lowercase 'e', list has "Explore "
        ("We Explore science", None),  # not at start
    ],
    ids=["en_explore", "zh_explore", "en_learn", "no_match", "empty", "lowercase", "mid_text"],
)
def test_check_generic_opening(desc, expected):
    assert _check_generic_opening(desc) == expected


# ── _check_language_mismatch ──────────────────────────────────────────


@pytest.mark.parametrize(
    "path, title, desc, expected",
    [
        ("/sciencepedia/quantum", "Quantum Physics", "A deep dive", True),
        ("/sciencepedia/quantum", "量子物理", "深入探索", False),
        ("/en/sciencepedia/quantum", "Quantum Physics", "A deep dive", False),
        ("/sciencepedia/quantum", "Quantum 量子", "English desc", False),
        ("/sciencepedia/quantum", "", "", True),
    ],
    ids=["zh_path_en_content", "zh_path_zh_content", "en_path_skip", "mixed_lang", "empty_both"],
)
def test_check_language_mismatch(path, title, desc, expected):
    assert _check_language_mismatch(path, title, desc) == expected


# ── _check_keyword_coverage ───────────────────────────────────────────


def test_keyword_coverage_all_covered():
    title = "Quantum physics explained"
    desc = "Learn about quantum physics and mechanics"
    queries = [{"query": "quantum physics", "impressions": 100}]
    assert _check_keyword_coverage(title, desc, queries) == []


def test_keyword_coverage_not_covered():
    title = "Biology intro"
    desc = "A short intro"
    queries = [{"query": "quantum physics mechanics", "impressions": 100}]
    missing = _check_keyword_coverage(title, desc, queries)
    assert "quantum physics mechanics" in missing


def test_keyword_coverage_60pct_boundary():
    # 3/5 words matched = 60%, threshold is < 0.6 so this passes
    title = "word1 word2 word3"
    desc = "some description"
    queries = [{"query": "word1 word2 word3 other1 other2", "impressions": 100}]
    assert _check_keyword_coverage(title, desc, queries) == []


def test_keyword_coverage_below_60pct():
    # 2/5 words matched = 40%, below threshold
    title = "word1 word2"
    desc = "some description"
    queries = [{"query": "word1 word2 other1 other2 other3", "impressions": 100}]
    missing = _check_keyword_coverage(title, desc, queries)
    assert len(missing) == 1


def test_keyword_coverage_empty_queries():
    assert _check_keyword_coverage("title", "desc", []) == []


def test_keyword_coverage_top_k_limit():
    queries = [
        {"query": f"query{i}", "impressions": 100 - i}
        for i in range(5)
    ]
    # Only top 3 are checked
    missing = _check_keyword_coverage("no match here", "nothing", queries)
    assert len(missing) == 3


def test_keyword_coverage_case_insensitive():
    title = "QUANTUM PHYSICS"
    desc = "description"
    queries = [{"query": "quantum physics", "impressions": 100}]
    assert _check_keyword_coverage(title, desc, queries) == []


# ── _check_schema_completeness ────────────────────────────────────────


@pytest.mark.parametrize(
    "schemas, page_type, expected",
    [
        ([], "other", ["no_schema"]),
        ([{"@type": "Article"}], "other", ["missing_datePublished", "missing_dateModified"]),
        (
            [{"@type": "Article", "datePublished": "2026-01-01", "dateModified": "2026-03-01"}],
            "other",
            [],
        ),
        (
            [{"@type": "Article", "datePublished": "x", "dateModified": "x"}],
            "course_article",
            ["course_missing_LearningResource"],
        ),
        (
            [{"@type": ["Article", "LearningResource"], "datePublished": "x", "dateModified": "x"}],
            "course_article",
            [],
        ),
        ([{"@type": "BreadcrumbList"}], "other", []),
        ([{"@type": "Course"}], "course_article", []),
        (
            [{"@type": ["Article", "WebPage"]}],
            "other",
            ["missing_datePublished", "missing_dateModified"],
        ),
    ],
    ids=[
        "no_schema",
        "article_missing_dates",
        "article_with_dates",
        "course_missing_lr",
        "course_with_lr",
        "breadcrumb_only",
        "course_type_counts",
        "type_list_article",
    ],
)
def test_check_schema_completeness(schemas, page_type, expected):
    assert _check_schema_completeness(schemas, page_type) == expected
