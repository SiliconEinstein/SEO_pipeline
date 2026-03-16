"""
prepare_contexts.py — 从 pipeline 输出中提取目标页面的重写上下文

用法:
    python scripts/prepare_contexts.py                  # Top 30
    python scripts/prepare_contexts.py --top 50         # Top 50
    python scripts/prepare_contexts.py --range 31-60    # 排名 31-60

输出:
    /tmp/seo_batch_0.json, /tmp/seo_batch_1.json, ...   — 分批上下文 (每批 10 个)
    /tmp/seo_rewrite_contexts.json                       — 全部上下文
    /tmp/seo_original_metadata.json                      — 对应的原始元数据
"""

import argparse
import csv
import glob
import json
import sys
from urllib.parse import urlparse


def parse_range(args):
    """解析命令行参数，返回 (start_idx, end_idx)，均为 0-based。"""
    if args.range:
        parts = args.range.split("-")
        if len(parts) != 2:
            print(f"错误: 无法解析范围 '{args.range}'，格式应为 start-end，如 31-60")
            sys.exit(1)
        start = int(parts[0]) - 1  # 转为 0-based
        end = int(parts[1])
        return start, end
    else:
        return 0, args.top


def load_priority_ranked(path, start, end):
    """加载 priority_ranked.csv 中目标范围的行。"""
    pages = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if i < start:
                continue
            if i >= end:
                break
            pages.append(row)
    return pages


def load_audit_report(path, target_paths):
    """加载 audit_report.csv，返回 path -> issues 映射。"""
    audit = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            p = row["path"]
            if p in target_paths:
                issues_str = row.get("issues", "")
                issues = [x.strip() for x in issues_str.split(",") if x.strip()]
                audit[p] = issues
    return audit


def load_metadata(path, target_paths):
    """加载 existing_metadata.json，返回匹配的元数据和跳过的路径。"""
    with open(path, "r", encoding="utf-8") as f:
        all_meta = json.load(f)

    metadata = {}
    skipped = []
    for p in target_paths:
        if p in all_meta:
            metadata[p] = all_meta[p]
        else:
            skipped.append(p)
    return metadata, skipped


def load_zero_click_queries(pattern, target_paths):
    """加载最新的零点击查询词 CSV，返回 path -> top 5 queries。"""
    zc_files = sorted(glob.glob(pattern))
    query_data = {}

    if not zc_files:
        return query_data

    latest = zc_files[-1]
    with open(latest, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            page = row.get("路径", "")
            if page.startswith("http"):
                page = urlparse(page).path
            if page in target_paths:
                if page not in query_data:
                    query_data[page] = []
                query_data[page].append(
                    {
                        "query": row.get("查询词", ""),
                        "impressions": int(float(row.get("展示", 0))),
                    }
                )

    # 按展示量排序，保留 Top 5
    for p in query_data:
        query_data[p] = sorted(
            query_data[p], key=lambda x: x["impressions"], reverse=True
        )[:5]

    return query_data


def build_contexts(pages, audit, metadata, query_data, skipped):
    """为每个目标页面构建重写上下文。"""
    contexts = []
    for p in pages:
        path = p["路径"]
        if path in skipped:
            continue

        meta = metadata.get(path, {})
        contexts.append(
            {
                "path": path,
                "current_title": meta.get("title", ""),
                "current_description": meta.get("meta_description", ""),
                "current_keywords": meta.get("meta_keywords", ""),
                "issues": audit.get(path, []),
                "top_queries": query_data.get(path, []),
                "page_type": p.get("seo_page_type", ""),
                "language": p.get("language", ""),
                "avg_position": float(p.get("平均排名", 0)),
            }
        )
    return contexts


def main():
    parser = argparse.ArgumentParser(description="准备 SEO 重写上下文")
    parser.add_argument("--top", type=int, default=30, help="处理 Top N 页面 (默认 30)")
    parser.add_argument("--range", type=str, help="处理指定排名范围，如 31-60")
    parser.add_argument("--batch-size", type=int, default=10, help="每批页面数 (默认 10)")
    parser.add_argument(
        "--output-dir", type=str, default="output", help="pipeline 输出目录 (默认 output)"
    )
    args = parser.parse_args()

    start, end = parse_range(args)
    out = args.output_dir

    # 1. 加载 priority_ranked
    pages = load_priority_ranked(f"{out}/seo/priority_ranked.csv", start, end)
    actual_count = len(pages)
    if actual_count == 0:
        print("错误: 没有找到目标页面，请检查范围参数")
        sys.exit(1)
    if actual_count < (end - start):
        print(f"警告: 请求 {end - start} 个页面，实际只有 {actual_count} 个")

    paths = [p["路径"] for p in pages]
    target_set = set(paths)
    print(f"目标页面数: {actual_count}")

    # 2. 加载其他数据源
    audit = load_audit_report(f"{out}/seo/audit_report.csv", target_set)
    metadata, skipped = load_metadata(f"{out}/seo/existing_metadata.json", paths)
    query_data = load_zero_click_queries(f"{out}/gsc/query_page_zero_click_*.csv", target_set)

    print(f"审计数据匹配: {len(audit)} 页")
    print(f"元数据匹配: {len(metadata)} 页")
    print(f"查询词数据匹配: {len(query_data)} 页")
    if skipped:
        print(f"跳过 (元数据缺失): {skipped}")

    # 3. 构建上下文
    contexts = build_contexts(pages, audit, metadata, query_data, skipped)
    print(f"构建上下文: {len(contexts)} 页")

    # 4. 分批保存
    batch_size = args.batch_size
    num_batches = (len(contexts) + batch_size - 1) // batch_size
    for i in range(num_batches):
        batch = contexts[i * batch_size : (i + 1) * batch_size]
        path = f"/tmp/seo_batch_{i}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False, indent=2)
        print(f"  Batch {i}: {len(batch)} 页 -> {path}")

    # 5. 保存全量上下文和原始元数据
    with open("/tmp/seo_rewrite_contexts.json", "w", encoding="utf-8") as f:
        json.dump(contexts, f, ensure_ascii=False, indent=2)

    original_meta = {c["path"]: metadata[c["path"]] for c in contexts if c["path"] in metadata}
    with open("/tmp/seo_original_metadata.json", "w", encoding="utf-8") as f:
        json.dump(original_meta, f, ensure_ascii=False, indent=2)

    # 6. 打印摘要
    print(f"\n{'='*50}")
    print(f"上下文准备完成: {len(contexts)} 页, {num_batches} 批")
    for i, ctx in enumerate(contexts):
        lang = ctx["language"]
        n_issues = len(ctx["issues"])
        n_queries = len(ctx["top_queries"])
        print(
            f"  {start+i+1}. [{lang}] issues={n_issues} queries={n_queries} | {ctx['path'][:80]}"
        )


if __name__ == "__main__":
    main()
