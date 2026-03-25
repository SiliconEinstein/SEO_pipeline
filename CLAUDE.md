# SEO Pipeline 开发指南

本文档面向开发者，介绍代码实现细节，便于理解和修改。

## 架构概览

```
main.py                    CLI 入口，调度所有步骤
  ├── steps/_classify.py   共享工具（子类型发现、CSV 查找）
  ├── steps/fetch_gsc.py   GSC API 交互 + 数据清洗
  ├── steps/analyze.py     数据分析（依赖 _classify.py）
  ├── steps/rank.py        排名筛选（依赖 _classify.py）
  ├── steps/crawl.py       异步 HTTP 抓取
  ├── steps/audit.py       规则检测
  ├── steps/optimize.py    LLM 调用 + 后处理
  └── steps/evaluate.py    趋势跟踪 + 效果评估（依赖 _classify.py）
```

每个步骤模块暴露 `run(config: dict, output_dir: Path) -> dict` 函数。返回值包含 `output_files`（文件路径列表）和 `summary`（摘要字典）。步骤之间通过文件传递数据，无内存依赖。

## main.py — CLI 调度

```python
STEPS = ["fetch", "analyze", "rank", "crawl", "audit", "optimize"]
```

步骤名到模块名的映射：

```python
module_map = {
    "fetch": "fetch_gsc",
    "analyze": "analyze",
    "rank": "rank",
    "crawl": "crawl",
    "audit": "audit",
    "optimize": "optimize",
}
```

`_import_step(name)` 通过 `importlib` 懒加载模块，先尝试 `seo_pipeline.steps.*`，失败则回退到 `steps.*`。

`all` 命令按 STEPS 顺序执行，`--skip` 从中剔除指定步骤。`--top` 和 `--range` 是 optimize 专用参数，注入到 `config["optimize"]`。

**修改点：** 新增步骤只需在 `STEPS` 和 `module_map` 中添加条目，然后在 `steps/` 下创建对应模块。

## steps/_classify.py — 共享工具

### find_latest_csv

```python
find_latest_csv(directory: Path, pattern: str) -> Path
```

在指定目录中查找匹配 glob 模式的最新 CSV 文件（按文件名字典序取最后一个）。analyze、rank、audit 共用。

### discover_subtypes

核心函数 `discover_subtypes(paths: pd.Series) -> pd.Series`，从 URL 路径结构推导子类型：

```python
# 算法流程：
# 1. 去掉 /en/ 前缀（语言归一化）
normalized = paths.str.replace(r"^/en/", "/", regex=True)

# 2. 取目录部分（去掉最后一段 slug）
#    /sciencepedia/feynman/keyword/quantum → /sciencepedia/feynman/keyword
dirs = normalized.apply(_dir_part)

# 3. 找所有目录的最长公共前缀（在段边界截断）
parent = os.path.commonprefix(unique_dirs)  # + 段边界修正

# 4. 去掉公共前缀 → 子类型标签
subtypes = dirs.str.replace(prefix_pattern, "", regex=True)
```

analyze 和 rank 共用此函数。注意：**必须对同一组路径调用一次**，不能分别对不同子集调用（否则公共前缀不同，标签对不上）。

**注意：** 如果数据中有少量"浅层"页面（如 `/sciencepedia/manifold_hypothesis`，只有两段路径），其目录部分与公共前缀相同，会导致公共前缀无法被剥离。此时所有子类型标签都会保留完整的目录前缀（如 `sciencepedia/feynman` 而非 `feynman`）。`config.yaml` 中的 `include_subtypes` 和 `subtype_page_types` 必须使用 analyze 报告中显示的实际标签。

**修改点：** 如果自动发现的子类型粒度不合适（太粗或太细），可以调整 `_dir_part()` 中取路径段的逻辑。

## steps/fetch_gsc.py — GSC 数据拉取

### OAuth 认证

```python
# 凭证加载流程：
# 1. 尝试从 token.json 加载已有 token
# 2. token 过期 → 自动刷新
# 3. 无 token → 启动 InstalledAppFlow，弹浏览器授权
# 4. token 缓存到 token.json
creds = _get_credentials(credentials_file)
```

凭证路径通过 `_cwd()` 函数在运行时解析（不在 import 时捕获），确保工作目录变化时仍然正确。

### 数据拉取

```python
# 两个维度的请求：
# 1. query×page 维度 — 每个搜索词在每个页面的数据（用于零点击分析）
df_qp = _fetch_search_analytics(service, site_url, start, end,
                                 dimensions=["query", "page"])

# 2. page 维度 — 页面级汇总（用于排名）
df_page = _fetch_search_analytics(service, site_url, start, end,
                                   dimensions=["page"])
```

自动分页，每页 25000 行。

### 页面分类

```python
PAGE_TYPES = [
    (["/paper-details/", "/en/paper-details/"], "Paper"),
    (["/scholar/", "/en/scholar/"], "Scholar"),
    (["/sciencepedia/", "/en/sciencepedia/"], "Sciencepedia"),
    (["/apps/", "/en/apps/"], "Apps"),
    (["/notebooks/"], "Notebooks"),
    (["/intro"], "Intro"),
    (["/blog/"], "Blog"),
]
```

这个列表是**硬编码**的，跟 bohrium.com 的 URL 结构绑定。换站点需要修改。

### 排名段和优先级

```python
RANKING_BINS = [
    (0, 3, "1-3 (首页顶部)"),
    (3, 5, "4-5"),
    (5, 10, "6-10 (首页底部)"),
    (10, 20, "11-20 (第2页)"),
    (20, 50, "21-50"),
    (50, 999, "50+"),
]
```

优先级 A~D 基于排名段判定。页面分群基于展示量中位数。

### URL origin 转换

`_site_url_to_origin(site_url, base_url)` 将 GSC 的 `site_url` 转为实际 URL origin，用于从完整 URL 中提取路径。优先使用 `seo.base_url` 配置（最可靠），fallback 时 `sc-domain:` 格式不再假设 `www` 子域名。

### page_filter 和 exclude_patterns

`page_filter` 是路径子串匹配，应用于 ranking 和 zero-click 两个数据集。`exclude_patterns` 中的空字符串会匹配所有页面，代码已做防御（`[p for p in patterns if p]`）。

**修改点：** 如需支持正则过滤，把 `str.contains(page_filter, case=False)` 改为 `str.contains(page_filter, case=False, regex=True)`。

## steps/analyze.py — 数据分析

### 分析维度

```python
# 1. 子类型分布：页面数、总展示、总点击、加权CTR、CTR方差、加权排名、机会分
def _subtype_distribution(ranking):
    # 加权排名 = sum(排名 × 展示) / sum(展示)
    # 加权CTR = 总点击 / 总展示
    # CTR_std = 组内各页面 CTR 的标准差
    # 机会分 = 总展示 - 总点击（未转化的展示量）

# 2. 零点击分析：各子类型零点击页面数、占比、零点击总展示
def _zero_click_analysis(ranking, zero_click):
    # 子类型标签从 ranking 的已计算列 join 过来
    # 不单独调用 discover_subtypes（否则公共前缀不一致）
    # 零点击 CSV 缺失时优雅降级，跳过此分析

# 3. 排名段分布：子类型 × 排名段的 crosstab
def _ranking_segment_distribution(ranking):
    # 提取排名段标签的数字下界进行映射，归并为 5 个短标签

# 4. 机会评分预览：各子类型 总展示-总点击
def _opportunity_score_preview(ranking):

# 5. 语言分布：按 /en/ 前缀判断中英文
def _language_distribution(ranking):
```

**修改点：**
- 新增分析维度：添加一个 `_xxx()` 函数，在 `run()` 中调用，结果加入 JSON 输出和控制台打印。
- 修改子类型发现逻辑：改 `_classify.py` 中的 `discover_subtypes()`。

## steps/rank.py — 优先级排名

### 核心流程

```python
def run(config, output_dir):
    # 1. 加载两份 CSV，合并零点击查询词到页面维度
    data = load_and_merge_data(gsc_dir)

    # 2. 筛选 + 排名
    ranked = filter_and_rank(data, include_subtypes, subtype_page_types)

    # 3. 输出 priority_ranked.csv
```

### 数据合并

```python
def load_and_merge_data(gsc_dir):
    # ranking CSV + zero-click CSV，通过 路径 列 left join
    # 为每个页面聚合 top 10 查询词（按展示量排序）
    # 计算 query_count（去重查询词数量）
    # 校验两份 CSV 的日期一致性
```

### 筛选逻辑

```python
def filter_and_rank(df, include_subtypes=None, subtype_page_types=None):
    # 1. discover_subtypes() → subtype 列（自动发现）
    # 2. classify_page_type(subtype, mapping) → seo_page_type 列（配置驱动）
    # 3. detect_language() → language 列
    # 4. 筛选：query_count > 0 AND (subtype in include_subtypes OR include_subtypes 为空)
    # 5. priority_score = 展示 × (1 - CTR)
    # 6. 按 priority_score 降序排列
```

### subtype 与 seo_page_type 的关系

这是两套独立的分类体系：

- **subtype**：由 `_classify.py` 的 `discover_subtypes()` 从 URL 结构自动发现，用于用户可见的筛选（`include_subtypes`）。
- **seo_page_type**：由 `seo.subtype_page_types` 配置映射（subtype → page_type），用于 optimize 的 Schema.org 增强和 audit 的规则检测。

```yaml
# config.yaml 示例（subtype 标签取决于 analyze 报告的实际输出）
seo:
  subtype_page_types:
    "sciencepedia/feynman": "course_article"           # → 添加 LearningResource
    "sciencepedia/feynman/keyword": "keyword"          # → 添加 DefinedTerm
    "sciencepedia/agent-tools": "agent_tool"           # → 不做 Schema 增强
```

未映射的 subtype 默认为 `"other"`。换站点只需修改此映射表。

`classify_page_type(subtype, mapping)` 函数只做一次 dict 查找，无硬编码路径规则。

**修改点：**
- 修改排名公式：改 `compute_priority_scores()`。
- 修改筛选条件：改 `filter_and_rank()` 中的 mask 逻辑。
- 新增输出列：加到 `SAVE_COLUMNS` 列表。
- 新增页面类型：在 `config.yaml` 的 `subtype_page_types` 中添加映射。

## steps/crawl.py — 元数据抓取

```python
# 异步抓取架构：
# aiohttp.ClientSession + asyncio.Semaphore(concurrency)
# 每页超时 30 秒，每 50 页打印进度
# User-Agent 使用 seo.base_url 动态拼接

async def _fetch_one(session, sem, url):
    # HTTP GET → BeautifulSoup 解析 → 提取所有 SEO 元数据

def _parse_metadata(html):
    # 提取字段：
    # - <title>
    # - <meta name="description/keywords/robots/author">
    # - <meta property="og:*">
    # - <meta name="twitter:*">
    # - <link rel="canonical">
    # - <link rel="alternate" hreflang="...">
    # - <script type="application/ld+json"> (JSON-LD Schema.org)
    # - <h1>
```

`seo.base_url` 是必填配置，未设置时 crawl 会报错而非静默使用默认值。

**修改点：** 需要抓取新字段（如 `<meta name="viewport">`），在 `_parse_metadata()` 中添加提取逻辑。

## steps/audit.py — 质量审计

6 条检测规则，每条是一个独立函数：

```python
# 规则 1-2: 长度检测（阈值从 config 读取）
# title > max_title_length (默认 60)
# description > max_desc_length (默认 155)

# 规则 3: 通用开头词检测
GENERIC_OPENERS = ["Explore ", "Learn ", "Discover ", "探索", "学习", "了解", ...]
def _check_generic_opening(desc) -> str | None

# 规则 4: 语言不匹配
def _check_language_mismatch(path, title, desc) -> bool
    # 非 /en/ 路径 + title+desc 无中文字符 → 不匹配

# 规则 5: 关键词覆盖
def _check_keyword_coverage(title, desc, top_queries) -> list[str]
    # Top 3 查询词的 60% 词级匹配阈值（不检查 meta_keywords）

# 规则 6: Schema.org 检测
def _check_schema_completeness(schemas, page_type) -> list[str]
    # page_type 从 priority_ranked.csv 的 seo_page_type 列读取（配置驱动）
    # 检测: 无 schema、缺 datePublished/dateModified
    # course_article 页面需有 LearningResource 类型
```

audit 使用 `logging` 记录数据加载和文件保存，控制台摘要报告使用 `print`。

**修改点：** 新增审计规则只需写一个检测函数，在 `run()` 的 per-page 循环中调用，issue 加入结果列表即可。

## steps/optimize.py — LLM 重写

### 4 个阶段

```python
def run(config, output_dir):
    # 阶段 1: 准备上下文
    contexts = _build_contexts(ranked, metadata, audit, queries)
    # 为每个页面构建 JSON：当前 title/desc/keywords、审计问题、Top 5 查询词
    # 按 batch_size 分批写入 tmp/seo_batch_*.json

    # 阶段 2: 调用 LLM API
    results = _call_llm(batches, prompt_template, model, ...)
    # LiteLLM 发送请求，并发控制 + 指数退避重试
    # 断点续传：检测 _result.json 跳过已完成批次

    # 阶段 3: 合并结果
    merged = _merge_results(tmp_dir)
    # 从 LLM 响应提取 JSON（支持 markdown fence 和裸 JSON）
    # 写入 tmp/seo_rewritten.json

    # 阶段 4: 后处理
    final = _postprocess(merged, contexts, config)
```

### 后处理细节

```python
def _postprocess_page(rewrite, ctx, seo_config):
    # 1. 品牌后缀：确保 title 以 brand_suffix 结尾
    # 2. 长度截断：词/句边界智能截断（中英文分开处理）
    # 3. Schema.org 增强（_enhance_schema）：
    #    - 仅修改已有 schema，不凭空创建
    #    - 白名单机制（CONTENT_SCHEMA_TYPES）：只修改内容型 schema
    #      Article, LearningResource, Course, WebPage 等 → 修改
    #      BreadcrumbList, WebSite, Organization 等 → 完全不动
    #    - headline, description 同步到内容型 schema（保持一致性）
    #    - 不修改 dateModified（pipeline 只改 meta，不改内容，日期由 CMS 管理）
    #    - course_article → LearningResource 类型 + isPartOf(Course)
    #    - keyword/course_article → about(DefinedTerm)，需 LLM 提供 term_name + subject
    # 4. 同步 OG/Twitter 标签（og_type 保留原值，无原值时默认 "article"）
    # 5. meta_keywords 置空（Google 不使用此信号）
```

`seo.base_url` 是必填配置，未设置时 optimize 会报错。

增量合并：如果 `optimized_metadata.json` 已存在，新结果 merge 进去，支持分批多次运行。

**修改点：**
- 修改 prompt：编辑 `templates/rewrite-prompt.md`（换站点需修改品牌名）。
- 修改后处理规则：改 `_postprocess_page()` 和 `_enhance_schema()`。
- 修改 Schema 白名单：改 `CONTENT_SCHEMA_TYPES` 集合。
- 换 LLM 模型：改 `config.optimize.model`（通过 LiteLLM 支持 Claude / GPT-4 / Gemini 等）。

### audit + optimize 优化行为详解

#### audit（步骤 5）— 仅检测，不修改

audit 不修改任何数据，只输出问题清单（`audit_report.csv`）供 optimize 消费。6 条检测规则均为硬编码：

| 规则 | 检测逻辑 | 阈值/来源 |
|------|---------|-----------|
| `desc_too_long` | `len(desc) > max_desc_length` | 配置（默认 155） |
| `title_too_long` | `len(title) > max_title_length` | 配置（默认 60） |
| `generic_opening` | desc 以 "Explore/Learn/探索/学习..." 等开头 | 硬编码词表 |
| `language_mismatch` | 非 `/en/` 路径但 title+desc 无中文字符 | 硬编码 |
| `missing_keywords` | Top 3 查询词 < 60% 词级匹配 | 硬编码 `TOP_K=3, THRESHOLD=0.6` |
| `schema:*` | 无 schema / 缺 datePublished / course_article 缺 LearningResource | 硬编码 |

#### optimize（步骤 6）— LLM 与硬编码的分工

**LLM 负责"写什么内容"，硬编码负责"放到哪里 + 格式合规"。**

##### LLM 生成的字段（通过 prompt 模板控制）

| LLM 输出字段 | 用途 |
|-------------|------|
| `title` | 新标题正文 |
| `meta_description` | 新描述正文 |
| `schema_term_name` | 核心术语名（→ Schema.org DefinedTerm.name） |
| `schema_subject` | 所属学科名（→ DefinedTerm.inDefinedTermSet） |
| `schema_course_name` | 所属课程名（仅 course_article，→ isPartOf.name） |

LLM **不输出** `meta_keywords`、长度字段、OG/Twitter 标签 — 这些全由后处理硬编码控制。

##### 后处理逐字段行为

| 字段 | 来源 | 具体逻辑 |
|------|------|---------|
| `title` | LLM + 硬编码 | LLM 生成 → `_ensure_brand_suffix` 确保以 `brand_suffix` 结尾 → 超长时 `_smart_truncate` 在词/句边界截断 |
| `meta_description` | LLM + 硬编码 | LLM 生成 → 超过 `max_desc_length` 时 `_smart_truncate` 截断 |
| `meta_keywords` | 硬编码 | 直接置空 `""`（Google 不使用此信号） |
| `og_title` | 硬编码 | = 处理后的 title |
| `og_description` | 硬编码 | = 处理后的 description |
| `og_url` | 硬编码 | = `base_url + path` |
| `og_type` | 硬编码 | 保留原值，无原值时默认 `"article"` |
| `twitter_title` | 硬编码 | = 处理后的 title |
| `twitter_description` | 硬编码 | = 处理后的 description |
| `schema_json_ld` | LLM + 硬编码 | 见下方 Schema.org 增强 |
| 其他字段 | 不修改 | `canonical`、`alternates`、`meta_robots`、`og_image`、`h1` 等从原始 metadata 深拷贝 |

##### Schema.org 增强细节（`_enhance_schema`）

白名单过滤 → 仅修改内容型 schema（`CONTENT_SCHEMA_TYPES`），结构型 schema（BreadcrumbList、WebSite、Organization）完全不动。

对白名单内的 schema：

| 操作 | 条件 | 值来源 |
|------|------|--------|
| `headline` = title 去品牌后缀 | 所有内容型 schema | 硬编码（从处理后 title 截取） |
| `description` = desc | 所有内容型 schema | 硬编码（同步） |
| `@type` 追加 `"LearningResource"` | `page_type == "course_article"` | 硬编码 |
| `isPartOf = {Course, name}` | `page_type == "course_article"` 且 LLM 提供了 `schema_course_name` | name 由 LLM 提供，结构硬编码 |
| `about = {DefinedTerm, name, inDefinedTermSet}` | page_type 为 course_article 或 keyword，且 LLM 提供了 `term_name` + `subject` | 值由 LLM 提供，结构硬编码 |
| `dateModified` | **不修改** | 刻意不动（pipeline 只改 meta 不改内容） |

不会做的事：不凭空创建 schema、不伪造日期、不修改结构型 schema、不修改 canonical/hreflang/robots。

##### 长度控制的双重保障

| 层 | 机制 | 目的 |
|---|------|------|
| LLM prompt | 要求 title ≤ 60、desc ≤ 155 | 让 LLM **尽量**生成合规内容 |
| 后处理硬编码 | `_ensure_brand_suffix` + `_smart_truncate` | **兜底**截断，确保最终输出不超标 |

后处理记录截断统计（`title_truncated_count` / `desc_truncated_count`），可观测 LLM 遵守长度约束的比率。

#### Schema.org 背景知识

Schema.org 是 Google/Bing/Yahoo 共同制定的结构化数据词汇表，用 JSON-LD 格式嵌入 HTML `<script type="application/ld+json">` 标签中，用户不可见但搜索引擎可读。作用是让搜索引擎精确理解页面内容类型，从而展示**富摘要（Rich Snippet）**提升 CTR。

本项目涉及的类型层级：

```
Thing → CreativeWork（内容型，本项目会修改）
         ├── Article
         ├── WebPage
         ├── LearningResource（教学资源）
         └── Course（课程）
      → DefinedTerm（术语定义）
      → Organization / WebSite / BreadcrumbList（结构型，本项目不动）
```

## steps/evaluate.py — 效果评估

evaluate 独立于 `all` 命令之外，需要单独运行。包含两个独立分析，由不同条件触发。

### 趋势分析（始终运行）

只要 `daily_pages_*.csv` 存在就运行，不依赖 `--deploy-date`。

```python
def _compute_trends(daily_df, optimized_paths=None):
    # 1. 对 daily CSV 中所有唯一路径调用 discover_subtypes()
    #    注意：在唯一路径上调用一次，再 merge 回完整 df
    # 2. 按日期聚合两个层级：
    #    - overall: 全部页面
    #    - per-subtype: 每个子类型
    # 3. 如有 optimized_paths，额外计算 _optimized_ 板块
    # 指标：总点击、总展示、加权CTR（=总点击/总展示）、平均排名、页面数
```

CTR 使用**加权计算**（总点击 / 总展示），不是各页面 CTR 的算术平均。这确保高展示量页面获得更大权重。

### 可视化

```python
def _plot_trends(seo_dir, trends, deploy_date=None):
    # 3×2 子图：有展示页面数、展示、点击、加权CTR、平均排名、CTR±1σ
    # overall 粗线 + 各子类型细线 + _optimized_ 红色虚线
    # 每个系列固定颜色（_SUBTYPE_COLORS），确保跨子图颜色一致
    # 排名图 Y 轴反转（数值小 = 排名好 → 画在上方）
    # 第 6 子图（CTR±1σ）通过 _CTR_CHART_EXCLUDE 排除噪声板块
    # deploy_date 画灰色竖线
    # _MIN_DATAPOINTS = 10：过滤掉数据点不足的小板块，避免噪声
```

matplotlib 中文显示通过 `font.sans-serif` 配置 PingFang SC / Microsoft YaHei 等字体。

### 优化前后对比（需 --deploy-date）

```python
def _evaluate_gsc_performance(daily_df, deploy_date, optimized_paths):
    # 1. 筛选 optimized_paths 对应的页面（来自 optimized_metadata.json 的 key）
    # 2. 按 deploy_date 分为 before / after 两个窗口
    # 3. 每个窗口按页面聚合，计算日均点击/展示
    # 4. inner join → 计算 ΔCTR / Δ点击 / Δ展示 / Δ排名
    # 5. 输出 top improved / declined + 聚合统计
```

### 输出文件

| 文件 | 触发条件 | 内容 |
|------|---------|------|
| `trend_report.csv` | daily CSV 存在 | 逐日分板块指标，板块列值为 `_overall_` / 子类型标签 / `_optimized_` |
| `trend_chart.png` | daily CSV 存在 | 3×2 趋势图（含 CTR±1σ 误差带子图） |
| `evaluation_report.csv` | 始终输出 | 优化页面逐页前后对比（无数据时为空占位） |
| `evaluation_summary.json` | 始终输出 | `trends`（时间序列）+ `stats`（前后对比聚合）的合并 JSON |

`trend_report.csv` 和 `evaluation_summary.json` 的趋势数据内容相同、格式不同（CSV 供人/Excel 消费，JSON 供程序消费），是有意的冗余。

**修改点：**
- 修改趋势指标：改 `_compute_trends()` 的 `_agg_daily()` 聚合逻辑。
- 修改图表样式：改 `_plot_trends()` 的 matplotlib 代码，`_MIN_DATAPOINTS` 控制小板块过滤阈值。
- 修改前后对比逻辑：改 `_evaluate_gsc_performance()` 的 delta 计算。
- 新增输出列：在 `_write_trend_csv()` 的 `fieldnames` 和行构建中添加。

## 数据流与列名对照

### ranking_pages CSV（fetch 输出）

| 列名 | 含义 | 示例 |
|------|------|------|
| `路径` | URL 路径 | `/sciencepedia/feynman/quantum` |
| `平均排名` | Google 平均排名 | `12.3` |
| `排名段` | 排名区间标签 | `"11-20 (第2页)"` |
| `优先级` | A-D 优先级 | `"C-差一点上首页"` |
| `点击` | 总点击数 | `45` |
| `展示` | 总展示数 | `12000` |
| `CTR` | 点击率 | `0.00375` |
| `完整URL` | 完整页面 URL | `https://www.bohrium.com/sciencepedia/...` |
| `页面类型` | 大类 | `"Sciencepedia"` |
| `分群` | 页面分群 | `"有需求无转化"` |

### query_page_zero_click CSV（fetch 输出）

| 列名 | 含义 |
|------|------|
| `路径` | URL 路径 |
| `查询词` | 搜索查询词 |
| `展示` | 该查询词的展示数 |
| `排名` | 该查询词的排名 |
| `页面总展示` | 该页面所有查询词展示总和 |
| `页面总点击` | 该页面总点击（= 0） |

### daily_pages CSV（fetch 输出）

| 列名 | 含义 |
|------|------|
| `日期` | 日期 |
| `路径` | URL 路径 |
| `点击` | 当天点击数 |
| `展示` | 当天展示数 |
| `CTR` | 当天点击率 |
| `平均排名` | 当天平均排名 |
| `页面类型` | 大类 |

### trend_report CSV（evaluate 输出）

| 列名 | 含义 | 示例 |
|------|------|------|
| `日期` | 日期 | `2026-03-10` |
| `板块` | 分组标签 | `_overall_` / `sciencepedia/feynman` / `_optimized_` |
| `点击` | 当日总点击 | `56` |
| `展示` | 当日总展示 | `13315` |
| `平均CTR` | 加权 CTR（= 点击/展示） | `0.004206` |
| `平均排名` | 各页面平均排名 | `7.05` |
| `页面数` | 当日有数据的页面数 | `569` |

### priority_ranked CSV（rank 输出）

| 列名 | 含义 |
|------|------|
| `路径` | URL 路径 |
| `subtype` | 自动发现的子类型（analyze 报告中的标签） |
| `seo_page_type` | 由 `seo.subtype_page_types` 配置映射（course_article / keyword / agent_tool / other） |
| `language` | 语言（zh / en） |
| `priority_score` | 优化价值分 |
| `展示` / `CTR` / `平均排名` | GSC 指标 |
| `优先级` | A-D 优先级 |
| `query_count` | 关联查询词数量 |

## 开发约定

- 每个步骤模块必须暴露 `run(config: dict, output_dir: Path) -> dict`
- 返回值必须包含 `output_files` 和 `summary` 两个 key
- 步骤间通过文件通信，不共享内存状态
- CSV 统一用 `utf-8-sig` 编码（兼容 Excel）
- 日志用 `logging.getLogger(__name__)`，不直接 print（控制台报告除外）
- 共享工具函数放在 `steps/_classify.py`，避免步骤间的交叉导入
- `seo.base_url` 是必填配置，crawl 和 optimize 在缺失时会报错
- Schema.org 增强只修改已有的内容型 schema，不凭空创建，不伪造日期
