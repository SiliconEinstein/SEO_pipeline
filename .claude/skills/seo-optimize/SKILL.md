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

### 1. 加载数据

读取以下文件：
- `output/seo/audit_report.csv` — 审计报告（问题列表）
- `output/seo/existing_metadata.json` — 现有元数据
- `output/gsc/query_page_zero_click_*.csv` — 零点击查询词数据
- `output/seo/priority_ranked.csv` — 优先级排名

### 2. 解析参数

根据 `$ARGUMENTS` 确定处理范围：
- 空或 `30` → Top 30 页面（priority_ranked.csv 的前 30 行）
- `50` → Top 50
- `31-60` → 排名 31 到 60 的页面

**边界情况：**
- 如果请求范围超出 priority_ranked.csv 的总行数，自动截断到实际行数，并提示用户实际处理了多少页面
- 如果参数格式无法识别，报错并展示用法示例

### 3. 准备重写上下文

对于每个目标页面，打包以下信息：
- **path**: URL 路径
- **current_title**: 现有 title
- **current_description**: 现有 meta description
- **current_keywords**: 现有 meta keywords
- **issues**: 审计发现的问题列表
- **top_queries**: 该页面的 Top 5 查询词（按展示量排序）；如果该页面在零点击查询词数据中没有记录，则留空数组
- **page_type**: `course_article` 或 `keyword`
- **language**: `zh` 或 `en`（根据 URL 前缀判断）
- **avg_position**: 平均搜索排名

**边界情况：**
- 如果某页面在 `existing_metadata.json` 中找不到，跳过该页面并在最终摘要中列出被跳过的路径
- 如果零点击查询词数据文件不存在或为空，`top_queries` 设为空数组，仍然基于 issues 进行重写

### 4. 分批并行重写

将页面分为每 10 个一批，使用 Agent tool 并行处理。每个 Agent 使用 [rewrite-prompt.md](templates/rewrite-prompt.md) 中的 prompt 模板。

**Agent 输出格式：** JSON，key 为 path，value 包含 `title` 和 `meta_description`。

### 5. 后处理

合并所有 Agent 输出后，对每个页面补全完整元数据：

```
对每个页面:
  1. 验证 title ≤ 60 字符，desc ≤ 155 字符
     如果超长，智能截断而非简单裁剪（在词边界处截断，保留核心语义）
  2. 确保 title 以 " | SciencePedia" 结尾
  3. 同步 OG 标签（社交媒体分享时显示的内容，必须与页面元数据一致）:
     - og_title = title
     - og_description = description
     - og_url = base_url + path
     - og_type = "article"
     - 保留原有 og_image, og_site_name（这些是站点级配置，不应覆盖）
  4. 同步 Twitter 标签（Twitter 卡片展示，逻辑同 OG）:
     - twitter_title = title
     - twitter_description = description
     - 保留原有 twitter_card, twitter_image, twitter_site
  5. 更新 meta_keywords（从 top 查询词提取）
  6. 增强 Schema.org（结构化数据帮助搜索引擎理解页面内容，可触发富摘要展示）:
     - 更新 headline = title (去掉品牌后缀，因为 Schema headline 应是纯内容标题)
     - 更新 description = meta_description
     - 添加 datePublished 和 dateModified (如果缺失，搜索引擎用此判断内容新鲜度)
     - course_article 页面: 添加 LearningResource type（匹配教育类内容的结构化数据类型）
     - keyword 页面: 添加 about.DefinedTerm（匹配术语/概念类内容）
  7. 保留其他原有字段 (canonical, alternates, h1, meta_robots, meta_author)
```

输出格式参考 [sample-output.json](examples/sample-output.json)。

### 6. 输出

将结果写入：
- `output/seo/optimized_metadata.json` — 完整优化后的元数据
- `output/seo/original_metadata_topN.json` — 对应的原始元数据（同格式，便于对比）

输出完成后，打印统计摘要：
- 总处理页数
- 被跳过的页数及原因
- 各问题修复数量
- title/description 平均长度变化
- 语言修正数量
