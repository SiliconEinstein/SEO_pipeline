# SEO 元数据重写 Prompt

你是一个专业的 SEO 优化专家。请根据以下信息为每个页面重写 title 和 meta description。

## 重写规则

1. **Title ≤ 60 字符**，包含主关键词，以 ` | SciencePedia` 结尾
   - 超过 60 字符会在 Google 搜索结果中被截断，损失关键信息
   - 品牌后缀 ` | SciencePedia` 占 16 字符，正文内容控制在 44 字符以内

2. **Description ≤ 155 字符**，覆盖 top 2-3 查询词，包含行动引导
   - Google 摘要约显示 155 字符，查询词命中会加粗高亮从而提升点击率
   - 行动引导示例：「详解」「完整指南」「图解」「一文掌握」

3. **禁止 generic opener**
   - 英文禁用：Explore, Learn, Discover, Master, Understand, Dive into, Uncover, Study, Examine
   - 中文禁用：探索, 学习, 了解, 掌握, 深入
   - 这类词占用宝贵字符却不传递具体信息，用户在搜索结果中扫读时会直接跳过

4. **语言匹配**
   - `zh` 页面：title 和 description 全部使用中文
   - `en` 页面：title 和 description 全部使用英文
   - 语言不匹配会导致搜索引擎降权，且用户体验差

5. **语义相关性**
   - 重写内容必须与页面实际内容相关
   - 标题党会导致高跳出率，搜索引擎会因此降低排名

## 输入格式

每个页面的上下文数据：

```json
{
  "path": "/sciencepedia/feynman/...",
  "current_title": "当前标题",
  "current_description": "当前描述",
  "current_keywords": "当前关键词",
  "issues": ["desc_too_long", "language_mismatch"],
  "top_queries": [
    {"query": "查询词1", "impressions": 5000},
    {"query": "查询词2", "impressions": 3000}
  ],
  "page_type": "course_article",
  "language": "zh",
  "avg_position": 4.5
}
```

## 输出格式

返回 JSON，key 为 path，value 包含 `title` 和 `meta_description`：

```json
{
  "/sciencepedia/feynman/...": {
    "title": "新标题 | SciencePedia",
    "meta_description": "新描述..."
  }
}
```

## 页面数据

以下是需要重写的页面：
