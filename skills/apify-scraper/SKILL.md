---
name: openakita/skills@apify-scraper
description: Web data extraction using 55+ Apify Actors for AI-driven scraping. Supports Instagram, Facebook, TikTok, YouTube, Google, and more. Auto-selects best Actor for the task. Structured output in JSON/CSV with rate limiting and ethical scraping guidelines.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Apify Scraper — 网页数据抓取

## When to Use

- 用户需要从网站抓取结构化数据（商品信息、社交媒体帖子、搜索结果等）
- 需要批量获取社交媒体平台数据（Instagram、TikTok、YouTube 等）
- 需要抓取 Google 搜索结果、地图信息、评价数据
- 需要定期监控网页变化
- 需要将非结构化网页内容转换为 JSON/CSV
- 需要从电商平台提取商品和价格信息

---

## Prerequisites

### 必需配置

| 配置项 | 说明 |
|--------|------|
| `APIFY_TOKEN` | Apify API Token，在 https://console.apify.com/account/integrations 获取 |

将 Token 添加到 `.env` 文件：

```
APIFY_TOKEN=apify_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 必需依赖

| 依赖 | 用途 | 安装方式 |
|------|------|---------|
| `httpx` | HTTP API 调用 | `pip install httpx` |

### 可选依赖

| 依赖 | 用途 | 安装方式 |
|------|------|---------|
| `apify-client` | Apify Python SDK | `pip install apify-client` |
| `pandas` | 数据处理与导出 | `pip install pandas` |

### 验证配置

```bash
curl -s "https://api.apify.com/v2/user/me?token=$APIFY_TOKEN" | python -m json.tool
```

---

## Instructions

### Apify Actor 快速概览

Apify 平台上有数千个 Actor（即预构建的爬虫/自动化程序）。本技能聚焦 55+ 个经过验证的、面向 AI 数据提取优化的 Actor。

### 平台 Actor 速查表

#### 社交媒体

| 平台 | Actor | Actor ID | 主要功能 |
|------|-------|----------|---------|
| Instagram | Profile Scraper | `apify/instagram-profile-scraper` | 用户资料、帖子、粉丝数 |
| Instagram | Hashtag Scraper | `apify/instagram-hashtag-scraper` | 标签下的帖子 |
| Instagram | Comment Scraper | `apify/instagram-comment-scraper` | 帖子评论 |
| TikTok | Scraper | `clockworks/free-tiktok-scraper` | 视频、用户、标签 |
| YouTube | Scraper | `bernardo/youtube-scraper` | 视频信息、评论 |
| YouTube | Channel Scraper | `streamers/youtube-channel-scraper` | 频道数据 |
| Facebook | Posts Scraper | `apify/facebook-posts-scraper` | 页面帖子 |
| Facebook | Comments Scraper | `apify/facebook-comments-scraper` | 帖子评论 |
| Twitter/X | Scraper | `apidojo/tweet-scraper` | 推文搜索 |
| LinkedIn | Profile Scraper | `anchor/linkedin-profile-scraper` | 用户资料 |

#### 搜索引擎

| 平台 | Actor | Actor ID | 主要功能 |
|------|-------|----------|---------|
| Google | Search Results | `apify/google-search-scraper` | SERP 结果 |
| Google | Maps | `compass/crawler-google-places` | 商家信息、评价 |
| Google | Trends | `emastra/google-trends-scraper` | 搜索趋势 |
| Google | News | `lhotanova/google-news-scraper` | 新闻搜索 |
| Google | Shopping | `epctex/google-shopping-scraper` | 商品价格 |
| Bing | Search | `nicefellow/bing-search-scraper` | Bing 搜索结果 |

#### 电商平台

| 平台 | Actor | Actor ID | 主要功能 |
|------|-------|----------|---------|
| Amazon | Product Scraper | `junglee/amazon-scraper` | 商品详情、评价 |
| Amazon | Review Scraper | `junglee/amazon-reviews-scraper` | 商品评论 |
| eBay | Scraper | `drobnikj/ebay-scraper` | 商品搜索 |
| AliExpress | Scraper | `epctex/aliexpress-scraper` | 商品数据 |

#### 通用工具

| 功能 | Actor | Actor ID | 主要功能 |
|------|-------|----------|---------|
| 网页抓取 | Web Scraper | `apify/web-scraper` | 通用网页数据提取 |
| 网页截图 | Screenshot | `apify/screenshot-url` | 网页截图 |
| 链接提取 | Link Extractor | `apify/link-extractor` | 页面链接收集 |
| RSS 解析 | RSS Feed | `drobnikj/rss-feed-reader` | RSS 源数据 |
| AI 提取 | GPT Scraper | `drobnikj/gpt-scraper` | AI 驱动智能提取 |

### Actor 自动选择策略

Agent 根据用户需求自动选择最合适的 Actor：

1. **解析用户意图** — 从用户描述中识别目标平台和数据类型
2. **匹配 Actor** — 根据平台和功能匹配上方表格
3. **若无精确匹配** — 使用通用 Web Scraper 或 GPT Scraper
4. **确认方案** — 向用户确认将使用的 Actor 和预计数据量

---

## Workflows

### Workflow 1: 社交媒体数据抓取

**步骤 1 — 确认需求**

| 参数 | 说明 | 示例 |
|------|------|------|
| 平台 | 目标社交平台 | Instagram |
| 数据类型 | 帖子/评论/用户/标签 | 帖子 |
| 范围 | URL/关键词/用户名 | @openai |
| 数量限制 | 最大抓取条数 | 100 |
| 时间范围 | 时间过滤 | 最近 30 天 |

**步骤 2 — 选择并配置 Actor**

```python
from apify_client import ApifyClient

client = ApifyClient(os.environ['APIFY_TOKEN'])

run_input = {
    "usernames": ["openai"],
    "resultsLimit": 100,
    "resultsType": "posts",
}

run = client.actor("apify/instagram-profile-scraper").call(run_input=run_input)
```

**步骤 3 — 获取并处理结果**

```python
items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
```

**步骤 4 — 格式化输出**

将数据转换为用户需要的格式（JSON/CSV/表格摘要）。

---

### Workflow 2: 搜索引擎数据抓取

**步骤 1 — 确认搜索参数**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 关键词 | 搜索查询 | — |
| 搜索引擎 | Google/Bing | Google |
| 国家/语言 | 地域设置 | CN/zh |
| 结果数量 | 抓取条数 | 50 |
| 类型 | 网页/新闻/图片/视频 | 网页 |

**步骤 2 — 调用 Actor**

```python
run_input = {
    "queries": "AI agent 框架 2025",
    "maxPagesPerQuery": 3,
    "languageCode": "zh",
    "countryCode": "cn",
    "resultsPerPage": 10,
}

run = client.actor("apify/google-search-scraper").call(run_input=run_input)
items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
```

**步骤 3 — 提取关键字段**

| 字段 | 说明 |
|------|------|
| `title` | 结果标题 |
| `url` | 链接地址 |
| `description` | 摘要描述 |
| `position` | 排名位置 |

---

### Workflow 3: 通用网页数据提取

当没有专用 Actor 时，使用 AI 驱动的通用提取：

**方法 A — Web Scraper（基于选择器）**

```python
run_input = {
    "startUrls": [{"url": "https://example.com/products"}],
    "pageFunction": """
    async function pageFunction(context) {
        const $ = context.jQuery;
        const results = [];
        $('div.product-card').each((i, el) => {
            results.push({
                name: $(el).find('.title').text().trim(),
                price: $(el).find('.price').text().trim(),
                url: $(el).find('a').attr('href'),
            });
        });
        return results;
    }
    """,
    "maxRequestsPerCrawl": 100,
}

run = client.actor("apify/web-scraper").call(run_input=run_input)
```

**方法 B — GPT Scraper（AI 智能提取）**

```python
run_input = {
    "startUrls": [{"url": "https://example.com/products"}],
    "instructions": "Extract all product names, prices, and descriptions from this page",
    "openaiApiKey": os.environ.get('OPENAI_API_KEY'),
    "maxRequestsPerCrawl": 10,
}

run = client.actor("drobnikj/gpt-scraper").call(run_input=run_input)
```

---

### Workflow 4: 批量多平台抓取

同时从多个来源抓取数据：

**步骤 1** — 列出所有抓取任务
**步骤 2** — 并行启动多个 Actor
**步骤 3** — 等待所有任务完成
**步骤 4** — 合并结果并去重

```python
import asyncio
from apify_client import ApifyClientAsync

async def batch_scrape(tasks):
    client = ApifyClientAsync(os.environ['APIFY_TOKEN'])
    results = {}

    async def run_actor(name, actor_id, input_data):
        run = await client.actor(actor_id).call(run_input=input_data)
        items = []
        async for item in client.dataset(run["defaultDatasetId"]).iterate_items():
            items.append(item)
        results[name] = items

    await asyncio.gather(*[
        run_actor(t['name'], t['actor_id'], t['input'])
        for t in tasks
    ])

    return results
```

---

## Output Format

### JSON 输出（默认）

```json
{
  "metadata": {
    "actor": "apify/instagram-profile-scraper",
    "total_items": 42,
    "scraped_at": "2025-03-01T14:30:00Z",
    "run_id": "abc123",
    "cost_usd": 0.05
  },
  "data": [
    {
      "id": "post_12345",
      "text": "帖子内容...",
      "likes": 1234,
      "comments": 56,
      "timestamp": "2025-02-28T10:00:00Z",
      "url": "https://instagram.com/p/xxx"
    }
  ]
}
```

### CSV 输出

```python
import pandas as pd

df = pd.DataFrame(items)
df.to_csv('output.csv', index=False, encoding='utf-8-sig')
```

### 摘要表格

当数据量较大时，先输出摘要统计：

```
📊 抓取完成
- Actor: Instagram Profile Scraper
- 总条数: 142 条帖子
- 时间范围: 2025-01-01 ~ 2025-03-01
- 平均点赞: 2,345
- 最高互动帖子: [URL]
- 费用: $0.12
```

---

## Common Pitfalls

### 1. APIFY_TOKEN 未配置

**症状**：所有请求返回 401
**解决**：确认 `.env` 中的 `APIFY_TOKEN` 已正确设置

### 2. Actor 运行超时

**症状**：任务长时间未完成
**解决**：
- 减少 `maxRequestsPerCrawl` 或 `resultsLimit`
- 使用 `memoryMbytes` 增加内存分配
- 检查目标网站是否可达

### 3. 被目标网站封禁

**症状**：返回 403 或空结果
**解决**：
- 使用 Apify 的代理池（Actor 通常自带）
- 降低并发和频率
- 增加请求间隔

### 4. 数据格式不一致

不同 Actor 返回的数据结构不同。在处理数据前先检查字段：

```python
if items:
    print("Available fields:", list(items[0].keys()))
```

### 5. 费用超预期

Apify 按计算单元（CU）收费。大规模抓取前：
- 先小量测试（`resultsLimit: 10`）确认结果质量
- 估算总费用：查看测试运行的 CU 消耗 × 总数据量倍数
- 设置账户费用上限

### 6. 社交平台反爬限制

Instagram、TikTok 等平台会动态调整反爬策略：
- 不要在短时间内大量抓取同一账号
- 使用平台提供的数据导出功能作为补充
- 遵守平台的 robots.txt 和 Terms of Service

---

## 伦理与合规指南

### 必须遵守

1. **robots.txt** — 尊重网站的 robots.txt 规则
2. **Terms of Service** — 不违反目标网站的服务条款
3. **隐私法规** — 遵守 GDPR、个人信息保护法等法规
4. **频率限制** — 不对目标网站造成过大负载
5. **数据用途** — 仅用于合法目的（分析、研究、商业决策）

### 禁止行为

1. 抓取个人隐私信息用于骚扰或监控
2. 大量抓取导致目标网站服务降级
3. 绕过付费墙或版权保护
4. 出售未经授权的第三方数据
5. 用于垃圾信息、虚假评论等恶意目的

### 建议做法

1. 缓存已获取数据，避免重复抓取
2. 使用增量抓取而非全量抓取
3. 在非高峰时段执行大规模任务
4. 提供 User-Agent 标识你的爬虫身份
5. 记录抓取日志以便审计

---

## 高级功能

### 定时任务

```python
schedule_input = {
    "actorId": "apify/google-search-scraper",
    "cronExpression": "0 9 * * 1",  # 每周一早 9 点
    "input": {
        "queries": "竞品动态",
        "maxPagesPerQuery": 1,
    }
}
```

### Webhook 通知

```python
run = client.actor("apify/web-scraper").call(
    run_input=run_input,
    webhooks=[{
        "eventTypes": ["ACTOR.RUN.SUCCEEDED"],
        "requestUrl": "https://your-server.com/webhook",
    }]
)
```

---

## EXTEND.md 扩展

用户可在技能同目录下创建 `EXTEND.md` 添加：
- 常用的 Actor ID 和预设配置
- 自定义数据处理管道
- 特定网站的抓取策略
- 代理配置和反封禁策略

