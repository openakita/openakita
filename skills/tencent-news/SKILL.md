---
name: openakita/skills@tencent-news
description: "Tencent News content subscription skill. Provides 7x24 news updates with hot news, morning/evening briefings, real-time feeds, rankings, topic news, and subject queries. Use when user wants news, headlines, briefings, or current events from Chinese sources."
license: MIT
metadata:
  author: TencentNews
  version: "1.0.7"
---

# Tencent News Content Subscription

Get Tencent News content via `tencent-news-cli`. Supports hot news topics, morning/evening briefings, real-time news feeds, news rankings, topic news, and subject queries.

## Configuration

### API Key Acquisition
Visit https://news.qq.com/exchange?scene=appkey to get an API Key.

### Install CLI
Download the official skill package and install the CLI.

### Set Key
```bash
"<cliPath>" apikey-set KEY
```

## Get News

1. Execute `help` to view available subcommands
2. Understand the user's intent and map to the appropriate subcommand
3. Execute and output in the specified format

## Output Format

1. **Headline text**
   Source: Media name
   Summary content...
   [Read original](https://...)

**Source: Tencent News**

## Pre-built Scripts

### scripts/news_cli_setup.py
Tencent News CLI installation and configuration script.

```bash
python3 scripts/news_cli_setup.py install
python3 scripts/news_cli_setup.py configure
python3 scripts/news_cli_setup.py status
```
