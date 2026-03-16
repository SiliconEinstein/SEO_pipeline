#!/usr/bin/env python3
"""
SEO Pipeline — 统一 CLI 入口

子命令:
    fetch    — 从 Google Search Console 拉取数据
    rank     — 优先级排名
    crawl    — 抓取现有 SEO 元数据
    audit    — 质量审计
    optimize — LLM 重写 SEO 元数据
    all      — 按顺序执行全部 (可用 --skip 跳过某步)

使用方法:
    uv run python main.py fetch
    uv run python main.py optimize --top 50
    uv run python main.py all
    uv run python main.py all --skip fetch
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Ensure imports work regardless of how the script is invoked:
#   - `python main.py` from within seo_pipeline/ (standard usage)
#   - `python seo_pipeline/main.py` from the parent directory
#   - the directory being renamed (e.g. `seo-pipeline/`)
_PIPELINE_DIR = str(Path(__file__).resolve().parent)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
for _p in (_PIPELINE_DIR, _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml

# ---------------------------------------------------------------------------
# Config & logging
# ---------------------------------------------------------------------------

_PIPELINE_DIR = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _PIPELINE_DIR / "config.yaml"


def _load_config(config_path: Path) -> dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Step registry
# ---------------------------------------------------------------------------

STEPS = ["fetch", "rank", "crawl", "audit", "optimize"]


def _import_step(name: str):
    """Lazy-import a step module to avoid loading all deps upfront.

    Tries ``seo_pipeline.steps.*`` first (when running from the parent
    directory), then falls back to ``steps.*`` (when running from within
    the seo_pipeline directory, or if the directory has been renamed).
    """
    module_map = {
        "fetch": "fetch_gsc",
        "rank": "rank",
        "crawl": "crawl",
        "audit": "audit",
        "optimize": "optimize",
    }
    if name not in module_map:
        raise ValueError(f"Unknown step: {name}")

    mod_name = module_map[name]
    try:
        import importlib
        return importlib.import_module(f"seo_pipeline.steps.{mod_name}")
    except ImportError:
        import importlib
        return importlib.import_module(f"steps.{mod_name}")


def _run_step(name: str, config: dict, output_dir: Path) -> dict:
    """Run a single step and return its result dict."""
    print(f"\n{'='*60}")
    print(f"  Step: {name}")
    print(f"{'='*60}")
    t0 = time.monotonic()
    module = _import_step(name)
    result = module.run(config, output_dir)
    elapsed = time.monotonic() - t0
    print(f"\n  [{name}] completed in {elapsed:.1f}s")
    print(f"  Output files: {[str(f) for f in result.get('output_files', [])]}")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="SEO Pipeline — 端到端 SEO 数据处理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  uv run python main.py fetch              # 仅拉取 GSC 数据
  uv run python main.py rank              # 仅排名
  uv run python main.py crawl             # 仅抓取元数据
  uv run python main.py audit             # 仅审计
  uv run python main.py optimize          # LLM 优化 Top 30
  uv run python main.py optimize --top 50 # LLM 优化 Top 50
  uv run python main.py all               # 执行全部步骤
  uv run python main.py all --skip fetch  # 跳过 fetch
""",
    )

    parser.add_argument(
        "command",
        choices=STEPS + ["all"],
        help="要执行的步骤 (或 'all' 执行全部)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"配置文件路径 (默认: {_DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--skip",
        nargs="+",
        choices=STEPS,
        default=[],
        help="跳过的步骤 (仅在 'all' 模式下有效)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="输出详细日志",
    )

    # optimize 步骤专用参数
    opt_group = parser.add_mutually_exclusive_group()
    opt_group.add_argument(
        "--top", type=int, help="optimize: 处理 Top N 页面 (覆盖 config.yaml)"
    )
    opt_group.add_argument(
        "--range", type=str, help="optimize: 排名范围如 31-60 (覆盖 config.yaml)"
    )

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    # Load config
    config = _load_config(args.config)

    # Inject optimize CLI overrides
    if getattr(args, "top", None):
        config.setdefault("optimize", {})["top"] = args.top
    if getattr(args, "range", None):
        config.setdefault("optimize", {})["range"] = args.range

    output_dir = Path(config.get("output_dir", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Config: {args.config}")
    print(f"Output: {output_dir}")

    # Determine which steps to run
    if args.command == "all":
        steps_to_run = [s for s in STEPS if s not in args.skip]
        if args.skip:
            print(f"Skipping: {args.skip}")
    else:
        steps_to_run = [args.command]

    # Execute
    t_total = time.monotonic()
    results = {}
    for step_name in steps_to_run:
        results[step_name] = _run_step(step_name, config, output_dir)

    total_elapsed = time.monotonic() - t_total

    # Final summary
    print(f"\n{'='*60}")
    print(f"  Pipeline complete!")
    print(f"  Steps run: {steps_to_run}")
    print(f"  Total time: {total_elapsed:.1f}s")
    print(f"{'='*60}")

    # Print per-step summaries
    for step_name, result in results.items():
        summary = result.get("summary", {})
        if summary:
            print(f"\n  [{step_name}] summary:")
            for k, v in summary.items():
                if isinstance(v, dict):
                    print(f"    {k}:")
                    for kk, vv in v.items():
                        print(f"      {kk}: {vv}")
                else:
                    print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
