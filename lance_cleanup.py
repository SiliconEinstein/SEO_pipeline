#!/usr/bin/env python3
"""Lance on TOS 数据维护工具

用法:
    uv run python lance_cleanup.py purge    # 清空所有数据，保留空表结构
    uv run python lance_cleanup.py compact  # 清理旧版本文件，释放 TOS 存储空间
    uv run python lance_cleanup.py stats    # 查看表状态（行数、版本数）
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

import lance
import pyarrow as pa
import yaml
from dotenv import load_dotenv


def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_store():
    from steps._lance import LanceStore
    config = _load_config()
    lance_config = config.get("lance", {})
    if not lance_config.get("enabled"):
        print("错误: lance.enabled 未开启")
        sys.exit(1)
    return LanceStore(lance_config), lance_config


def _table_names() -> list[str]:
    return ["prompt_templates", "optimization_history"]


def cmd_stats():
    """显示每张表的行数和版本数。"""
    store, _ = _get_store()
    for name in _table_names():
        ds = store._dataset(name)
        if ds is None:
            print(f"  {name}: 不存在")
        else:
            print(f"  {name}: {ds.count_rows()} 行, {len(ds.versions())} 个版本")


def cmd_compact():
    """清理旧版本文件，只保留最新版本。"""
    store, _ = _get_store()
    for name in _table_names():
        ds = store._dataset(name)
        if ds is None:
            print(f"  {name}: 不存在，跳过")
            continue
        before = len(ds.versions())
        ds.cleanup_old_versions(older_than=timedelta(0))
        after = len(ds.versions())
        removed = before - after
        print(f"  {name}: 清理 {removed} 个旧版本 ({before} → {after})")


def cmd_purge():
    """清空所有数据，保留空表结构。"""
    from steps._lance import (
        OPTIMIZATION_HISTORY_SCHEMA,
        PROMPT_TEMPLATES_SCHEMA,
    )

    store, _ = _get_store()
    schemas = {
        "prompt_templates": PROMPT_TEMPLATES_SCHEMA,
        "optimization_history": OPTIMIZATION_HISTORY_SCHEMA,
    }

    for name in _table_names():
        ds = store._dataset(name)
        if ds is None:
            print(f"  {name}: 不存在，跳过")
            continue

        rows_before = ds.count_rows()
        uri = store._table_uri(name)
        empty = pa.Table.from_pylist([], schema=schemas[name])
        lance.write_dataset(
            empty, uri,
            storage_options=store._storage_options,
            mode="overwrite",
        )
        # 清理 overwrite 产生的旧版本
        ds2 = lance.dataset(uri, storage_options=store._storage_options)
        ds2.cleanup_old_versions(older_than=timedelta(0))
        print(f"  {name}: 已清空 ({rows_before} → 0 行)")


def main():
    parser = argparse.ArgumentParser(
        description="Lance on TOS 数据维护工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  uv run python lance_cleanup.py stats     # 查看表状态
  uv run python lance_cleanup.py compact   # 清理旧版本
  uv run python lance_cleanup.py purge     # 清空所有数据
""",
    )
    parser.add_argument(
        "action",
        choices=["stats", "compact", "purge"],
        help="stats: 查看状态 | compact: 清理旧版本 | purge: 清空所有数据",
    )
    args = parser.parse_args()

    load_dotenv()

    if args.action == "purge":
        confirm = input("确认清空所有 Lance 数据？此操作不可逆 (y/N): ")
        if confirm.lower() != "y":
            print("已取消")
            return

    actions = {
        "stats": cmd_stats,
        "compact": cmd_compact,
        "purge": cmd_purge,
    }
    actions[args.action]()


if __name__ == "__main__":
    main()
