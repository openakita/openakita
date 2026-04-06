---
name: openakita/skills@baidu-paddleocr-doc
description: "PaddleOCR document parsing skill based on PaddleOCR-VL-1.5. Provides SOTA-level document understanding with ultra-high precision recognition and parsing. Use when user needs to parse, extract, or understand document content."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
requires:
  env: [BAIDU_API_KEY]
---

# 文心衍生 · PaddleOCR 文档解析

基于 SOTA 文档解析模型 PaddleOCR-VL-1.5 构建，为 Agent 加上"眼睛"，对文档进行超高精度识别、解析。

## 配置

export BAIDU_API_KEY="your_key"

## 功能

- 文档结构识别
- 表格提取与还原
- 公式识别
- 图文混排解析
- 多语言文档支持

## 预置脚本

### scripts/baidu_ocr_doc.py
百度文档/表格 OCR 识别，需设置 BAIDU_OCR_AK 和 BAIDU_OCR_SK。

```bash
python3 scripts/baidu_ocr_doc.py doc /path/to/document.jpg
python3 scripts/baidu_ocr_doc.py table /path/to/table.png
```
