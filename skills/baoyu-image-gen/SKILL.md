---
name: jimliu/baoyu-skills@baoyu-image-gen
description: Generate AI images using multiple providers (OpenAI DALL-E, Google Imagen, DashScope/Tongyi Wanxiang, Replicate). Supports various aspect ratios, quality presets, batch generation, and provider-specific prompt engineering techniques.
license: MIT
metadata:
 author: openakita
 version: "1.0.0"
---

# Baoyu Image Gen — AI Generation

## When to Use

- needGenerate image,,,, 
- need, PPT, Create
- need (, ) 
- needGeneration
- neednot AI prompt
- need AI

---

## Prerequisites

### API

needGeneration API. in `.env` orSet: 

| | | Get |
|---------|--------|---------|
| `OPENAI_API_KEY` | OpenAI DALL-E 3 | https://platform.openai.com/api-keys |
| `GOOGLE_API_KEY` | Google Imagen 3 | https://aistudio.google.com/apikey |
| `DASHSCOPE_API_KEY` | () | https://dashscope.console.aliyun.com/ |
| `REPLICATE_API_TOKEN` | Replicate (Flux ) | https://replicate.com/account/api-tokens |

###

| | | |
|------|------|---------|
| Python ≥ 3.10 | RunGeneration | |
| `httpx` | HTTP | `pip install httpx` |
| `Pillow` | | `pip install Pillow` |

### Optional

| | | |
|------|------|---------|
| `openai` | OpenAI SDK | `pip install openai` |
| `google-genai` | Google Gemini/Imagen SDK | `pip install google-genai` |
| `dashscope` | SDK | `pip install dashscope` |
| `replicate` | Replicate SDK | `pip install replicate` |

---

## Instructions

### Service

| | DALL-E 3 | Imagen 3 | | Replicate (Flux) |
|------|----------|----------|---------|-----------------|
| | ★★★★★ | ★★★★★ | ★★★★ | ★★★★★ |
| prompt | ★★★ | ★★★ | ★★★★★ | ★★ |
| | ★★★★ | ★★★★ | ★★★ | ★★★ |
| | | | | |
| | | | | |
| | 3 | | | |
| | ★★★★★ | ★★★★ | ★★★★ | ★★★★★ |

### ServiceAutomatic

Agent Automatic: 

1. **** — prompt Yesor, Use
2. **** — needin, Use DALL-E 3 or Imagen 3
3. **** — needQuickGeneration, Use Imagen 3 or
4. **** — need, Use DALL-E 3 or Flux Pro
5. **** — GenerationUse
6. **** — API Key

ViaAutomatic. 

###

| | () | |
|------|-------------|---------|
| 1:1 | 1024×1024 |,, |
| 16:9 | 1792×1024 | PPT, YouTube, |
| 4:3 | 1365×1024 |, |
| 9:16 | 1024×1792 |, Instagram Stories, |
| 3:2 | 1536×1024 |, |
| 2:3 | 1024×1536 |, |

### Quality

| | Description | |
|------|------|---------|
| `draft` | Quick, |, Quick |
| `standard` | | Use, |
| `hd` |, |, |
| `ultra` |, |, |

---

## Workflows

### Workflow 1: Generation

** 1 — **

Extract: 

| need | Default | Description |
|------|--------|------|
| | — | |
| | |,,,, |
| | 1:1 | Automatic |
| | standard | draft/standard/hd/ultra |
| | auto | Automaticor |

** 2 — Prompt**

prompt ( Prompt Partial). 

** 3 — CallGeneration API**

Call API. 

** 4 — Returns**

Generation File path, Use prompt and. 

---

### Workflow 2: Generation

Generation: 

** 1** —

| Parameter | Description |
|------|------|
| | 2-8 (Default 4) |
| | same-prompt ( prompt not) / varied-prompt (not) |

** 2** — Generationhave

Call API Quick. varied-prompt, prompt. 

** 3** —

---

### Workflow 3: Prompt

notneed: 

1. and, 
2. Provides 3-5 not prompt
3. prompt
4. Generate image

---

## Prompt

### Prompt

```
[], [/], [], [], [], [], []
```

****: 

```
A cozy coffee shop interior with warm lighting,
morning sunlight streaming through large windows,
a steaming cup of latte on a wooden table with an open book,
shot on 35mm film, soft warm tones,
shallow depth of field, photorealistic
```

### DALL-E 3

1. **** — DALL-E 3, notneed
2. **** — "digital art", "oil painting", "photograph"
3. **No** — ""and"have "
4. **** — `with text "Hello"` in
5. **revisedPrompt** — DALL-E 3 will prompt, ReturnsGetUse prompt

```python
from openai import OpenAI
client = OpenAI()

response = client.images.generate(
 model="dall-e-3",
prompt="in, Yes,, in",
 size="1792x1024",
 quality="hd",
 n=1
)
image_url = response.data[0].url
revised_prompt = response.data[0].revised_prompt
```

### Google Imagen 3

1. **** — + + +
2. **** —,, ISO
3. **** —
4. **Supports** — Supports prompt

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

###

1. ** prompt ** — Use
2. **** — Supports `<photography>`, `<anime>`, `<3d cartoon>`
3. **** — Supports negative_prompt notneed
4. **** — Supports img2img

```python
import dashscope

response = dashscope.ImageSynthesis.call(
 api_key=os.getenv('DASHSCOPE_API_KEY'),
 model='wanx-v1',
 input={
'prompt': ',, have, ',
'negative_prompt': ',, '
 },
 parameters={
 'size': '1024*1024',
 'n': 1,
 'style': '<oil painting>'
 }
)

image_url = response.output.results[0].url
```

### Replicate (Flux)

1. ** prompt** — Flux
2. **** — and
3. **** —
4. **** — Supports guidance_scale, steps

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

###

```
{}_{}_{}_{}.png
```

: `coffee_shop_dalle3_16x9_20250301_143022.png`

### Output

Generation, Returns: 

```
📸 Generation
-:./images/coffee_shop_dalle3_16x9.png
-: DALL-E 3
-: 1792 × 1024 (16:9)
-: HD
- Prompt: [Use prompt]
-: 8.3s
-: $0.08
```

###

Generation, (not, not). 

---

## Common Pitfalls

### 1. API Key

****: Call, Returns 401/403
****: `.env` or API Key YesNoSet

### 2. Prompt

****: " "
****: " in, Yes,, "

### 3. andnot

: 
- PPT 1:1 → 16:9
- 16:9 → 9:16
- 16:9 → 1:1

### 4. prompt Flux/Replicate

Flux Supportshave, Call. Agent Automatic. 

### 5.

AI Generationinnot. in,: 
- Generationnot
- Pillow in

```python
from PIL import Image, ImageDraw, ImageFont

img = Image.open('base_image.png')
draw = ImageDraw.Draw(img)
font = ImageFont.truetype('msyh.ttc', size=48)
draw.text((100, 50), '', fill='white', font=font)
img.save('final_image.png')
```

### 6.

Generation: 
| | () |
|--------|-------------|
| DALL-E 3 HD | $0.08 |
| DALL-E 3 Standard | $0.04 |
| Imagen 3 | $0.03 |
| | ¥0.04 |
| Flux Pro | $0.05 |

Generation. 

### 7.

haveallhave. Generation: 
- prompt YesNoIncludes
-
- notneed

---

## EXTEND.md

inCreate `EXTEND.md`: 
- andDefault
-
- prompt (Logo, ) 
- API or