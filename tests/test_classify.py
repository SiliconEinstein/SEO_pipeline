"""Tests for steps/_classify.py."""

import pandas as pd
import pytest

from steps._classify import discover_subtypes, find_latest_csv, get_filter_tag


# ── get_filter_tag ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"seo": {"page_filter": ""}}, "all"),
        ({}, "all"),
        ({"seo": {"page_filter": "sciencepedia"}}, "sciencepedia"),
        ({"seo": {"page_filter": "/sciencepedia/"}}, "sciencepedia"),
        ({"seo": {"page_filter": "/sciencepedia/feynman"}}, "sciencepedia_feynman"),
    ],
    ids=["empty", "no_seo_key", "simple", "strip_slashes", "nested_path"],
)
def test_get_filter_tag(config, expected):
    assert get_filter_tag(config) == expected


# ── find_latest_csv ───────────────────────────────────────────────────


def test_find_latest_csv_multiple(tmp_path):
    for name in [
        "ranking_pages_all_2026-03-01.csv",
        "ranking_pages_all_2026-03-15.csv",
        "ranking_pages_all_2026-03-10.csv",
    ]:
        (tmp_path / name).write_text("header\n")
    result = find_latest_csv(tmp_path, "ranking_pages_all_*.csv")
    assert result.name == "ranking_pages_all_2026-03-15.csv"


def test_find_latest_csv_single(tmp_path):
    (tmp_path / "ranking_pages_all_2026-03-01.csv").write_text("header\n")
    result = find_latest_csv(tmp_path, "ranking_pages_all_*.csv")
    assert result.name == "ranking_pages_all_2026-03-01.csv"


def test_find_latest_csv_no_match(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_latest_csv(tmp_path, "ranking_pages_all_*.csv")


def test_find_latest_csv_pattern_filters(tmp_path):
    (tmp_path / "ranking_pages_all_2026-03-01.csv").write_text("header\n")
    (tmp_path / "daily_pages_all_2026-03-15.csv").write_text("header\n")
    result = find_latest_csv(tmp_path, "ranking_pages_all_*.csv")
    assert result.name == "ranking_pages_all_2026-03-01.csv"


# ── discover_subtypes ─────────────────────────────────────────────────


def test_discover_subtypes_empty():
    result = discover_subtypes(pd.Series(dtype=str))
    assert result.empty


def test_discover_subtypes_standard():
    paths = pd.Series([
        "/sciencepedia/feynman/keyword/quantum",
        "/sciencepedia/feynman/classical-mechanics",
        "/sciencepedia/agent-tools/crystal",
    ])
    result = discover_subtypes(paths)
    assert list(result) == ["feynman/keyword", "feynman", "agent-tools"]


def test_discover_subtypes_en_prefix():
    paths = pd.Series([
        "/en/sciencepedia/feynman/keyword/quantum",
        "/sciencepedia/feynman/classical-mechanics",
        "/sciencepedia/agent-tools/crystal",
    ])
    result = discover_subtypes(paths)
    # /en/ stripped, same result as standard
    assert list(result) == ["feynman/keyword", "feynman", "agent-tools"]


def test_discover_subtypes_all_same_dir():
    paths = pd.Series([
        "/sciencepedia/feynman/a",
        "/sciencepedia/feynman/b",
        "/sciencepedia/feynman/c",
    ])
    result = discover_subtypes(paths)
    assert all(r == "feynman" for r in result)


def test_discover_subtypes_single_path():
    paths = pd.Series(["/sciencepedia/feynman/quantum"])
    result = discover_subtypes(paths)
    assert result.iloc[0] == "feynman"


def test_discover_subtypes_shallow():
    paths = pd.Series(["/sciencepedia/manifold"])
    result = discover_subtypes(paths)
    # 2-segment path: dir_part = "sciencepedia", single unique dir → label = last segment
    assert result.iloc[0] == "sciencepedia"


def test_discover_subtypes_empty_label_becomes_other():
    # Single-segment paths: dir_part returns the segment itself
    paths = pd.Series(["/about", "/contact"])
    result = discover_subtypes(paths)
    # Common prefix removal may leave empty strings → replaced with "other"
    for val in result:
        assert val != ""
