---
name: openakita/skills@tencent-news
description: "Tencent News content subscription skill. Provides 7x24 news updates with hot news, morning/evening briefings, real-time feeds, rankings, topic news, and subject queries. Use when user wants news, headlines, briefings, or current events from Chinese sources."
license: MIT
metadata:
  author: TencentNews
  version: "1.0.7"
---

# Tencent News内容订阅

Via tencent-news-cli Get腾讯新闻内容，Supports热点新闻、早报晚报、实时资讯、新闻榜单、领域新闻查询。

## Configuration

### API Key Get
Open https://news.qq.com/exchange?scene=appkey Get API Key。

### Installation CLI
Download官方 skill 包并安装 CLI。

### Set Key
"<cliPath>" apikey-set KEY

## Get新闻

1. Execute help View可用子命令
2. 理解用户意图，映射子命令
3. Execute并按格式输出

## Output Format

1. **标题文字**
   来源：媒体名称
   摘要内容……
   [View原文](https://…)

**来源：腾讯新闻**

## Pre-built Scripts

### scripts/news_cli_setup.py
腾讯新闻 CLI 安装配置脚本。

```bash
python3 scripts/news_cli_setup.py install
python3 scripts/news_cli_setup.py configure
python3 scripts/news_cli_setup.py status
```
