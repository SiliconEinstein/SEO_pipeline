# SEO Pipeline

端到端的 SEO 数据处理流水线 + Claude Code Skill，用于批量检测和优化网站 SEO 元数据。

## 项目结构

- `main.py` — CLI 入口，支持 `fetch` / `rank` / `crawl` / `audit` / `all` 子命令
- `steps/` — 4 个 pipeline 步骤模块，每个导出 `run(config, output_dir)` 接口
- `.claude/skills/seo-optimize/` — Claude Code Skill，用 LLM 批量重写元数据
- `config.yaml` — 运行配置（不提交 git，从 `config.yaml.example` 复制）

## 运行约定

- 所有命令从本目录（`seo_pipeline/`）下执行
- 使用 `uv run python main.py <command>` 运行 pipeline
- 使用 `/seo-optimize` 调用 Skill 进行 LLM 重写
- 输出文件在 `output/` 目录下

## 常用命令

```bash
uv run python main.py all              # 执行全部步骤
uv run python main.py all --skip fetch  # 跳过 GSC 数据拉取
uv run python main.py audit            # 只运行审计
```

## 设计原则

- 脚本只检测问题，不生成修复建议；修复由 `/seo-optimize` skill 交给大模型处理
- 每个 step 模块是独立的，通过 `run(config: dict, output_dir: Path) -> dict` 统一接口
- 页面类型分类和过滤规则在 `config.yaml` 中配置
