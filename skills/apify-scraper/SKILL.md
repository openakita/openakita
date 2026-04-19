---
name: openakita/skills@apify-scraper
description: Web data extraction using 55+ Apify Actors for AI-driven scraping. Supports Instagram, Facebook, TikTok, YouTube, Google, and more. Auto-selects best Actor for the task. Structured output in JSON/CSV with rate limiting and ethical scraping guidelines.
license: MIT
metadata:
 author: openakita
 version: "1.0.0"
---

# Apify Scraper —

## When to Use

- When the user needs (,, search) 
- needget (Instagram, TikTok, YouTube ) 
- need Google search,, 
- need
- need JSON/CSV
- needExtractand

---

## Prerequisites

###

| | Description |
|--------|------|
| `APIFY_TOKEN` | Apify API Token, in https://console.apify.com/account/integrations get |

Token `.env`: 

```
APIFY_TOKEN=apify_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

###

| | | install |
|------|------|---------|
| `httpx` | HTTP API Call | `pip install httpx` |

### Optional

| | | install |
|------|------|---------|
| `apify-client` | Apify Python SDK | `pip install apify-client` |
| `pandas` | handleand | `pip install pandas` |

### Validate

```bash
curl -s "https://api.apify.com/v2/user/me?token=$APIFY_TOKEN" | python -m json.tool
```

---

## Instructions

### Apify Actor Quick

Apify have Actor ( /Automatic). 55+, AI Extract Actor. 

### Platform Actor

####

| | Actor | Actor ID | need |
|------|-------|----------|---------|
| Instagram | Profile Scraper | `apify/instagram-profile-scraper` |,, |
| Instagram | Hashtag Scraper | `apify/instagram-hashtag-scraper` | |
| Instagram | Comment Scraper | `apify/instagram-comment-scraper` | |
| TikTok | Scraper | `clockworks/free-tiktok-scraper` |,, |
| YouTube | Scraper | `bernardo/youtube-scraper` |, |
| YouTube | Channel Scraper | `streamers/youtube-channel-scraper` | |
| Facebook | Posts Scraper | `apify/facebook-posts-scraper` | |
| Facebook | Comments Scraper | `apify/facebook-comments-scraper` | |
| Twitter/X | Scraper | `apidojo/tweet-scraper` | search |
| LinkedIn | Profile Scraper | `anchor/linkedin-profile-scraper` | |

#### search

| | Actor | Actor ID | need |
|------|-------|----------|---------|
| Google | Search Results | `apify/google-search-scraper` | SERP |
| Google | Maps | `compass/crawler-google-places` |, |
| Google | Trends | `emastra/google-trends-scraper` | search |
| Google | News | `lhotanova/google-news-scraper` | search |
| Google | Shopping | `epctex/google-shopping-scraper` | |
| Bing | Search | `nicefellow/bing-search-scraper` | Bing search |

####

| | Actor | Actor ID | need |
|------|-------|----------|---------|
| Amazon | Product Scraper | `junglee/amazon-scraper` |, |
| Amazon | Review Scraper | `junglee/amazon-reviews-scraper` | |
| eBay | Scraper | `drobnikj/ebay-scraper` | search |
| AliExpress | Scraper | `epctex/aliexpress-scraper` | |

####

| | Actor | Actor ID | need |
|------|-------|----------|---------|
| | Web Scraper | `apify/web-scraper` | Extract |
| | Screenshot | `apify/screenshot-url` | |
| Extract | Link Extractor | `apify/link-extractor` | Receive |
| RSS | RSS Feed | `drobnikj/rss-feed-reader` | RSS |
| AI Extract | GPT Scraper | `drobnikj/gpt-scraper` | AI Extract |

### Actor Automatic

Agent Automatic Actor: 

1. **** — and
2. ** Actor** — and
3. **** — Use Web Scraper or GPT Scraper
4. **** — Use Actor and

---

## Workflows

### Workflow 1:

** 1 — **

| Parameter | Description | Example |
|------|------|------|
| | | Instagram |
| | /// | |
| | URL// | @openai |
| | Maximum | 100 |
| | | 30 |

** 2 — Actor**

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

** 3 — gethandle**

```python
items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
```

** 4 — **

When the user needs (JSON/CSV/need). 

---

### Workflow 2: search

** 1 — search**

| Parameter | Description | Default |
|------|------|--------|
| | search | — |
| search | Google/Bing | Google |
| / | Set | CN/zh |
| | | 50 |
| Type | /// | |

** 2 — Call Actor**

```python
run_input = {
"queries": "AI agent 2025",
 "maxPagesPerQuery": 3,
 "languageCode": "zh",
 "countryCode": "cn",
 "resultsPerPage": 10,
}

run = client.actor("apify/google-search-scraper").call(run_input=run_input)
items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
```

** 3 — Extract**

| | Description |
|------|------|
| `title` | |
| `url` | |
| `description` | need |
| `position` | |

---

### Workflow 3: Extract

have Actor, Use AI Extract: 

** A — Web Scraper (Based on) **

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

** B — GPT Scraper (AI Extract) **

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

### Workflow 4:

: 

** 1** — listhave
** 2** — Launch Actor
** 3** — have
** 4** —

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

### JSON (Default) 

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
"text": "...",
 "likes": 1234,
 "comments": 56,
 "timestamp": "2025-02-28T10:00:00Z",
 "url": "https://instagram.com/p/xxx"
 }
 ]
}
```

### CSV

```python
import pandas as pd

df = pd.DataFrame(items)
df.to_csv('output.csv', index=False, encoding='utf-8-sig')
```

### Summary

, need: 

```
📊
- Actor: Instagram Profile Scraper
-: 142
-: 2025-01-01 ~ 2025-03-01
-: 2,345
-: [URL]
-: $0.12
```

---

## Common Pitfalls

### 1. APIFY_TOKEN

****: haveReturns 401
****: `.env` `APIFY_TOKEN` Set

### 2. Actor Run

****: 
****: 
- `maxRequestsPerCrawl` or `resultsLimit`
- Use `memoryMbytes`
- YesNo

### 3.

****: Returns 403 or
****: 
- Use Apify (Actor ) 
- and
-

### 4. not

not Actor Returns not. inhandle: 

```python
if items:
 print("Available fields:", list(items[0].keys()))
```

### 5.

Apify (CU).: 
- (`resultsLimit: 10`) 
-: ViewRun CU ×
- Set

### 6.

Instagram, TikTok will: 
- notneedin
- UseProvides
- robots.txt and Terms of Service

---

## and

###

1. **robots.txt** — robots.txt
2. **Terms of Service** — not
3. **** — GDPR, 
4. **** — not
5. **** — Used for (Analyze,, ) 

###

1. Used foror
2.
3. or
4.
5. Used for, 

###

1. get, 
2. Useand
3. inExecute
4. Provides User-Agent
5.

---

## Advanced Features

### Timer

```python
schedule_input = {
 "actorId": "apify/google-search-scraper",
"cronExpression": "0 9 * * 1", # 9
 "input": {
"queries": "",
 "maxPagesPerQuery": 1,
 }
}
```

### Webhook

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

## EXTEND.md

increate `EXTEND.md`: 
- Actor ID and
- handle
-
- and