---
name: openakita/skills@image-understanding
description: Analyze images using Dashscope (Qwen) Vision models for detailed description, OCR text extraction, object recognition, and visual Q&A. Use when the user needs to understand image content via Alibaba Cloud Dashscope API, especially for Chinese-language image analysis and documents.
license: MIT
metadata:
 author: openakita
 version: "1.0.0"
---

# Image Understanding (Image Understanding)

Use **Dashscope () ** Analyze, Supports, OCRExtract, and. 

---

## Introduction

Yes Analyze, ViaCall Dashscope () (qwen-vl-plus, qwen-vl-max), AI andAnalyze. 

**: **
- 🖼️
- 🔤 Extract (OCR) 
- 🎯
- 💬

---

## Use Cases

### 📄
- will
-
-

### 🛒
- Analyze
- Extract
-

### 💬
- Get
-
- Analyze

---

##

### 1️⃣

```bash
pip install requests
```

### 2️⃣ Get Dashscope API Key

1. [Dashscope ](https://dashscope.console.aliyun.com/)
2. Create
3. Create API Key

### 3️⃣ API Key

```bash
#: (Recommendations) 
set DASHSCOPE_API_KEY=sk-your-api-key-here

#: Run () 
```

---

## Usage

###

```bash
python scripts/image_understanding.py -i []
```

###

| Parameter | Description |
|------|------|
| `-i, --image` | **Required** orURL |
| `-m, --model` |: `qwen-vl-plus`(Default) or `qwen-vl-max` |
| `-p, --custom-prompt` | Analyze |
| `-e, --extract-text` | Extract(OCR) |
| `-o, --identify-objects` | |
| `--compact` | JSON |

### Usage Examples

```bash
# 1. (Default) 
python scripts/image_understanding.py -i photo.jpg

# 2. Extract
python scripts/image_understanding.py -i screenshot.png -e

# 3.
python scripts/image_understanding.py -i photo.jpg -o

# 4.
python scripts/image_understanding.py -i photo.jpg -p "this? "

# 5. Use
python scripts/image_understanding.py -i photo.jpg -m qwen-vl-max

# 6.
python scripts/image_understanding.py -i "https://example.com/image.png" -e

# 7. SetAPI KeyRun
set DASHSCOPE_API_KEY=sk-xxx
python scripts/image_understanding.py -i photo.jpg
```

---

## Best Practices

### 📸
-, 
- not 640x640
- or

### 💡
- Use, 
- ("") 
-

### ✅
- need
-
- SaveandAnalyze

---

## API

| | Value |
|--------|-----|
| | Dashscope () |
| Default | qwen-vl-plus |
| | qwen-vl-max |
| API Base | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| | `DASHSCOPE_API_KEY` |

---

## Troubleshooting

| | |
|------|----------|
| API Key | `DASHSCOPE_API_KEY` YesNo |
| notSupports | Use PNG/JPG/GIF/WEBP/BMP |
| |, Use |
| not |, |

---

Run `python scripts/image_understanding.py --help` ViewFull