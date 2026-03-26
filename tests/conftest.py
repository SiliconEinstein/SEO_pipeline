"""Shared fixtures for SEO pipeline tests."""

import matplotlib
matplotlib.use("Agg")

import pandas as pd
import pytest


@pytest.fixture
def sample_config():
    """Minimal valid config dict."""
    return {
        "site_url": "sc-domain:example.com",
        "credentials_file": "client_secret_test.json",
        "date_range": "28d",
        "seo": {
            "base_url": "https://www.example.com",
            "brand_suffix": " | TestBrand",
            "max_title_length": 60,
            "max_desc_length": 155,
            "crawl_concurrency": 5,
            "page_filter": "",
            "exclude_patterns": [],
            "include_subtypes": [],
            "subtype_page_types": {},
        },
        "optimize": {
            "model": "test-model",
            "top": 30,
            "batch_size": 10,
        },
        "lance": {
            "enabled": False,
        },
        "output_dir": "output",
    }


@pytest.fixture
def sample_ranking_df():
    """Simulated rank output DataFrame (4 rows, zh + en paths)."""
    return pd.DataFrame({
        "路径": [
            "/sciencepedia/feynman/keyword/quantum",
            "/sciencepedia/feynman/classical-mechanics",
            "/sciencepedia/agent-tools/crystal",
            "/en/sciencepedia/feynman/keyword/relativity",
        ],
        "平均排名": [5.2, 12.3, 8.1, 3.5],
        "展示": [5000, 12000, 3000, 8000],
        "CTR": [0.02, 0.003, 0.01, 0.05],
        "点击": [100, 36, 30, 400],
        "优先级": ["B-首页可优化", "C-差一点上首页", "B-首页可优化", "A-已在顶部"],
        "query_count": [5, 3, 2, 7],
        "top_queries": [
            [{"查询词": "quantum", "展示": 1000, "排名": 5.0}],
            [{"查询词": "classical", "展示": 500, "排名": 12.0}],
            [],
            [{"查询词": "relativity", "展示": 2000, "排名": 3.0}],
        ],
    })


@pytest.fixture
def sample_daily_df():
    """Simulated daily_pages CSV DataFrame (2 paths x 20 days)."""
    dates = pd.date_range("2026-03-01", periods=20, freq="D")
    rows = []
    paths = ["/sciencepedia/feynman/quantum", "/sciencepedia/feynman/relativity"]
    for d in dates:
        for p in paths:
            rows.append({
                "日期": d.strftime("%Y-%m-%d"),
                "路径": p,
                "点击": 10,
                "展示": 500,
                "CTR": 0.02,
                "平均排名": 7.5,
                "页面类型": "Sciencepedia",
            })
    return pd.DataFrame(rows)
