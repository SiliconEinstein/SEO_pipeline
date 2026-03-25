"""Shared utilities for analyze, rank, and audit steps."""

import glob
import os
import re
from pathlib import Path

import pandas as pd


def get_filter_tag(config: dict) -> str:
    """Derive the filename filter tag from config, matching fetch_gsc logic."""
    page_filter = config.get("seo", {}).get("page_filter", "")
    return page_filter.strip("/").replace("/", "_") if page_filter else "all"


def find_latest_csv(directory: Path, pattern: str) -> Path:
    """Find the most recent CSV matching *pattern* inside *directory*.

    Files are assumed to contain a date component in the name; the
    lexicographically last match is treated as the newest file.
    """
    matches = sorted(glob.glob(str(directory / pattern)))
    if not matches:
        raise FileNotFoundError(
            f"No CSV files matching '{pattern}' found in {directory}"
        )
    return Path(matches[-1])


def discover_subtypes(paths: pd.Series) -> pd.Series:
    """Auto-discover page subtypes from URL path structure.

    Algorithm:
        1. Normalize: strip ``/en/`` prefix
        2. Extract directory: drop last path segment (the page slug)
        3. Find longest common prefix across all directories
        4. Strip common prefix → remaining path = subtype label

    Example::

        /sciencepedia/feynman/keyword/quantum   → feynman/keyword
        /sciencepedia/feynman/classical-mechanics → feynman
        /sciencepedia/agent-tools/crystal        → agent-tools

    Args:
        paths: Series of URL paths (e.g. ``/sciencepedia/feynman/keyword/xxx``).

    Returns:
        Series of subtype labels, same index as *paths*.
    """
    if paths.empty:
        return pd.Series(dtype=str)

    # 1. Normalize: strip /en/ prefix for consistent classification
    normalized = paths.str.replace(r"^/en/", "/", regex=True)

    # 2. Extract directory part (drop last segment = slug)
    def _dir_part(p: str) -> str:
        parts = [s for s in p.strip("/").split("/") if s]
        if len(parts) <= 1:
            return parts[0] if parts else ""
        return "/".join(parts[:-1])

    dirs = normalized.apply(_dir_part)

    # 3. Find longest common prefix (at segment boundary)
    unique_dirs = dirs.unique().tolist()
    if len(unique_dirs) == 1:
        # All pages share the same directory — use the last segment as label
        # e.g. all paths are /sciencepedia/feynman/xxx → subtype = "feynman"
        common = unique_dirs[0]
        parent = "/".join(common.split("/")[:-1]) if "/" in common else ""
    else:
        raw_common = os.path.commonprefix(unique_dirs)
        # Snap to segment boundary
        if raw_common and not raw_common.endswith("/"):
            parent = raw_common.rsplit("/", 1)[0] if "/" in raw_common else ""
        else:
            parent = raw_common.rstrip("/")

    # 4. Strip common prefix to get subtype label
    if parent:
        prefix_pattern = re.escape(parent) + r"/?"
        subtypes = dirs.str.replace(f"^{prefix_pattern}", "", regex=True)
    else:
        subtypes = dirs

    # Empty labels → "other"
    subtypes = subtypes.replace("", "other")

    return subtypes
