---
name: openakita/skills@fliggy-travel
description: "FlyAI travel search and booking skill powered by Fliggy MCP. Search flights, hotels, attractions, trains, concerts, and travel deals with natural language. Supports diverse travel scenarios including individual, group, business, family trips. No API key required for basic features."
license: MIT
metadata:
  author: alibaba-flyai
  version: "1.0.14"
---

# FlyAI — 飞猪旅行搜索与预订

通过 flyai-cli 调用飞猪 MCP 服务，支持全品类旅行搜索与预订。

## 安装

npm i -g @fly-ai/flyai-cli
flyai keyword-search --query "三亚有什么好玩的"

无需 API Key 即可使用基础功能。增强功能可配置：flyai config set FLYAI_API_KEY "your-key"

## 核心命令

| 命令 | 用途 | 必需参数 |
|------|------|---------|
| keyword-search | 自然语言跨品类搜索 | --query |
| ai-search | 语义搜索，理解复杂意图 | --query |
| search-flight | 结构化航班搜索 | --origin |
| search-hotel | 按目的地酒店搜索 | --dest-name |
| search-poi | 按城市景点搜索 | --city-name |
| search-train | 火车票搜索 | --origin |

## 输出格式

所有命令输出单行 JSON，可配合 jq 或 Python 处理。

展示结果时：
- 包含图片：![]({picUrl})
- 包含预订链接：[点击预订]({jumpUrl})
- 使用 Markdown 表格进行多选项对比

## 使用示例

flyai keyword-search --query "下周末上海飞三亚"
flyai search-hotel --dest-name "杭州" --check-in 2026-04-10 --check-out 2026-04-12
flyai search-poi --city-name "北京"

## 预置脚本

### scripts/setup.py
飞猪 flyai-cli 安装配置脚本。

```bash
python3 scripts/setup.py
```

### scripts/flyai_quick.py
飞猪搜索快捷脚本。

```bash
python3 scripts/flyai_quick.py search --keyword "三亚酒店"
python3 scripts/flyai_quick.py ai-search --query "五一去哪里玩"
python3 scripts/flyai_quick.py flight --from 北京 --to 上海 --date 2026-05-01
python3 scripts/flyai_quick.py hotel --city 三亚 --checkin 2026-05-01
```
