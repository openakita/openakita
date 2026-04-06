---
name: openakita/skills@baidu-search
description: "Baidu Web Search skill for real-time Chinese web information retrieval. Breaks through static knowledge base limitations to get the latest news and information. Use when user needs to search the Chinese web for current information."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
requires:
  env: [BAIDU_QIANFAN_AK, BAIDU_QIANFAN_SK]
---

# 百度搜索

赋予智能体实时检索全网信息的能力，突破静态知识库限制，获取最新资讯与答案。ClawHub 全球下载量第一的搜索引擎 Skill。

## 配置

申请百度千帆 API Key: https://console.bce.baidu.com/qianfan/ais/console/apikey

export BAIDU_QIANFAN_AK="your_ak"
export BAIDU_QIANFAN_SK="your_sk"

## 安装

clawhub install baidu-search --no-input

## 功能

- 网页搜索：实时检索全网信息
- 图片搜索：图搜相似图多模态检索
- 时效筛选：按发布时间过滤结果
- 权威度评级：结果附带相关度和权威度评级

## 预置脚本

### scripts/baidu_search.py
百度搜索 API 封装，需设置 BAIDU_QIANFAN_AK 和 BAIDU_QIANFAN_SK。

```bash
python3 scripts/baidu_search.py web "Python 异步编程"
python3 scripts/baidu_search.py image "风景壁纸"
```
