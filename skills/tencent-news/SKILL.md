---
name: openakita/skills@tencent-news
description: "Tencent News content subscription skill. Provides 7x24 news updates with hot news, morning/evening briefings, real-time feeds, rankings, topic news, and subject queries. Use when user wants news, headlines, briefings, or current events from Chinese sources."
license: MIT
metadata:
  author: TencentNews
  version: "1.0.7"
---

# 腾讯新闻内容订阅

通过 tencent-news-cli 获取腾讯新闻内容，支持热点新闻、早报晚报、实时资讯、新闻榜单、领域新闻查询。

## 配置

### API Key 获取
打开 https://news.qq.com/exchange?scene=appkey 获取 API Key。

### 安装 CLI
下载官方 skill 包并安装 CLI。

### 设置 Key
"<cliPath>" apikey-set KEY

## 获取新闻

1. 执行 help 查看可用子命令
2. 理解用户意图，映射子命令
3. 执行并按格式输出

## 输出格式

1. **标题文字**
   来源：媒体名称
   摘要内容……
   [查看原文](https://…)

**来源：腾讯新闻**

## 预置脚本

### scripts/news_cli_setup.py
腾讯新闻 CLI 安装配置脚本。

```bash
python3 scripts/news_cli_setup.py install
python3 scripts/news_cli_setup.py configure
python3 scripts/news_cli_setup.py status
```
