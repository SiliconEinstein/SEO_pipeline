"""Tests for steps/fetch_gsc.py pure functions."""

import pytest

from steps.fetch_gsc import _rank_bin, _site_url_to_origin, classify_page_type


# ── _site_url_to_origin ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "site_url, base_url, expected",
    [
        ("sc-domain:example.com", "https://www.example.com", "https://www.example.com"),
        ("sc-domain:example.com", "https://www.example.com/", "https://www.example.com"),
        ("sc-domain:example.com", "", "https://example.com"),
        ("https://example.com/", "", "https://example.com"),
        ("https://wrong.com/", "https://correct.com", "https://correct.com"),
    ],
    ids=["base_url_wins", "trailing_slash", "sc_domain_no_base", "https_url", "base_overrides"],
)
def test_site_url_to_origin(site_url, base_url, expected):
    assert _site_url_to_origin(site_url, base_url) == expected


# ── classify_page_type ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/paper-details/12345", "Paper"),
        ("/en/paper-details/12345", "Paper"),
        ("/sciencepedia/feynman/quantum", "Sciencepedia"),
        ("/scholar/john-doe", "Scholar"),
        ("/apps/myapp", "Apps"),
        ("/blog/post-1", "Blog"),
        ("/notebooks/nb-1", "Notebooks"),
        ("/intro", "Intro"),
        ("/", "Homepage"),
        ("", "Homepage"),
        ("/en", "Homepage"),
        ("/unknown/page", "Other"),
    ],
    ids=[
        "paper", "paper_en", "sciencepedia", "scholar",
        "apps", "blog", "notebooks", "intro",
        "homepage_slash", "homepage_empty", "homepage_en", "other",
    ],
)
def test_classify_page_type(path, expected):
    assert classify_page_type(path) == expected


# ── _rank_bin ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "position, expected",
    [
        (0.0, "1-3 (首页顶部)"),
        (1.0, "1-3 (首页顶部)"),
        (2.9, "1-3 (首页顶部)"),
        (3.0, "4-5"),
        (4.9, "4-5"),
        (5.0, "6-10 (首页底部)"),
        (10.0, "11-20 (第2页)"),
        (20.0, "21-50"),
        (50.0, "50+"),
        (100.0, "50+"),
    ],
    ids=[
        "zero", "one", "near3", "exact3", "near5",
        "exact5", "exact10", "exact20", "exact50", "high",
    ],
)
def test_rank_bin(position, expected):
    assert _rank_bin(position) == expected
