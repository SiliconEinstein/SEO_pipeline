# SEO Pipeline

端到端 SEO 数据处理流水线，自动检测并优化网站 SEO 元数据。通过 Google Search Console 数据定位高价值页面，用 LLM 批量重写 title / description / keywords / Schema.org。

## 快速开始

### 1. 安装依赖

```bash
uv sync    # 需要 Python >= 3.12
```

### 2. 配置

```bash
cp config.yaml.example config.yaml   # Pipeline 配置
cp .env.example .env                  # LLM API 凭据
```

编辑 `config.yaml`，填入你的站点信息：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `site_url` | GSC 中的站点 URL | `"sc-domain:example.com"` |
| `credentials_file` | OAuth 凭证文件名 | `"client_secret_xxx.json"` |
| `seo.base_url` | 网站域名 | `"https://www.example.com"` |
| `seo.brand_suffix` | 品牌后缀 | `" \| MyBrand"` |

编辑 `.env`，填入 LLM API 凭据（optimize 步骤需要）：

```
LITELLM_PROXY_API_BASE=https://your-litellm-proxy.example.com
LITELLM_PROXY_API_KEY=sk-xxx
```

### 3. 获取 OAuth 凭证

1. [Google Cloud Console](https://console.cloud.google.com/) → 启用 **Search Console API**
2. **Credentials** → 创建 **OAuth 2.0 Client ID**（Desktop App）
3. 下载 JSON 文件放到项目根目录，文件名填入 `config.yaml` 的 `credentials_file`

首次运行 `fetch` 时会弹出浏览器完成 OAuth 授权。

## Pipeline 步骤

```
fetch → rank → crawl → audit → optimize
 GSC数据   优先级排名   元数据抓取   质量审计    LLM重写
```

| 步骤 | 说明 | 输出 |
|------|------|------|
| `fetch` | 通过 GSC API 拉取搜索数据 | `output/gsc/*.csv` |
| `rank` | 按 SEO 优化价值排序页面 | `output/seo/priority_ranked.csv` |
| `crawl` | 异步抓取现有 SEO 元数据 | `output/seo/existing_metadata.json` |
| `audit` | 6 项质量规则检测 | `output/seo/audit_report.csv` |
| `optimize` | LLM 批量重写元数据 | `output/seo/optimized_metadata.json` |

## 常用命令

```bash
uv run python main.py all                    # 全部 5 步
uv run python main.py all --skip fetch       # 跳过 GSC 拉取（已有数据时）
uv run python main.py all --skip optimize    # 只做检测，不调 LLM
uv run python main.py audit                  # 只运行审计
uv run python main.py optimize --top 10      # 只优化 Top 10
uv run python main.py optimize --range 31-60 # 增量优化排名 31-60
```

## 项目结构

```
.
├── main.py                 # CLI 入口
├── steps/                  # 5 个 pipeline 步骤
│   ├── fetch_gsc.py
│   ├── rank.py
│   ├── crawl.py
│   ├── audit.py
│   └── optimize.py
├── templates/
│   └── rewrite-prompt.md   # LLM 重写 prompt 模板
├── config.yaml.example     # 配置模板
├── .env.example            # API 凭据模板
├── pyproject.toml
└── output/                 # 运行输出（不提交 git）
```

## 配置说明

完整配置见 `config.yaml.example`。关键选项：

- `seo.page_filter` — 只处理路径含此字符串的页面，留空处理全部
- `seo.exclude_patterns` — 排除路径含这些字符串的页面
- `optimize.model` — LiteLLM 模型名称
- `optimize.concurrency` — 并发 API 请求数
- `optimize.top` — 默认处理的 Top N 页面数
