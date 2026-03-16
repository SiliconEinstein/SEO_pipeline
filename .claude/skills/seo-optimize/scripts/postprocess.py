"""
postprocess.py — 合并 Agent 重写结果，补全完整元数据并输出

用法:
    python scripts/postprocess.py --rewritten /tmp/seo_rewritten.json

输入:
    --rewritten     Agent 输出的合并 JSON (key=path, value={title, meta_description})
    自动读取:
        /tmp/seo_rewrite_contexts.json   — prepare_contexts.py 生成的上下文
        /tmp/seo_original_metadata.json  — prepare_contexts.py 生成的原始元数据

输出:
    output/seo/optimized_metadata.json      — 优化后的完整元数据 (增量合并)
    output/seo/original_metadata_backup.json — 本次处理的原始元数据
"""

import argparse
import copy
import json
import os
import sys
from datetime import date

BASE_URL = "https://www.bohrium.com"
TODAY = date.today().isoformat()

TITLE_MAX = 60
DESC_MAX = 155
BRAND_SUFFIX = " | SciencePedia"


def smart_truncate(text, max_len, lang="en"):
    """在词/句边界处智能截断，保留核心语义。"""
    if len(text) <= max_len:
        return text

    truncated = text[:max_len]
    if lang == "en":
        for sep in [". ", ", ", " — ", " - ", "; ", " "]:
            idx = truncated.rfind(sep)
            if idx > max_len * 0.6:
                return truncated[:idx].rstrip(".,;: ")
    else:
        for sep in ["。", "，", "；", "、", " "]:
            idx = truncated.rfind(sep)
            if idx > max_len * 0.6:
                return truncated[:idx].rstrip("。，；、 ")

    return truncated.rstrip()


def ensure_brand_suffix(title, lang):
    """确保 title 以 ' | SciencePedia' 结尾，且总长 ≤ 60。"""
    if not title.endswith(BRAND_SUFFIX):
        content = title.replace(BRAND_SUFFIX, "").strip()
        title = content + BRAND_SUFFIX

    if len(title) > TITLE_MAX:
        max_content = TITLE_MAX - len(BRAND_SUFFIX)
        content = title.replace(BRAND_SUFFIX, "").strip()
        content = smart_truncate(content, max_content, lang)
        title = content + BRAND_SUFFIX

    return title


def enhance_schema(schema_list, headline, desc, path, page_type, rewrite=None):
    """增强 Schema.org 结构化数据，优先使用 LLM 生成的语义字段。"""
    if rewrite is None:
        rewrite = {}

    if not schema_list:
        schema_list = [{"@context": "https://schema.org", "@type": "Article"}]

    # LLM 生成的语义字段（fallback 到机械默认值）
    term_name = rewrite.get("schema_term_name", headline)
    subject = rewrite.get("schema_subject", "Science")
    course_name = rewrite.get("schema_course_name")

    for schema in schema_list:
        schema["headline"] = headline
        schema["description"] = desc

        if "datePublished" not in schema:
            schema["datePublished"] = "2024-01-15"
        schema["dateModified"] = TODAY

        if page_type == "course_article":
            types = schema.get("@type", "Article")
            if isinstance(types, str):
                types = [types]
            if "LearningResource" not in types:
                types.append("LearningResource")
            schema["@type"] = types

            if "educationalLevel" not in schema:
                if "graduate" in path.lower().split("-")[0]:
                    schema["educationalLevel"] = "Graduate"
                else:
                    schema["educationalLevel"] = "Undergraduate"

            # 使用 LLM 生成的课程名
            if course_name:
                schema["isPartOf"] = {
                    "@type": "Course",
                    "name": course_name,
                }

            # about 使用 LLM 生成的术语名和学科
            schema["about"] = {
                "@type": "DefinedTerm",
                "name": term_name,
                "inDefinedTermSet": subject,
            }

        elif page_type == "keyword":
            schema["about"] = {
                "@type": "DefinedTerm",
                "name": term_name,
                "inDefinedTermSet": subject,
            }

    return schema_list


def postprocess_page(path, rewrite, orig, ctx):
    """对单个页面执行完整的后处理。"""
    lang = ctx.get("language", "en")
    page_type = ctx.get("page_type", "")
    top_queries = ctx.get("top_queries", [])

    title = rewrite["title"]
    desc = rewrite["meta_description"]

    stats = {"title_truncated": False, "desc_truncated": False}

    # 1. 验证并截断
    title = ensure_brand_suffix(title, lang)
    if len(title) > TITLE_MAX:
        stats["title_truncated"] = True

    if len(desc) > DESC_MAX:
        desc = smart_truncate(desc, DESC_MAX, lang)
        stats["desc_truncated"] = True

    # 2. 构建优化后的元数据 (从原始数据 deepcopy)
    opt = copy.deepcopy(orig)
    opt["title"] = title
    opt["meta_description"] = desc

    # 3. 同步 OG 标签
    opt["og_title"] = title
    opt["og_description"] = desc
    opt["og_url"] = BASE_URL + path
    opt["og_type"] = "article"

    # 4. 同步 Twitter 标签
    opt["twitter_title"] = title
    opt["twitter_description"] = desc

    # 5. 更新 meta_keywords（优先使用 LLM 生成，fallback 到查询词拼接）
    if rewrite.get("meta_keywords"):
        opt["meta_keywords"] = rewrite["meta_keywords"]
    elif top_queries:
        opt["meta_keywords"] = ",".join(q["query"] for q in top_queries[:5])

    # 6. 增强 Schema.org（传入 rewrite 以使用 LLM 生成的语义字段）
    headline = title.replace(BRAND_SUFFIX, "").strip()
    opt["schema_json_ld"] = enhance_schema(
        opt.get("schema_json_ld", []), headline, desc, path, page_type, rewrite
    )

    return opt, stats


def merge_with_existing(output_path, new_data):
    """增量合并: 如果输出文件已存在，merge 新数据进去。"""
    if os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing.update(new_data)
        return existing
    return new_data


def main():
    parser = argparse.ArgumentParser(description="SEO 元数据后处理")
    parser.add_argument("--rewritten", required=True, help="Agent 输出的合并 JSON 文件路径")
    parser.add_argument(
        "--output-dir", default="output/seo", help="输出目录 (默认 output/seo)"
    )
    args = parser.parse_args()

    # 加载数据
    with open(args.rewritten, "r", encoding="utf-8") as f:
        rewritten = json.load(f)

    with open("/tmp/seo_rewrite_contexts.json", "r", encoding="utf-8") as f:
        contexts = json.load(f)

    with open("/tmp/seo_original_metadata.json", "r", encoding="utf-8") as f:
        original_metadata = json.load(f)

    ctx_lookup = {c["path"]: c for c in contexts}

    # 统计
    total = 0
    skipped = []
    issues_fixed = {}
    title_lens_before, title_lens_after = [], []
    desc_lens_before, desc_lens_after = [], []
    title_truncated_count = 0
    desc_truncated_count = 0
    language_fixed = 0

    optimized = {}
    original_backup = {}

    for path, rw in rewritten.items():
        if path not in original_metadata:
            skipped.append((path, "not in existing_metadata"))
            continue

        total += 1
        orig = original_metadata[path]
        ctx = ctx_lookup.get(path, {})

        title_lens_before.append(len(orig.get("title", "")))
        desc_lens_before.append(len(orig.get("meta_description", "")))

        opt, stats = postprocess_page(path, rw, orig, ctx)

        title_lens_after.append(len(opt["title"]))
        desc_lens_after.append(len(opt["meta_description"]))

        if stats["title_truncated"]:
            title_truncated_count += 1
        if stats["desc_truncated"]:
            desc_truncated_count += 1

        for issue in ctx.get("issues", []):
            issues_fixed[issue] = issues_fixed.get(issue, 0) + 1

        if "language_mismatch" in ctx.get("issues", []):
            language_fixed += 1

        optimized[path] = opt
        original_backup[path] = orig

    # 增量合并并写入
    out_dir = args.output_dir
    os.makedirs(out_dir, exist_ok=True)

    optimized_path = os.path.join(out_dir, "optimized_metadata.json")
    merged = merge_with_existing(optimized_path, optimized)
    with open(optimized_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    backup_path = os.path.join(out_dir, "original_metadata_backup.json")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(original_backup, f, ensure_ascii=False, indent=2)

    # 打印统计摘要
    def avg(lst):
        return sum(lst) / len(lst) if lst else 0

    print("=" * 60)
    print("SEO 元数据优化完成")
    print("=" * 60)
    print(f"\n本次处理页数: {total}")
    print(f"输出文件累计页数: {len(merged)}")
    print(f"被跳过页数: {len(skipped)}")
    for p, reason in skipped:
        print(f"  - {p}: {reason}")

    print(f"\n各问题修复数量:")
    for issue, count in sorted(issues_fixed.items(), key=lambda x: -x[1]):
        print(f"  {issue}: {count}")

    print(f"\nTitle 平均长度变化: {avg(title_lens_before):.1f} -> {avg(title_lens_after):.1f} 字符")
    print(f"Description 平均长度变化: {avg(desc_lens_before):.1f} -> {avg(desc_lens_after):.1f} 字符")
    print(f"Title 截断次数: {title_truncated_count}")
    print(f"Description 截断次数: {desc_truncated_count}")
    print(f"语言修正数量: {language_fixed}")

    print(f"\n输出文件:")
    print(f"  {optimized_path}")
    print(f"  {backup_path}")


if __name__ == "__main__":
    main()
