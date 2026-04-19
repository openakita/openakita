---
name: openakita/skills@baidu-paddleocr-doc
description: "PaddleOCR document parsing skill based on PaddleOCR-VL-1.5. Provides SOTA-level document parsing including layout analysis, table extraction, formula recognition, and multi-modal document understanding. Use when user needs intelligent document parsing."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# Baidu PaddleOCR Document Parsing

Based on the SOTA document parsing model PaddleOCR-VL-1.5, providing comprehensive document structure analysis and multi-modal understanding.

## Features

- Layout analysis and element detection
- Table structure extraction
- Formula recognition
- Multi-modal document understanding

## Pre-built Scripts

### scripts/baidu_ocr_doc.py
Document parsing. Requires setting BAIDU_OCR_AK and BAIDU_OCR_SK.

```bash
python3 scripts/baidu_ocr_doc.py parse /path/to/document.pdf
python3 scripts/baidu_ocr_doc.py parse /path/to/invoice.jpg
```
