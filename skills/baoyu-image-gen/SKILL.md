---
name: jimliu/baoyu-skills@baoyu-image-gen
description: Generate AI images using multiple providers (OpenAI DALL-E, Google Imagen, DashScope/Tongyi Wanxiang, Replicate). Supports various aspect ratios, quality presets, batch generation, and provider-specific prompt engineering techniques.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Baoyu Image Gen — AI 图像Generation

## When to Use

- 用户要求Generate image、插图、海报、封面、图标等视觉内容
- 需要为文章、PPT、网站Create配图
- 需要特定比例的图片（社交媒体封面、手机壁纸等）
- 需要批量Generation多张图片并选择
- 需要针对不同 AI 绘图服务编写优化 prompt
- 需要将自然语言描述转换为高质量 AI 图片

---

## Prerequisites

### API 密钥配置

至少需要配置一个图像Generation服务的 API 密钥。在 `.env` 文件或环境变量中Set：

| 环境变量 | 服务商 | Get方式 |
|---------|--------|---------|
| `OPENAI_API_KEY` | OpenAI DALL-E 3 | https://platform.openai.com/api-keys |
| `GOOGLE_API_KEY` | Google Imagen 3 | https://aistudio.google.com/apikey |
| `DASHSCOPE_API_KEY` | 通义万象 (阿里云) | https://dashscope.console.aliyun.com/ |
| `REPLICATE_API_TOKEN` | Replicate (Flux 等) | https://replicate.com/account/api-tokens |

### 必需依赖

| 依赖 | 用途 | 安装方式 |
|------|------|---------|
| Python ≥ 3.10 | RunGeneration脚本 | 系统预装 |
| `httpx` | HTTP 请求 | `pip install httpx` |
| `Pillow` | 图片处理 | `pip install Pillow` |

### 可选依赖

| 依赖 | 用途 | 安装方式 |
|------|------|---------|
| `openai` | OpenAI SDK | `pip install openai` |
| `google-genai` | Google Gemini/Imagen SDK | `pip install google-genai` |
| `dashscope` | 通义万象 SDK | `pip install dashscope` |
| `replicate` | Replicate SDK | `pip install replicate` |

---

## Instructions

### 服务商能力对比

| 能力 | DALL-E 3 | Imagen 3 | 通义万象 | Replicate (Flux) |
|------|----------|----------|---------|-----------------|
| 图片质量 | ★★★★★ | ★★★★★ | ★★★★ | ★★★★★ |
| 中文 prompt | ★★★ | ★★★ | ★★★★★ | ★★ |
| 文字渲染 | ★★★★ | ★★★★ | ★★★ | ★★★ |
| 速度 | 中等 | 快 | 快 | 慢 |
| 价格 | 较贵 | 中等 | 便宜 | 按模型 |
| 尺寸灵活度 | 3 种固定 | 多种 | 多种 | 自定义 |
| 风格多样性 | ★★★★★ | ★★★★ | ★★★★ | ★★★★★ |

### 服务商Automatic选择策略

Agent 根据以下规则Automatic选择最合适的服务商：

1. **中文场景优先** — 如果 prompt Yes中文或涉及中国文化元素，优先Use通义万象
2. **文字渲染需求** — 需要在图片中嵌入文字时，优先Use DALL-E 3 或 Imagen 3
3. **速度优先** — 用户要求QuickGeneration时，优先Use Imagen 3 或通义万象
4. **质量优先** — 用户要求最高质量时，Use DALL-E 3 或 Flux Pro
5. **成本优先** — 批量Generation时优先Use通义万象
6. **可用性** — 根据用户已配置的 API Key 选择

用户可Via指定服务商名称覆盖Automatic选择。

### 宽高比速查

| 比例 | 像素（示例） | 适用场景 |
|------|-------------|---------|
| 1:1 | 1024×1024 | 头像、图标、社交媒体帖子 |
| 16:9 | 1792×1024 | PPT 配图、YouTube 封面、桌面壁纸 |
| 4:3 | 1365×1024 | 传统幻灯片、相册 |
| 9:16 | 1024×1792 | 手机壁纸、Instagram Stories、短视频封面 |
| 3:2 | 1536×1024 | 摄影风格、杂志插图 |
| 2:3 | 1024×1536 | 海报、书籍封面 |

### 质量预设

| 预设 | Description | 适用场景 |
|------|------|---------|
| `draft` | Quick草稿，低分辨率 | 方案探索、Quick迭代 |
| `standard` | 标准质量 | 日常Use、网页配图 |
| `hd` | 高清，细节丰富 | 正式出版、印刷 |
| `ultra` | 最高质量，最长等待 | 海报、展览 |

---

## Workflows

### Workflow 1: 单张图片Generation

**步骤 1 — 理解用户意图**

从用户描述中Extract以下信息：

| 要素 | Default | Description |
|------|--------|------|
| 主题 | — | 图片的核心内容 |
| 风格 | 写实 | 摄影、插画、水彩、赛博朋克、扁平化等 |
| 比例 | 1:1 | 根据用途Automatic推断 |
| 质量 | standard | draft/standard/hd/ultra |
| 服务商 | auto | Automatic选择或用户指定 |

**步骤 2 — 构建优化 Prompt**

将用户描述转换为各服务商优化的 prompt（见 Prompt 工程Partial）。

**步骤 3 — CallGeneration API**

根据选择的服务商Call对应 API。

**步骤 4 — Returns结果**

输出Generation的图片File path，并附上Use的 prompt 和参数。

---

### Workflow 2: 批量Generation

一次Generation多张图片供用户选择：

**步骤 1** — 确定批量参数

| Parameter | Description |
|------|------|
| 数量 | 2-8 张（Default 4） |
| 变体策略 | same-prompt（同 prompt 不同种子）/ varied-prompt（不同风格变体） |

**步骤 2** — Generation所有图片

并行Call API 以加Quick度。对于 varied-prompt 模式，为每张图调整 prompt 的风格描述词。

**步骤 3** — 展示结果并让用户选择

---

### Workflow 3: Prompt 优化咨询

当用户不确定如何描述想要的图片时：

1. 与用户对话，逐步明确需求
2. Provides 3-5 个不同方向的 prompt 建议
3. 用户选择方向后微调 prompt
4. Generate image

---

## Prompt 工程指南

### 通用 Prompt 结构

```
[主体描述], [环境/背景], [风格], [光线], [构图], [色调], [细节描述]
```

**示例**：

```
A cozy coffee shop interior with warm lighting,
morning sunlight streaming through large windows,
a steaming cup of latte on a wooden table with an open book,
shot on 35mm film, soft warm tones,
shallow depth of field, photorealistic
```

### DALL-E 3 专用技巧

1. **自然语言描述** — DALL-E 3 理解长句叙述，不需要关键词堆砌
2. **风格指定** — 明确说明 "digital art"、"oil painting"、"photograph" 等
3. **避免No定词** — 说"蓝天"而非"没有云的天空"
4. **文字嵌入** — 直接写 `with text "Hello"` 可以在图中渲染文字
5. **revisedPrompt** — DALL-E 3 会重写 prompt，可从Returns值Get实际Use的 prompt

```python
from openai import OpenAI
client = OpenAI()

response = client.images.generate(
    model="dall-e-3",
    prompt="一只橘猫坐在窗台上看雨，窗外Yes东京夜景，赛博朋克风格，霓虹灯倒映在雨滴中",
    size="1792x1024",
    quality="hd",
    n=1
)
image_url = response.data[0].url
revised_prompt = response.data[0].revised_prompt
```

### Google Imagen 3 专用技巧

1. **结构化描述** — 主体 + 动作 + 环境 + 风格
2. **摄影参数** — 可以指定镜头焦段、光圈、ISO 等
3. **艺术家风格** — 可以参考知名艺术家的风格
4. **多语言Supports** — Supports中文 prompt 但英文效果更佳

```python
from google import genai
client = genai.Client()

response = client.models.generate_images(
    model='imagen-3.0-generate-002',
    prompt='A serene Japanese garden in autumn, koi fish swimming in a crystal clear pond, maple trees with red and orange leaves',
    config=genai.types.GenerateImagesConfig(
        number_of_images=1,
        aspect_ratio='16:9'
    )
)

for image in response.generated_images:
    image.image.save('garden.png')
```

### 通义万象专用技巧

1. **中文 prompt 最优** — 直接Use中文描述效果最佳
2. **风格参数** — Supports `<photography>`、`<anime>`、`<3d cartoon>` 等风格标签
3. **负向提示** — Supports negative_prompt 排除不想要的元素
4. **参考图** — Supports img2img 以图生图

```python
import dashscope

response = dashscope.ImageSynthesis.call(
    api_key=os.getenv('DASHSCOPE_API_KEY'),
    model='wanx-v1',
    input={
        'prompt': '一座雪山下的小木屋，温暖的灯光从窗户透出，天空中有极光，油画风格',
        'negative_prompt': '低质量, 模糊, 变形'
    },
    parameters={
        'size': '1024*1024',
        'n': 1,
        'style': '<oil painting>'
    }
)

image_url = response.output.results[0].url
```

### Replicate (Flux) 专用技巧

1. **英文 prompt** — Flux 模型英文效果远优于中文
2. **极致细节** — 适合描述复杂场景和精细纹理
3. **风格混搭** — 可以混合多种艺术风格
4. **自定义参数** — Supports guidance_scale、steps 等高级参数

```python
import replicate

output = replicate.run(
    "black-forest-labs/flux-1.1-pro",
    input={
        "prompt": "An astronaut riding a horse on Mars, cinematic lighting, 8k resolution, hyperdetailed",
        "aspect_ratio": "16:9",
        "output_format": "png",
        "safety_tolerance": 2
    }
)
```

---

## Output Format

### 文件命名

```
{描述关键词}_{服务商}_{比例}_{日期时间}.png
```

示例：`coffee_shop_dalle3_16x9_20250301_143022.png`

### Output内容

每次图片Generation后，Returns以下信息：

```
📸 图片已Generation
- 文件：./images/coffee_shop_dalle3_16x9.png
- 服务商：DALL-E 3
- 尺寸：1792 × 1024 (16:9)
- 质量：HD
- Prompt：[实际Use的 prompt]
- 耗时：8.3s
- 费用估算：$0.08
```

### 多图对比输出

批量Generation时以网格形式对比展示，标注每张的差异（不同风格、不同构图等）。

---

## Common Pitfalls

### 1. API Key 未配置

**症状**：Call失败，Returns 401/403 错误
**解决**：检查 `.env` 或环境变量中的 API Key YesNo已正确Set

### 2. Prompt 过于模糊

**错误**："画一张好看的图"
**正确**："一只白色的猫咪趴在阳光下的窗台上，背景Yes模糊的绿色植物，温暖的暖色调，胶片摄影风格"

### 3. 比例与用途不匹配

常见错误搭配：
- PPT 配图用了 1:1 → 应该用 16:9
- 手机壁纸用了 16:9 → 应该用 9:16
- 社交媒体头像用了 16:9 → 应该用 1:1

### 4. 中文 prompt 发给 Flux/Replicate

Flux 等模型对中文Supports有限，必须先翻译为英文再Call。Agent 应Automatic完成翻译。

### 5. 图片中文字乱码

AI 图像Generation模型在渲染中文文字时质量不稳定。如果必须在图片中嵌入中文文字，建议：
- 先Generation不含文字的图片
- 然后用 Pillow 在图片上叠加中文文字

```python
from PIL import Image, ImageDraw, ImageFont

img = Image.open('base_image.png')
draw = ImageDraw.Draw(img)
font = ImageFont.truetype('msyh.ttc', size=48)
draw.text((100, 50), '标题文字', fill='white', font=font)
img.save('final_image.png')
```

### 6. 费用失控

批量Generation时注意费用：
| 服务商 | 单张费用（约） |
|--------|-------------|
| DALL-E 3 HD | $0.08 |
| DALL-E 3 Standard | $0.04 |
| Imagen 3 | $0.03 |
| 通义万象 | ¥0.04 |
| Flux Pro | $0.05 |

Generation前提醒用户预估费用。

### 7. 内容安全限制

所有服务商都有内容安全审查。如果Generation被拒绝：
- 检查 prompt YesNoIncludes敏感词
- 调整描述方式
- 不要尝试绕过安全限制

---

## EXTEND.md 扩展

用户可在技能同目录下Create `EXTEND.md` 添加：
- 首选服务商和Default参数
- 自定义风格预设模板
- 品牌相关的 prompt 片段（Logo 描述、品牌色等）
- 额外的 API 端点或自部署模型地址
