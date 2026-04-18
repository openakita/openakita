---
name: openakita/skills@image-understander
description: Analyze images using GPT-4 Vision for detailed description, OCR text extraction, object recognition, and visual Q&A. Use when the user needs to understand image content, extract text from screenshots, identify objects in photos, or ask questions about images via OpenAI GPT-4 Vision API.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# 图片理解技能 (Image Understander)

## 📋 概述

一个Based on OpenAI GPT-4 Vision 的图片理解工具，Supports图片描述、文字识别(OCR)、物体识别和图片问答。

## 🚀 功能

| 功能 | 命令 | Description |
|------|------|------|
| 图片描述 | `-m describe` | 详细描述图片内容 |
| 文字Extract | `-m ocr` | Extract图片中的所有文字 |
| 物体识别 | `-m objects` | 识别并list图片中的物体 |
| 图片问答 | `-m qa` | 针对图片回答问题 |

## 📦 install

```bash
# install依赖
pip install openai pillow requests
```

## 🔧 配置

### 方式一：环境变量
```bash
set OPENAI_API_KEY=sk-your-api-key-here
```

### 方式二：命令行传入
```bash
python scripts/main.py -i photo.jpg -a sk-your-key
```

## 📖 Use方法

### 基本Use
```bash
# 描述图片
python scripts/main.py -i photo.jpg -m describe

# Extract文字（OCR）
python scripts/main.py -i screenshot.png -m ocr

# 识别物体
python scripts/main.py -i photo.jpg -m objects

# 图片问答
python scripts/main.py -i photo.jpg -m qa -q "这个图片里有什么？"
```

### Full参数
```bash
python scripts/main.py \
  --image PATH_TO_IMAGE \
  --mode describe|ocr|objects|qa \
  --api-key YOUR_API_KEY \
  --prompt "你的问题" \
  --output OUTPUT.json \
  --verbose
```

## 📁 输出示例

```json
{
  "mode": "describe",
  "image": "photo.jpg",
  "result": "A beautiful sunset over the ocean with orange and purple sky...",
  "objects": [],
  "text": ""
}
```

## ⚠️ 注意事项

- 需要 OpenAI API Key（Supports GPT-4 Vision）
- Supports的图片格式：PNG、JPG、GIF、BMP
- 图片大小建议小于 20MB
