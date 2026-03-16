---
name: seo-optimize
description: 基于 SEO 审计报告，用大模型批量重写 title/description/keywords/Schema.org。当用户提到"SEO 优化"、"重写元数据"、"优化 meta"时触发。
argument-hint: "[N] 或 [start-end]"
disable-model-invocation: true
allowed-tools: Bash(uv run *), Read, Write, Agent, Glob, Grep
---

# SEO 元数据批量优化

基于 SEO pipeline 的审计报告，用大模型批量重写 title / description / keywords / Schema.org。

**用法：**
- `/seo-optimize` — 默认优化 Top 30
- `/seo-optimize 50` — 优化 Top 50
- `/seo-optimize 31-60` — 优化排名 31-60（增量处理）

参数: $ARGUMENTS

---

## 前置检查

在开始前，检查 pipeline 输出是否存在：

```bash
ls output/seo/audit_report.csv output/seo/existing_metadata.json output/seo/priority_ranked.csv
```

如果文件不存在，提示用户先运行 pipeline：
```bash
uv run python main.py all --skip fetch
```

如果只缺 `audit_report.csv`，可以单独补跑：
```bash
uv run python main.py audit
```

## 执行步骤

### 1-3. 加载数据、解析参数、准备重写上下文

使用 [prepare_contexts.py](scripts/prepare_contexts.py) 一步完成数据加载、参数解析和上下文构建：

```bash
# 根据 $ARGUMENTS 选择对应的参数
uv run python .claude/skills/seo-optimize/scripts/prepare_contexts.py --top 30        # 默认
uv run python .claude/skills/seo-optimize/scripts/prepare_contexts.py --top 50        # Top 50
uv run python .claude/skills/seo-optimize/scripts/prepare_contexts.py --range 31-60   # 排名 31-60
```

脚本自动处理：
- CSV 的 `utf-8-sig` 编码和中文列名映射
- 从 `existing_metadata.json` 匹配目标页面（缺失则跳过并报告）
- 从最新的零点击查询词 CSV 提取 Top 5 查询词
- 输出分批 JSON 到 `/tmp/seo_batch_*.json`（每批 10 个），以及 `/tmp/seo_rewrite_contexts.json` 和 `/tmp/seo_original_metadata.json`

**如需自定义**，脚本还支持 `--batch-size` 和 `--output-dir` 参数。详见脚本文件头部的 docstring。

### 4. 分批并行重写

将页面分为每 10 个一批，使用 Agent tool 并行处理。每个 Agent 使用 [rewrite-prompt.md](templates/rewrite-prompt.md) 中的 prompt 模板。

**Agent 输出格式：** JSON，key 为 path，value 包含 `title`、`meta_description`、`meta_keywords`、`schema_term_name`、`schema_subject`，以及 `schema_course_name`（仅 course_article 页面）。

**Agent 输出校验：**
- 验证返回的 JSON 可解析，且 key 为预期的 path
- 如果某个 Agent 返回格式异常或缺少页面，记录并在摘要中报告，不阻塞其他批次

### 5-6. 后处理与输出

先将所有 Agent 输出合并为一个 JSON 文件（key=path, value={title, meta_description, meta_keywords, schema_term_name, schema_subject, schema_course_name}），保存到 `/tmp/seo_rewritten.json`，然后运行 [postprocess.py](scripts/postprocess.py)：

```bash
uv run python .claude/skills/seo-optimize/scripts/postprocess.py --rewritten /tmp/seo_rewritten.json
```

脚本自动完成以下后处理（输出格式参考 [sample-output.json](examples/sample-output.json)）：

1. **验证长度** — title ≤ 60 字符，desc ≤ 155 字符，超长时在词/句边界智能截断
2. **品牌后缀** — 确保 title 以 ` | SciencePedia` 结尾
3. **同步 OG 标签** — og_title/og_description/og_url/og_type，保留原有 og_image、og_site_name
4. **同步 Twitter 标签** — twitter_title/twitter_description，保留原有 twitter_card、twitter_image、twitter_site
5. **更新 meta_keywords** — 从 top 查询词提取
6. **增强 Schema.org** — headline（去品牌后缀）、description、datePublished/dateModified、course_article 加 LearningResource、keyword 加 DefinedTerm
7. **保留原有字段** — canonical、alternates、h1、meta_robots、meta_author
8. **增量合并** — 如果 `optimized_metadata.json` 已存在，merge 而非覆盖

输出：
- `output/seo/optimized_metadata.json` — 完整优化后的元数据（增量累积）
- `output/seo/original_metadata_backup.json` — 本次处理的原始元数据

脚本会自动打印统计摘要（处理页数、跳过页数、问题修复数量、长度变化、语言修正数量）。
