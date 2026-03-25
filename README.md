# SEO Pipeline

端到端 SEO 数据处理流水线。从 Google Search Console 拉取数据，自动分析页面分布，定位高价值页面，用 LLM 批量重写 title / description / keywords / Schema.org。

## 快速开始

### 1. 安装

```bash
uv sync    # 需要 Python >= 3.12
```

### 2. 配置

```bash
cp config.yaml.example config.yaml
cp .env.example .env
```

编辑 `config.yaml`：

| 必填配置 | 说明 | 示例 |
|----------|------|------|
| `site_url` | GSC 中的站点 URL | `"sc-domain:example.com"` |
| `credentials_file` | OAuth 凭证文件名 | `"client_secret_xxx.json"` |
| `seo.base_url` | 网站域名 | `"https://www.example.com"` |
| `seo.brand_suffix` | 品牌后缀 | `" \| MyBrand"` |

编辑 `.env`（optimize 步骤需要）：

```
LITELLM_PROXY_API_BASE=https://your-litellm-proxy.example.com
LITELLM_PROXY_API_KEY=sk-xxx
```

### 3. OAuth 凭证

1. [Google Cloud Console](https://console.cloud.google.com/) → 启用 **Search Console API**
2. **Credentials** → 创建 **OAuth 2.0 Client ID**（Desktop App）→ 下载 JSON 放到项目根目录

首次运行 `fetch` 时会弹出浏览器完成授权，token 自动缓存。

## 使用方法

### 推荐流程：先分析再优化

```bash
# 第一步：拉取数据 + 分析
uv run python main.py fetch
uv run python main.py analyze

# 查看 analyze 报告，找到要优化的子类型
# 编辑 config.yaml：
#   seo.include_subtypes: ["feynman", "feynman/keyword"]
#   seo.subtype_page_types: {"feynman": "course_article", "feynman/keyword": "keyword"}

# 第二步：跑完整 pipeline（跳过已完成的 fetch）
uv run python main.py all --skip fetch analyze
```

### 常用命令

```bash
# 完整 pipeline（6 步全跑）
uv run python main.py all

# 跳过某些步骤
uv run python main.py all --skip fetch          # 已有 GSC 数据
uv run python main.py all --skip fetch analyze   # 已有数据且已分析
uv run python main.py all --skip optimize        # 只检测不优化

# 单步运行
uv run python main.py fetch
uv run python main.py analyze
uv run python main.py rank
uv run python main.py crawl
uv run python main.py audit
uv run python main.py optimize
uv run python main.py evaluate

# optimize 专用参数
uv run python main.py optimize --top 10          # 只优化 Top 10
uv run python main.py optimize --range 31-60     # 增量优化排名 31-60

# 调试
uv run python main.py fetch -v                   # 详细日志
```

## Pipeline 流程

```
fetch ──→ analyze ──→ rank ──→ crawl ──→ audit ──→ optimize
  │          │          │         │         │          │
  │          │          │         │         │          ▼
  │          │          │         │         │     optimized_metadata.json
  │          │          │         │         ▼          │
  │          │          │         │     audit_report.csv │
  │          │          │         ▼                     │
  │          │          │     existing_metadata.json    │
  │          │          ▼                               │
  │          │     priority_ranked.csv                  │
  │          ▼                                         │
  │     site_analysis.csv/json     evaluate ◄──────────┘
  ▼                                    │
ranking_pages_*.csv                    ▼
query_page_zero_click_*.csv       trend_report.csv
daily_pages_*.csv                 trend_chart.png
                                  evaluation_report.csv
                                  evaluation_summary.json
```

### 各步骤说明

#### 1. fetch — 拉取 GSC 数据

从 Google Search Console API 拉取搜索分析数据。

| | |
|---|---|
| **输入** | GSC API（OAuth 认证） |
| **输出** | `output/gsc/ranking_pages_{filter}_{date}.csv` — 页面级排名数据 |
| | `output/gsc/query_page_zero_click_{filter}_{date}.csv` — 零点击页面及查询词 |
| **配置** | `site_url`, `credentials_file`, `date_range`, `seo.page_filter`, `seo.exclude_patterns` |

`page_filter` 控制范围：设为 `"sciencepedia"` 则只拉取路径含 sciencepedia 的页面。

---

#### 2. analyze — 数据分析

从 URL 路径结构**自动发现子类型**，按子类型分组生成分析报告。

| | |
|---|---|
| **输入** | `output/gsc/ranking_pages_*.csv`, `output/gsc/query_page_zero_click_*.csv` |
| **输出** | `output/analyze/site_analysis.csv` — 子类型汇总表 |
| | `output/analyze/site_analysis.json` — 完整分析（含各维度明细） |
| | 控制台报告 |

**分析维度：** 子类型分布（页面数、展示、点击、加权CTR、CTR方差、加权排名、机会分）、零点击分析、排名段分布、语言分布。

**自动发现算法：** 取 URL 目录部分 → 去掉公共前缀 → 剩余路径即为子类型标签。例如 `page_filter: "sciencepedia"` 时发现 `feynman`、`feynman/keyword`、`agent-tools` 等。

根据报告设置 `seo.include_subtypes` 告诉后续步骤优化哪些子类型，设置 `seo.subtype_page_types` 指定各子类型的页面类型（影响 Schema.org 增强策略）。

---

#### 3. rank — 优先级排名

计算每个页面的优化价值，按 `seo.include_subtypes` 筛选，输出排序后的页面列表。

| | |
|---|---|
| **输入** | `output/gsc/ranking_pages_*.csv`, `output/gsc/query_page_zero_click_*.csv` |
| **输出** | `output/seo/priority_ranked.csv` |
| **配置** | `seo.include_subtypes`（留空 = 全部子类型）, `seo.subtype_page_types` |

**排序公式：** `priority_score = impressions × (1 - CTR)`，高展示低点击率的页面排在前面。仅保留有查询词的页面。

---

#### 4. crawl — 抓取现有元数据

异步抓取 `priority_ranked.csv` 中每个页面的当前 SEO 元数据。

| | |
|---|---|
| **输入** | `output/seo/priority_ranked.csv` |
| **输出** | `output/seo/existing_metadata.json` — 各页面的 title / description / OG / Schema.org 等 |
| | `output/seo/crawl_report.csv` — 抓取状态与耗时 |
| **配置** | `seo.base_url`, `seo.crawl_concurrency` |

---

#### 5. audit — 质量审计

对现有元数据执行 6 条规则检测。

| | |
|---|---|
| **输入** | `output/seo/existing_metadata.json`, `output/seo/priority_ranked.csv`, `output/gsc/query_page_zero_click_*.csv` |
| **输出** | `output/seo/audit_report.csv` — 每页问题详情 |
| | `output/seo/audit_summary.json` — 问题聚合统计 |
| **配置** | `seo.max_title_length`, `seo.max_desc_length` |

**6 条规则：** title 超长、description 超长、通用开头词（Explore/学习/...）、中英文不匹配、缺少查询关键词、Schema.org 结构问题。

---

#### 6. optimize — LLM 重写

整合前 5 步数据，通过 LLM 批量重写 SEO 元数据，后处理确保符合规范。


| | |
|---|---|
| **输入** | `output/seo/priority_ranked.csv`, `output/seo/existing_metadata.json`, `output/seo/audit_report.csv`, `output/gsc/query_page_zero_click_*.csv` |
| **输出** | `output/seo/optimized_metadata.json` — 优化后的元数据（可部署） |
| | `output/seo/original_metadata_backup.json` — 原始备份 |
| **配置** | `optimize.*` 全部配置项，`seo.base_url`, `seo.brand_suffix` |

支持断点续传：已处理的批次会跳过。支持增量运行：多次运行结果会合并。

> **换站点提示：** `templates/rewrite-prompt.md` 中的平台名称和品牌后缀是硬编码的，换站点时需要手动修改，与 `config.yaml` 中的 `seo.brand_suffix` 保持一致。

---

#### 7. evaluate — 效果评估

跟踪 GSC 指标变化趋势，评估优化效果。包含两个独立分析：

- **趋势分析**（无需 `--deploy-date`）：按 `discover_subtypes` 分板块的逐日点击、展示、加权 CTR、平均排名
- **优化前后对比**（需 `--deploy-date`）：优化页面在部署前后的指标变化

| | |
|---|---|
| **输入** | `output/gsc/daily_pages_*.csv`（fetch 输出），`output/seo/optimized_metadata.json`（optimize 输出） |
| **输出** | `output/seo/trend_report.csv` — 逐日分板块指标 |
| | `output/seo/trend_chart.png` — 3×2 趋势图（页面数、展示、点击、CTR、排名、CTR±1σ） |
| | `output/seo/evaluation_report.csv` — 优化页面逐页前后对比 |
| | `output/seo/evaluation_summary.json` — 完整评估数据 |
| **配置** | `evaluate.deploy_date`（或 CLI `--deploy-date`） |

```bash
# 仅看趋势（不需要 deploy-date）
uv run python main.py evaluate

# 同时看趋势 + 优化前后对比
uv run python main.py evaluate --deploy-date 2026-03-10
```

## 配置参考

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `date_range` | `"30d"` | GSC 数据时间范围 |
| `seo.page_filter` | `""` | 路径子串过滤，留空处理全部 |
| `seo.exclude_patterns` | `[]` | 排除路径含这些字符串的页面 |
| `seo.include_subtypes` | `[]` | 要优化的子类型（运行 analyze 查看可选值） |
| `seo.subtype_page_types` | `{}` | 子类型 → 页面类型映射，影响 Schema.org 增强和审计规则 |
| `seo.crawl_concurrency` | `20` | 异步抓取并发数 |
| `seo.max_title_length` | `60` | title 审计阈值 |
| `seo.max_desc_length` | `155` | description 审计阈值 |
| `optimize.model` | `"claude-sonnet-4-5"` | LiteLLM 模型名称 |
| `optimize.top` | `30` | 默认处理 Top N 页面 |
| `optimize.batch_size` | `10` | 每批页面数 |
| `optimize.concurrency` | `3` | 并发 API 请求数 |
| `optimize.temperature` | `0.3` | 生成温度 |
| `optimize.max_retries` | `2` | 单批重试次数 |

## 输出文件一览

| 步骤 | 文件 | 格式 | 说明 |
|------|------|------|------|
| fetch | `output/gsc/ranking_pages_*.csv` | CSV | 页面排名、分类、分群数据 |
| fetch | `output/gsc/query_page_zero_click_*.csv` | CSV | 零点击页面及查询词 |
| analyze | `output/analyze/site_analysis.csv` | CSV | 按子类型汇总的分析数据 |
| analyze | `output/analyze/site_analysis.json` | JSON | 完整分析结果 |
| rank | `output/seo/priority_ranked.csv` | CSV | 按优化价值排序的页面列表 |
| crawl | `output/seo/existing_metadata.json` | JSON | 各页面当前 SEO 元数据 |
| crawl | `output/seo/crawl_report.csv` | CSV | 抓取状态与耗时 |
| audit | `output/seo/audit_report.csv` | CSV | 每页问题详情 |
| audit | `output/seo/audit_summary.json` | JSON | 问题聚合统计 |
| optimize | `output/seo/optimized_metadata.json` | JSON | 优化后的元数据（可部署） |
| optimize | `output/seo/original_metadata_backup.json` | JSON | 原始元数据备份 |
| evaluate | `output/seo/trend_report.csv` | CSV | 逐日分板块指标（总体 + 各子类型 + 已优化） |
| evaluate | `output/seo/trend_chart.png` | PNG | 3×2 趋势可视化图表 |
| evaluate | `output/seo/evaluation_report.csv` | CSV | 优化页面逐页前后对比 |
| evaluate | `output/seo/evaluation_summary.json` | JSON | 完整评估数据（趋势 + 对比统计） |

## 项目结构

```
.
├── main.py                 # CLI 入口
├── steps/
│   ├── _classify.py        # 共享工具：URL 子类型自动发现
│   ├── fetch_gsc.py        # Step 1: GSC 数据拉取
│   ├── analyze.py          # Step 2: 数据分析
│   ├── rank.py             # Step 3: 优先级排名
│   ├── crawl.py            # Step 4: 元数据抓取
│   ├── audit.py            # Step 5: 质量审计
│   ├── optimize.py         # Step 6: LLM 重写
│   └── evaluate.py         # Step 7: 效果评估
├── templates/
│   └── rewrite-prompt.md   # LLM prompt 模板
├── config.yaml.example     # 配置模板
├── .env.example            # API 凭据模板
└── output/                 # 运行输出（不提交 git）
    ├── gsc/                # fetch 输出
    ├── analyze/            # analyze 输出
    └── seo/                # rank ~ optimize 输出
        └── tmp/            # optimize 中间文件
```
