# SEO 元数据重写 Prompt

你是一个专业的 SEO 优化专家。请根据以下信息为每个页面重写 title、meta description 和 meta keywords。

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

6. **Meta Keywords 5-8 个**，综合 `current_keywords` 和 `top_queries` 生成
   - 从 `current_keywords` 中保留与页面内容相关的优质词，丢弃过于宽泛或无意义的词
   - 从 `top_queries` 中提取核心概念作为关键词（去掉引号、公式符号、搜索语法等噪音）
   - 如果两个来源有重叠，合并去重
   - 关键词之间用英文逗号分隔，语言与页面一致
   - 优先选择搜索量大（impressions 高）且与页面主题高度相关的词

7. **Schema.org 结构化数据**，为搜索引擎提供精确的语义信息
   - **`schema_term_name`**：页面核心术语/概念的规范名称，语言与页面一致
     - 这不是 title，而是学术术语本身。例如 title 是 "Entropy Change in Free Expansion: Van der Waals Gas | SciencePedia"，但术语名应该是 "Entropy Change in Free Expansion"
     - 中文页面用中文术语名，如"复等位基因与等位基因系列"
   - **`schema_subject`**：所属学科/领域名称，语言与页面一致
     - 必须具体到二级学科，禁止使用 "Science"、"科学" 等笼统分类
     - 正确示例：`"Thermodynamics"`, `"Complex Analysis"`, `"遗传学"`, `"量子力学"`
     - 错误示例：`"Science"`, `"Physics"`, `"Math"`（太宽泛）
   - **`schema_course_name`**（仅 `course_article` 页面需要）：所属课程的名称，语言与页面一致
     - 从 URL 路径中的课程名推断，如 `principles_of_genetics_graduate` → `"遗传学原理"` (zh) 或 `"Principles of Genetics"` (en)
     - `keyword` 类型的页面不需要此字段，省略即可

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

返回 JSON，key 为 path，value 包含 `title`、`meta_description` 和对应的字符数：

course_article 页面示例：
```json
{
  "/sciencepedia/feynman/principles_of_genetics_graduate-...": {
    "title": "复等位基因遗传图解与基因型计算 | SciencePedia",
    "title_length": 22,
    "meta_description": "详解复等位基因的遗传图谱与基因型数量计算方法，涵盖ABO血型等经典案例图解。",
    "desc_length": 35,
    "meta_keywords": "复等位基因,遗传图,基因型数量计算,ABO血型遗传,等位基因系列",
    "schema_term_name": "复等位基因与等位基因系列",
    "schema_subject": "遗传学",
    "schema_course_name": "遗传学原理"
  }
}
```

keyword 页面示例：
```json
{
  "/en/sciencepedia/feynman/keyword/entropy_change_in_free_expansion": {
    "title": "Entropy Change in Free Expansion | SciencePedia",
    "title_length": 47,
    "meta_description": "Derivation of entropy change for ideal and van der Waals gas free expansion into vacuum.",
    "desc_length": 87,
    "meta_keywords": "entropy change,free expansion,van der Waals gas,thermodynamic irreversibility",
    "schema_term_name": "Entropy Change in Free Expansion",
    "schema_subject": "Thermodynamics"
  }
}
```

**字符数自检（必须执行）：**
- 写完每条 title 后数一遍字符数，如果超过 60 就重写，直到 ≤ 60
- 写完每条 description 后数一遍字符数，如果超过 155 就重写，直到 ≤ 155
- `title_length` 和 `desc_length` 字段填入实际字符数，用于下游校验

**常见超长原因及对策：**
- 英文 description 容易写到 160-200 字符 → 砍掉最后一个分句或少覆盖一个查询词
- 包含数学符号/特殊字符时需注意 Unicode 字符计数

## 输出保存

将你的 JSON 结果用 Write tool 保存到以下文件路径（不要输出到对话中，直接写文件）：

**输出文件路径：** `{{OUTPUT_PATH}}`

## 页面数据

以下是需要重写的页面：
