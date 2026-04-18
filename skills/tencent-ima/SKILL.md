---
name: openakita/skills@tencent-ima
description: "Tencent IMA OpenAPI skill for notes and knowledge base management. Use when user mentions knowledge base, notes, memos, file uploads, web page collection, or knowledge search. Supports notes CRUD, knowledge base file upload, web link addition, and content search."
license: MIT
metadata:
  author: tencent-ima
  version: "1.1.2"
requires:
  env: [IMA_OPENAPI_CLIENTID, IMA_OPENAPI_APIKEY]
---

# Tencent IMA 智能工作台

统一的 IMA OpenAPI 技能，支持笔记管理和知识库操作。

## Configuration

1. 打开 https://ima.qq.com/agent-interface 获取 Client ID 和 API Key
2. 存储凭证：

方式 A — 配置文件：
mkdir -p ~/.config/ima
echo "your_client_id" > ~/.config/ima/client_id
echo "your_api_key" > ~/.config/ima/api_key

方式 B — 环境变量：
export IMA_OPENAPI_CLIENTID="your_client_id"
export IMA_OPENAPI_APIKEY="your_api_key"

## API 调用模板

ima_api() {
  local path="$1" body="$2"
  curl -s -X POST "https://ima.qq.com/$path" \
    -H "ima-openapi-clientid: $IMA_CLIENT_ID" \
    -H "ima-openapi-apikey: $IMA_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$body"
}

## 模块决策表

| 用户意图 | 模块 |
|---------|------|
| 搜索/浏览/创建/编辑笔记 | notes |
| 上传文件/添加网页/搜索知识库 | knowledge-base |

## Pre-built Scripts

### scripts/ima_notes.py
IMA 笔记 API 封装，需设置 IMA_OPENAPI_CLIENTID 和 IMA_OPENAPI_APIKEY。

```bash
python3 scripts/ima_notes.py search "会议纪要"
python3 scripts/ima_notes.py folders
python3 scripts/ima_notes.py create --content "# 新笔记\n内容"
python3 scripts/ima_notes.py read --doc-id xxx
```

### scripts/ima_kb.py
IMA 知识库 API 封装。

```bash
python3 scripts/ima_kb.py search --kb-id xxx "关键词"
python3 scripts/ima_kb.py browse --kb-id xxx
python3 scripts/ima_kb.py import-url --kb-id xxx --url "https://example.com"
```
