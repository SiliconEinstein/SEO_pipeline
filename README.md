# SEO Pipeline

端到端的 SEO 数据处理流水线 + Claude Code Skill，用于批量检测和优化网站 SEO 元数据。

## 它能解决什么问题

很多网站（尤其是 SPA）虽然有 SEO 元数据，但质量堪忧：description 超长被 Google 截断、中文页面却用英文元数据、title 千篇一律以 "Explore" 开头、关键词与用户实际搜索词不匹配……这些问题直接导致搜索结果中的点击率低下。

本工具的做法是：**用脚本自动检测问题，用大模型批量修复问题。**

## 工作流程

```
                         脚本自动化                          LLM 驱动
              ┌─────────────────────────────┐    ┌──────────────────────┐
              │                             │    │                      │
  config.yaml │ fetch → rank → crawl → audit│    │   /seo-optimize      │
              │                             │    │                      │
              └──────────┬──────────────────┘    └──────────┬───────────┘
                         │                                  │
                         ▼                                  ▼
              output/gsc/*.csv                   output/seo/optimized_metadata.json
              output/seo/priority_ranked.csv     output/seo/original_metadata_backup.json
              output/seo/existing_metadata.json
              output/seo/audit_report.csv
```

- **左侧 4 步**通过 CLI 一键运行，不需要 AI，纯数据处理
- **右侧 Skill**在 Claude Code 中调用，由大模型根据审计结果智能重写元数据

## 目录结构

```
.
├── .claude/skills/seo-optimize/       # Claude Code Skill（LLM 重写）
│   ├── SKILL.md                       #   skill 主指令 + frontmatter
│   ├── templates/rewrite-prompt.md    #   agent 重写 prompt 模板
│   ├── scripts/prepare_contexts.py    #   数据加载 + 上下文构建脚本
│   ├── scripts/postprocess.py         #   后处理 + 增量输出脚本
│   └── examples/sample-output.json    #   输出格式示例
├── steps/                             # Pipeline 步骤模块
│   ├── fetch_gsc.py                   #   Step 1: GSC 数据拉取
│   ├── rank.py                        #   Step 2: 优先级排名
│   ├── crawl.py                       #   Step 3: 元数据抓取
│   └── audit.py                       #   Step 4: 质量审计
├── main.py                            # CLI 统一入口
├── pyproject.toml                     # 项目依赖
├── config.yaml.example                # 配置模板（新用户参考）
├── config.yaml                        # 实际配置（不提交 git）
├── .gitignore
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
# 所有操作都在本目录（seo_pipeline/）下执行
uv sync
```

> 需要 Python ≥ 3.12。

### 2. 配置

```bash
cp config.yaml.example config.yaml
```

编辑 `config.yaml`，修改以下 4 项：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `site_url` | 你在 Google Search Console 中的站点 URL | `"sc-domain:example.com"` |
| `credentials_file` | OAuth 凭证文件名（放在本目录下） | `"client_secret_xxx.json"` |
| `seo.base_url` | 你的网站域名 | `"https://www.example.com"` |
| `seo.brand_suffix` | 品牌后缀，会出现在 title 末尾 | `" | MyBrand"` |

可选配置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `date_range` | GSC 数据的时间范围 | `"30d"` |
| `seo.max_title_length` | title 超过此长度视为过长 | `60` |
| `seo.max_desc_length` | description 超过此长度视为过长 | `155` |
| `seo.crawl_concurrency` | 抓取并发数 | `20` |
| `seo.page_filter` | 只处理路径包含此字符串的页面 | `""` (全部) |
| `seo.exclude_patterns` | 排除路径包含这些字符串的页面 | `[]` |

### 3. 获取 OAuth 凭证

1. 进入 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建项目（或选择已有项目）
3. 启用 **Google Search Console API**
4. 进入 **APIs & Services → Credentials**
5. 创建 **OAuth 2.0 Client ID**（应用类型选 Desktop App）
6. 下载 JSON 文件，放到本目录下（与 `main.py` 同级）
7. 将文件名填入 `config.yaml` 的 `credentials_file`

首次运行 `fetch` 时会弹出浏览器完成 OAuth 授权，授权后自动保存 `token.json`，后续运行不再需要浏览器。

### 4. 运行 Pipeline

```bash
# 执行全部 4 步
uv run python main.py all

# 如果已有 GSC 数据，跳过 fetch（最常用）
uv run python main.py all --skip fetch

# 只执行某一步
uv run python main.py fetch
uv run python main.py rank
uv run python main.py crawl
uv run python main.py audit

# 查看详细日志
uv run python main.py all -v
```

### 5. 用 Skill 优化元数据

在 Claude Code 中：

```
/seo-optimize          # 优化 Top 30 页面
/seo-optimize 50       # 优化 Top 50
/seo-optimize 31-60    # 增量处理排名 31-60
```

---

## 各步骤详解

### Step 1: fetch (`steps/fetch_gsc.py`)

**功能**：通过 Google Search Console API 拉取搜索数据。

**做了什么**：
1. 使用 OAuth 2.0 认证连接 GSC API
2. 拉取 `query × page` 维度数据（每个页面被哪些查询词触发、各自的展示量/点击量/排名）
3. 拉取 `page` 维度汇总数据（每个页面的总展示/点击/平均排名）
4. 对页面进行类型分类（Sciencepedia / Paper / Apps / Blog 等）
5. 标注排名段（1-3 首页顶部 / 4-10 首页 / 11-20 第二页 / …）和优先级标签
6. 筛选零点击页面（有展示但零点击）

**输出**：
| 文件 | 说明 |
|------|------|
| `output/gsc/query_page_zero_click_{filter}_{date}.csv` | 零点击页面的查询词明细。每行是一个 (页面, 查询词) 组合 |
| `output/gsc/ranking_pages_{filter}_{date}.csv` | 页面级排名报告。包含排名段、优先级、点击/展示等指标 |

**关键设计**：
- 文件名包含日期戳，多次运行不会覆盖旧数据
- API 请求自动分页（每页最多 25,000 行）
- 支持 `sc-domain:` 和 `https://` 两种 GSC 站点格式

---

### Step 2: rank (`steps/rank.py`)

**功能**：对 GSC 数据中的页面按 SEO 优化价值进行优先级排名。

**做了什么**：
1. 加载 fetch 步骤生成的两个 CSV（自动选取最新日期，并校验日期一致性）
2. 合并数据：为每个页面关联其 Top 查询词列表和独立查询词数量
3. 分类页面类型：
   - `course_article`：课程文章页（如 `/feynman/genetics-dna_replication`）
   - `keyword`：关键词页（如 `/feynman/keyword/entropy`）
   - `agent_tool`：工具页（排除，不优化）
4. 计算优先级评分（Opportunity Score）：

```
priority_score = impressions × (1 - CTR)
```

这是 SEO 行业标准的机会分公式：展示量高但点击率低的页面代表最大的未开发潜力。对于零点击页面（CTR = 0），公式简化为按展示量排序。

5. 过滤：只保留 `course_article` 和 `keyword` 类型、且有查询词匹配的页面
6. 按评分降序排列

**输出**：
| 文件 | 说明 |
|------|------|
| `output/seo/priority_ranked.csv` | 优先级排名表。包含路径、页面类型、语言、评分、展示量、CTR、排名等 |

---

### Step 3: crawl (`steps/crawl.py`)

**功能**：异步批量抓取每个页面的现有 SEO 元数据，用于后续审计和对比。

**做了什么**：
1. 读取 `priority_ranked.csv` 中的所有页面路径
2. 使用 aiohttp 异步并发抓取每个页面的 HTML（默认 20 并发）
3. 用 BeautifulSoup 从 HTML 中解析以下 SEO 元素：

| 类别 | 提取内容 |
|------|----------|
| 基础 Meta | `<title>`, meta description, meta keywords, meta robots, meta author |
| Open Graph | og:title, og:description, og:url, og:type, og:image, og:site_name 等 8 个字段 |
| Twitter Card | twitter:card, twitter:title, twitter:description, twitter:image, twitter:site |
| 链接 | canonical URL, alternate/hreflang 链接 |
| Schema.org | 所有 `<script type="application/ld+json">` 块（搜索整个文档，不仅限 `<head>`） |
| 内容 | 所有 `<h1>` 标签文本 |

4. 统计元数据覆盖率（每种字段有多少页面有值）

**输出**：
| 文件 | 说明 |
|------|------|
| `output/seo/existing_metadata.json` | 所有页面的完整 SEO 元数据，key 为路径 |
| `output/seo/crawl_report.csv` | 抓取状态报告，包含每个页面的 HTTP 状态码、耗时、各字段是否存在 |

**关键设计**：
- Schema.org JSON-LD 搜索整个文档而非仅 `<head>`（部分网站将结构化数据放在 `<body>` 中）
- 30 秒超时 / 单页，抓取失败不中断整个流程
- 保持原始优先级排序

**性能参考**：635 页面，20 并发，约 46 秒完成（~14 pages/s）

---

### Step 4: audit (`steps/audit.py`)

**功能**：对现有元数据执行 6 项质量检测规则，生成审计报告。

**做了什么**：

对每个页面逐一检测以下 6 类问题：

| # | 规则 | 判定条件 | 为什么是问题 |
|---|------|----------|-------------|
| 1 | `desc_too_long` | description > 155 字符 | Google 搜索结果会截断，用户看到不完整的描述 |
| 2 | `title_too_long` | title > 60 字符 | Google 搜索结果会截断，关键信息被隐藏 |
| 3 | `generic_opening` | description 以通用词开头 | "Explore", "Learn", "了解" 等不传递具体信息，用户会跳过 |
| 4 | `language_mismatch` | 中文路径但元数据全英文 | 搜索引擎可能降权，用户体验差 |
| 5 | `missing_keywords` | Top 3 查询词未覆盖 | 查询词命中可触发 Google 加粗高亮，提升点击率 |
| 6 | `schema:*` | Schema.org 缺失关键字段 | 缺少 datePublished/dateModified 影响新鲜度信号；课程页缺 LearningResource 失去富摘要机会 |

**关键词覆盖检测逻辑**：
- 取每个页面展示量最高的 3 个查询词
- 将 title + description + keywords 合并为搜索文本
- 查询词拆为词组，若 60% 以上的词出现在文本中视为"已覆盖"
- 未覆盖的查询词列入报告

**输出**：
| 文件 | 说明 |
|------|------|
| `output/seo/audit_report.csv` | 逐页审计报告，按优先级排序。包含每页的问题列表、各项原始值 |
| `output/seo/audit_summary.json` | 汇总统计：问题分布、title/description 长度统计 |

**重要**：audit 只检测问题，不生成修复建议。修复由 `/seo-optimize` skill 交给大模型处理。

---

### Skill: `/seo-optimize` (`.claude/skills/seo-optimize/`)

**功能**：基于审计报告，用大模型批量重写 SEO 元数据。

**前置条件**：需要先运行 pipeline（至少完成 rank + crawl + audit）。

**做了什么**：
1. 加载审计报告、现有元数据、查询词数据、优先级排名
2. 按优先级取 Top N 个页面，打包每个页面的上下文（现有元数据 + 问题列表 + 查询词 + 页面类型 + 语言）
3. 分批（每 10 页一批）使用 Agent 并行重写 title、description、keywords 和 Schema.org 语义字段
4. 后处理：同步 OG/Twitter 标签、更新 keywords、增强 Schema.org
5. 输出优化后的完整元数据 + 原始元数据（同格式，便于 diff）

其中步骤 1-2 和步骤 4-5 由两个辅助脚本完成：

#### `scripts/prepare_contexts.py` — 数据加载与上下文构建

将 4 个 pipeline 输出文件解析合并，为每个目标页面生成重写上下文。

```bash
uv run python .claude/skills/seo-optimize/scripts/prepare_contexts.py --top 30        # 默认 Top 30
uv run python .claude/skills/seo-optimize/scripts/prepare_contexts.py --top 50        # Top 50
uv run python .claude/skills/seo-optimize/scripts/prepare_contexts.py --range 31-60   # 排名 31-60
```

**处理逻辑**：
1. 从 `priority_ranked.csv` 中按指定范围提取目标页面（处理 `utf-8-sig` BOM 编码）
2. 从 `audit_report.csv` 中匹配每个页面的问题列表
3. 从 `existing_metadata.json`（约 6MB）中提取现有元数据；找不到的页面跳过并报告
4. 从最新的 `query_page_zero_click_*.csv` 中提取每页 Top 5 查询词（按展示量排序）；零点击 CSV 的 `路径` 列可能是完整 URL，脚本自动用 `urlparse` 提取 path
5. 将结果分批输出到 `/tmp/seo_batch_*.json`（每批 10 个），供 Agent 并行消费

**额外参数**：`--batch-size`（每批页面数，默认 10）、`--output-dir`（pipeline 输出目录，默认 `output`）

#### `scripts/postprocess.py` — 后处理与增量输出

将 Agent 重写结果与原始元数据合并，补全所有 SEO 字段。

```bash
uv run python .claude/skills/seo-optimize/scripts/postprocess.py --rewritten /tmp/seo_rewritten.json
```

**处理逻辑**：
1. **长度校验** — title > 60 或 desc > 155 字符时在词/句边界智能截断（中英文分别处理）
2. **品牌后缀** — 确保 title 以 ` | SciencePedia` 结尾
3. **OG 标签同步** — og_title / og_description / og_url / og_type 与页面元数据一致；保留原有 og_image、og_site_name
4. **Twitter 标签同步** — twitter_title / twitter_description 同步；保留原有 twitter_card、twitter_image、twitter_site
5. **Keywords 更新** — 优先使用 LLM 从现有 keywords + 查询词中精选生成 5-8 个关键词，fallback 到 Top 5 查询词拼接
6. **Schema.org 增强** — 更新 headline（去品牌后缀）和 description；补充 datePublished / dateModified；course_article 页面添加 `LearningResource` type 和 `isPartOf.Course`；keyword 页面添加 `about.DefinedTerm`；术语名（`about.name`）和学科（`about.inDefinedTermSet`）由 LLM 生成精确语义值（替代机械复制 headline 和硬编码 "Science"）
7. **增量合并** — 如果 `optimized_metadata.json` 已存在（如之前跑过 Top 30），先读取再 merge，相同 path 以本次为准

脚本结束后自动打印统计摘要：处理/跳过页数、各问题修复数量、title/description 长度变化、语言修正数量。

**重写规则**：
- Title ≤ 60 字符，含主关键词，以品牌后缀结尾
- Description ≤ 155 字符，覆盖 top 查询词，含行动引导
- 禁止 generic opener（Explore/Learn/Discover/探索/学习/了解）
- 语言与页面匹配（中文路径用中文，英文路径用英文）
- 保持与页面内容的语义相关性

**输出**：
| 文件 | 说明 |
|------|------|
| `output/seo/optimized_metadata.json` | 优化后的完整元数据（含 OG/Twitter/Schema），支持增量累积 |
| `output/seo/original_metadata_backup.json` | 本次处理的原始元数据（同格式，便于对比） |

---

## 输出文件总览

```
output/
├── gsc/                                          # GSC 原始数据
│   ├── query_page_zero_click_{filter}_{date}.csv  #   零点击页面查询词明细
│   └── ranking_pages_{filter}_{date}.csv          #   页面排名报告
├── seo/                                          # SEO 分析结果
│   ├── priority_ranked.csv                        #   优先级排名
│   ├── existing_metadata.json                     #   现有元数据（635 页）
│   ├── crawl_report.csv                           #   抓取状态报告
│   ├── audit_report.csv                           #   审计报告
│   ├── audit_summary.json                         #   审计汇总统计
│   ├── optimized_metadata.json                    #   优化后元数据（skill 输出）
│   └── original_metadata_backup.json                #   原始元数据对照（skill 输出）
└── reports/                                      # 可视化报告（预留）
```

## 配置详解

完整配置说明见 `config.yaml.example`。以下补充几个关键配置的使用场景：

**`page_filter`**：当你的网站有多种内容类型，但只想优化其中一种时使用。例如设为 `"blog"` 则只处理 URL 中包含 `/blog/` 的页面。留空处理全部。

**`exclude_patterns`**：排除不需要 SEO 优化的页面。例如 `["admin", "test", "draft"]` 会跳过包含这些字符串的路径。

**`crawl_concurrency`**：异步抓取的并发连接数。如果你的网站服务器性能有限或有反爬措施，建议调低到 5-10。

**`date_range`**：GSC 数据的时间范围。范围越长数据越稳定，但近期变化会被稀释。推荐 `"28d"` 或 `"30d"`。

## 常见问题

**Q: fetch 步骤首次运行弹出浏览器怎么办？**
A: 这是 Google OAuth 授权流程，用浏览器登录你的 Google 账号并授权即可。授权后 `token.json` 会保存在本目录下，后续运行不再需要浏览器。Token 过期后会自动刷新。

**Q: 已有 GSC 数据，不想重新拉取？**
A: 将 CSV 文件放到 `output/gsc/` 目录下，然后 `uv run python main.py all --skip fetch`。

**Q: rank 步骤报 "Date mismatch" 错误？**
A: `output/gsc/` 中的两个 CSV 文件日期不一致。重新运行 `uv run python main.py fetch` 生成同日期的数据。

**Q: crawl 步骤很慢？**
A: 可以在 `config.yaml` 中调高 `seo.crawl_concurrency`（默认 20），但注意不要超过目标网站的承受能力。

**Q: optimized_metadata.json 如何使用？**
A: 这个 JSON 文件包含每个页面的完整 SEO 元数据，key 为 URL 路径。你的前端需要在 SSR 时读取此文件，将对应的 title/description/OG/Schema 注入到 HTML `<head>` 中。
