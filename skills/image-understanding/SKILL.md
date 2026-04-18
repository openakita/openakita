---
name: openakita/skills@image-understanding
description: Analyze images using Dashscope (Qwen) Vision models for detailed description, OCR text extraction, object recognition, and visual Q&A. Use when the user needs to understand image content via Alibaba Cloud Dashscope API, especially for Chinese-language image analysis and documents.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# 图片理解技能 (Image Understanding)

Use **Dashscope（通义千问）** 视觉模型Analyze图片，Supports详细描述、OCR文字Extract、物体识别和图片问答。

---

## Introduction

图片理解技能Yes一个强大的视觉Analyze工具，ViaCall Dashscope（阿里云通义千问）的视觉大模型（qwen-vl-plus、qwen-vl-max），让 AI 能够理解和Analyze图像内容。

**核心功能：**
- 🖼️ 图片内容详细描述
- 🔤 文字Extract（OCR）
- 🎯 物体识别
- 💬 图片问答

---

## Use Cases

### 📄 文档处理
- 会议白板照片转文字
- 纸质文档扫描识别
- 手写笔记数字化

### 🛒 工作应用
- 产品图片Analyze
- 竞品图片Extract信息
- 图表数据解读

### 💬 图片问答
- 针对图片提问Get答案
- 理解复杂场景细节
- 技术图纸逻辑Analyze

---

## 环境配置

### 1️⃣ 安装依赖

```bash
pip install requests
```

### 2️⃣ Get Dashscope API Key

1. 访问 [Dashscope 控制台](https://dashscope.console.aliyun.com/)
2. Create账号并开通服务
3. Create API Key

### 3️⃣ 配置 API Key

```bash
# 方式一：环境变量（Recommendations）
set DASHSCOPE_API_KEY=sk-your-api-key-here

# 方式二：Run时传入（见下方）
```

---

## Usage

### 基本命令

```bash
python scripts/image_understanding.py -i 图片路径 [选项]
```

### 常用参数

| Parameter | Description |
|------|------|
| `-i, --image` | **Required** 图片路径或URL |
| `-m, --model` | 模型选择：`qwen-vl-plus`(Default) 或 `qwen-vl-max` |
| `-p, --custom-prompt` | 自定义Analyze提示词 |
| `-e, --extract-text` | Extract文字(OCR) |
| `-o, --identify-objects` | 识别物体 |
| `--compact` | 输出紧凑JSON |

### Usage Examples

```bash
# 1. 基本描述（Default）
python scripts/image_understanding.py -i photo.jpg

# 2. Extract文字
python scripts/image_understanding.py -i screenshot.png -e

# 3. 识别物体
python scripts/image_understanding.py -i photo.jpg -o

# 4. 自定义问答
python scripts/image_understanding.py -i photo.jpg -p "这个产品多少钱？"

# 5. Use更强的模型
python scripts/image_understanding.py -i photo.jpg -m qwen-vl-max

# 6. 网络图片
python scripts/image_understanding.py -i "https://example.com/image.png" -e

# 7. SetAPI Key后Run
set DASHSCOPE_API_KEY=sk-xxx
python scripts/image_understanding.py -i photo.jpg
```

---

## Best Practices

### 📸 图片质量
- 确保图片清晰、亮度充足
- 文字图片分辨率不低于 640x640
- 避免模糊或过暗的图片

### 💡 提示词技巧
- Use具体、明确的指令
- 指定关注点（如"重点关注价格标签"）
- 多语言场景可混合中英文

### ✅ 结果验证
- 重要信息建议人工复核
- 涉及专业领域需专家确认
- 妥善Save原始图片和Analyze结果

---

## API 配置

| 配置项 | Value |
|--------|-----|
| 服务商 | Dashscope (通义千问) |
| Default模型 | qwen-vl-plus |
| 高级模型 | qwen-vl-max |
| API Base | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 环境变量 | `DASHSCOPE_API_KEY` |

---

## 故障排除

| 问题 | 解决方案 |
|------|----------|
| API Key 错误 | 检查 `DASHSCOPE_API_KEY` YesNo正确 |
| 图片格式不Supports | Use PNG/JPG/GIF/WEBP/BMP 格式 |
| 网络超时 | 检查网络连接，尝试Use代理 |
| 识别不准确 | 提高图片质量，添加更详细的提示词 |

---

Run `python scripts/image_understanding.py --help` ViewFull帮助
