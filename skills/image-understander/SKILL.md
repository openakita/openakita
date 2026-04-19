---
name: openakita/skills@image-understander
description: Analyze images using GPT-4 Vision for detailed description, OCR text extraction, object recognition, and visual Q&A. Use when the user needs to understand image content, extract text from screenshots, identify objects in photos, or ask questions about images via OpenAI GPT-4 Vision API.
license: MIT
metadata:
 author: openakita
 version: "1.0.0"
---

# Image Understanding (Image Understander)

## 📋

Based on OpenAI GPT-4 Vision, Supports, (OCR), and. 

## 🚀

| | | Description |
|------|------|------|
| | `-m describe` | |
| Extract | `-m ocr` | Extract have |
| | `-m objects` | list |
| | `-m qa` | |

## 📦 install

```bash
# install
pip install openai pillow requests
```

## 🔧

###: 
```bash
set OPENAI_API_KEY=sk-your-api-key-here
```

###: 
```bash
python scripts/main.py -i photo.jpg -a sk-your-key
```

## 📖 Use

### Use
```bash
#
python scripts/main.py -i photo.jpg -m describe

# Extract (OCR) 
python scripts/main.py -i screenshot.png -m ocr

#
python scripts/main.py -i photo.jpg -m objects

#
python scripts/main.py -i photo.jpg -m qa -q "thishave? "
```

### Full
```bash
python scripts/main.py \
 --image PATH_TO_IMAGE \
 --mode describe|ocr|objects|qa \
 --api-key YOUR_API_KEY \
--prompt " " \
 --output OUTPUT.json \
 --verbose
```

## 📁

```json
{
 "mode": "describe",
 "image": "photo.jpg",
 "result": "A beautiful sunset over the ocean with orange and purple sky...",
 "objects": [],
 "text": ""
}
```

## ⚠️

- need OpenAI API Key (Supports GPT-4 Vision) 
- Supports: PNG, JPG, GIF, BMP
- 20MB