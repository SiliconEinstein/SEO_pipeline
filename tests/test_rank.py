"""Tests for steps/rank.py."""

import pandas as pd
import pytest

from steps.rank import (
    classify_page_type,
    compute_priority_scores,
    detect_language,
    filter_and_rank,
)


# ── classify_page_type ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "subtype, mapping, expected",
    [
        ("feynman", {"feynman": "course_article"}, "course_article"),
        ("unknown", {"feynman": "course_article"}, "other"),
        ("feynman", {}, "other"),
    ],
    ids=["hit", "miss", "empty_mapping"],
)
def test_classify_page_type(subtype, mapping, expected):
    assert classify_page_type(subtype, mapping) == expected


# ── detect_language ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/sciencepedia/feynman", "zh"),
        ("/en/sciencepedia/feynman", "en"),
        ("/", "zh"),
    ],
    ids=["zh", "en", "root"],
)
def test_detect_language(path, expected):
    assert detect_language(path) == expected


# ── compute_priority_scores ───────────────────────────────────────────


def test_compute_priority_scores():
    df = pd.DataFrame({
        "展示": [1000, 5000],
        "CTR": [0.05, 0.01],
    })
    result = compute_priority_scores(df)
    assert result["priority_score"].iloc[0] == pytest.approx(950.0)
    assert result["priority_score"].iloc[1] == pytest.approx(4950.0)


def test_compute_priority_scores_zero_ctr():
    df = pd.DataFrame({
        "展示": [3000],
        "CTR": [0.0],
    })
    result = compute_priority_scores(df)
    assert result["priority_score"].iloc[0] == pytest.approx(3000.0)


# ── filter_and_rank ───────────────────────────────────────────────────


def test_filter_and_rank_no_filter(sample_ranking_df):
    result = filter_and_rank(sample_ranking_df)
    # All rows with query_count > 0 should be included
    assert len(result) == 4
    assert "subtype" in result.columns
    assert "priority_score" in result.columns
    # Sorted by priority_score descending
    scores = result["priority_score"].tolist()
    assert scores == sorted(scores, reverse=True)


def test_filter_and_rank_with_subtype_filter(sample_ranking_df):
    result = filter_and_rank(
        sample_ranking_df,
        include_subtypes=["feynman/keyword"],
    )
    assert all(result["subtype"] == "feynman/keyword")


def test_filter_and_rank_with_page_type_mapping(sample_ranking_df):
    result = filter_and_rank(
        sample_ranking_df,
        subtype_page_types={"feynman/keyword": "keyword", "feynman": "course_article"},
    )
    # Check page types are mapped correctly
    kw_rows = result[result["subtype"] == "feynman/keyword"]
    assert all(kw_rows["seo_page_type"] == "keyword")


def test_filter_and_rank_language_detection(sample_ranking_df):
    result = filter_and_rank(sample_ranking_df)
    en_rows = result[result["路径"].str.startswith("/en/")]
    zh_rows = result[~result["路径"].str.startswith("/en/")]
    assert all(en_rows["language"] == "en")
    assert all(zh_rows["language"] == "zh")


def test_filter_and_rank_no_queries():
    """When all query_count are 0, all rows should pass filter."""
    df = pd.DataFrame({
        "路径": ["/sciencepedia/feynman/a", "/sciencepedia/feynman/b"],
        "平均排名": [5.0, 10.0],
        "展示": [1000, 2000],
        "CTR": [0.01, 0.02],
        "点击": [10, 40],
        "优先级": ["B", "C"],
        "query_count": [0, 0],
        "top_queries": [[], []],
    })
    result = filter_and_rank(df)
    assert len(result) == 2
