---
name: openakita/skills@baidu-paddleocr-text
description: "PaddleOCR text recognition skill using PP-OCRv5 lightweight model. Supports natural scene and complex document text detection and recognition. Use when user needs OCR text extraction from images."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# 文心衍生 · PaddleOCR 文字识别

集成 SOTA 级轻量化 OCR 模型 PP-OCRv5，支持自然场景及复杂文档的文字检测与识别。

## 功能

- 自然场景文字识别
- 复杂文档 OCR
- 多语言支持
- 轻量化推理

## 预置脚本

### scripts/baidu_ocr_text.py
百度通用文字 OCR 识别，需设置 BAIDU_OCR_AK 和 BAIDU_OCR_SK。

```bash
python3 scripts/baidu_ocr_text.py general /path/to/image.jpg
python3 scripts/baidu_ocr_text.py accurate /path/to/image.jpg
python3 scripts/baidu_ocr_text.py handwriting /path/to/note.jpg
```
