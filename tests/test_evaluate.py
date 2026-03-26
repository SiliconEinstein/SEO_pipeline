"""Tests for steps/evaluate.py trend computation."""

import pandas as pd
import pytest

from steps.evaluate import _compute_trends


def test_compute_trends_basic(sample_daily_df):
    result = _compute_trends(sample_daily_df)
    assert "overall" in result
    assert "by_subtype" in result
    # 20 days of data
    assert len(result["overall"]["dates"]) == 20


def test_compute_trends_weighted_ctr(sample_daily_df):
    result = _compute_trends(sample_daily_df)
    # Each day: 2 paths × 10 clicks = 20 clicks, 2 × 500 = 1000 impressions
    # Weighted CTR = 20/1000 = 0.02
    for ctr in result["overall"]["avg_ctr"]:
        assert ctr == pytest.approx(0.02, abs=1e-4)


def test_compute_trends_per_subtype(sample_daily_df):
    result = _compute_trends(sample_daily_df)
    # Both paths share the same subtype "feynman"
    assert "feynman" in result["by_subtype"]


def test_compute_trends_optimized_board(sample_daily_df):
    opt_paths = {"/sciencepedia/feynman/quantum"}
    result = _compute_trends(sample_daily_df, optimized_paths=opt_paths)
    assert "_optimized_" in result["by_subtype"]
    # Optimized board should have only 1 path's data per day
    for count in result["by_subtype"]["_optimized_"]["page_count"]:
        assert count == 1


def test_compute_trends_no_optimized_paths(sample_daily_df):
    result = _compute_trends(sample_daily_df, optimized_paths=None)
    assert "_optimized_" not in result["by_subtype"]


def test_compute_trends_empty_optimized_set(sample_daily_df):
    result = _compute_trends(sample_daily_df, optimized_paths=set())
    assert "_optimized_" not in result["by_subtype"]


def test_compute_trends_clicks_impressions(sample_daily_df):
    result = _compute_trends(sample_daily_df)
    # Each day: 2 paths × 10 clicks = 20
    for clicks in result["overall"]["clicks"]:
        assert clicks == 20
    # Each day: 2 paths × 500 = 1000
    for imp in result["overall"]["impressions"]:
        assert imp == 1000
