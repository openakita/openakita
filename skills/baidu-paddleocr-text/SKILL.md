---
name: openakita/skills@baidu-paddleocr-text
description: "PaddleOCR text recognition skill using PP-OCRv5 lightweight model. Supports natural scene and complex document text detection and recognition. Use when user needs OCR text extraction from images."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
---

# Baidu PaddleOCR Text Recognition

Based on the state-of-the-art PP-OCRv5 lightweight model, providing text detection and recognition for natural scenes and complex documents.

## Features

- Natural scene text recognition
- Document OCR text extraction
- Handwriting recognition support
- High accuracy on Chinese and English text

## Pre-built Scripts

### scripts/baidu_ocr_text.py
OCR text recognition. Requires setting BAIDU_OCR_AK and BAIDU_OCR_SK.

```bash
python3 scripts/baidu_ocr_text.py general /path/to/image.jpg
python3 scripts/baidu_ocr_text.py accurate /path/to/image.jpg
python3 scripts/baidu_ocr_text.py handwriting /path/to/note.jpg
```
