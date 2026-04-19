---
name: openakita/skills@tencent-ima
description: "Tencent IMA OpenAPI skill for notes and knowledge base management. Use when user mentions knowledge base, notes, memos, file uploads, web page collection, or knowledge search. Supports notes CRUD, knowledge base file upload, and content search."
license: MIT
metadata:
  author: tencent-ima
  version: "1.1.2"
requires:
  env: [IMA_OPENAPI_CLIENTID, IMA_OPENAPI_APIKEY]
---

# Tencent IMA Smart Workbench

Unified IMA OpenAPI skill, supporting note management and knowledge base operations.

## Configuration

1. Visit https://ima.qq.com/agent-interface to get your Client ID and API Key
2. Store credentials:

Method A — Config file:
```bash
mkdir -p ~/.config/ima
echo "your_client_id" > ~/.config/ima/client_id
echo "your_api_key" > ~/.config/ima/api_key
```

Method B — Environment variables:
```bash
export IMA_OPENAPI_CLIENTID="your_client_id"
export IMA_OPENAPI_APIKEY="your_api_key"
```

## API Call Template

```bash
ima_api() {
  local path="$1" body="$2"
  curl -s -X POST "https://ima.qq.com/$path" \
    -H "ima-openapi-clientid: $IMA_CLIENT_ID" \
    -H "ima-openapi-apikey: $IMA_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$body"
}
```

## Module Decision Table

| User Intent | Module |
|---------|------|
| Search/Browse/Create/Edit notes | notes |
| Upload files/Add webpages/Search knowledge base | knowledge-base |

## Pre-built Scripts

### scripts/ima_notes.py
IMA Notes API wrapper. Requires `IMA_OPENAPI_CLIENTID` and `IMA_OPENAPI_APIKEY`.

```bash
python3 scripts/ima_notes.py search "Meeting minutes"
python3 scripts/ima_notes.py folders
python3 scripts/ima_notes.py create --content "# New note\nContent"
python3 scripts/ima_notes.py read --doc-id xxx
```

### scripts/ima_kb.py
IMA Knowledge Base API wrapper.

```bash
python3 scripts/ima_kb.py search --kb-id xxx "Keyword"
python3 scripts/ima_kb.py browse --kb-id xxx
python3 scripts/ima_kb.py import-url --kb-id xxx --url "https://example.com"
```
