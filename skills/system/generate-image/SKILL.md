---
name: generate-image
description: Generate images from text prompts using Qwen-Image (Dashscope). Saves output as local PNG files. Requires DASHSCOPE_API_KEY. Use deliver_artifacts to send generated images to IM chat.
system: true
handler: system
tool-name: generate_image
category: System
priority: high
---

# generate_image - (Qwen-Image) 

Use Qwen-Image ( `qwen-image-max`) Generate image, AutomaticDownloadSave PNG. 

## Prerequisites

-: `DASHSCOPE_API_KEY` (and) 
-: `DASHSCOPE_IMAGE_API_URL`
- (Default): `https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`
-: `https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`

 (API: `https://help.aliyun.com/zh/model-studio/qwen-image-api`) 

## Usage

```json
{
"prompt": ",, Yes, “OPENAKITA”",
 "model": "qwen-image-max",
 "size": "1328*1328",
 "prompt_extend": true,
 "watermark": false
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| prompt | string | Yes | |
| model | string | No |, Default `qwen-image-max` |
| negative_prompt | string | No | |
| size | string | No |, `*`, Default `1664*928` |
| prompt_extend | boolean | No | YesNo, Default true |
| watermark | boolean | No | YesNo, Default false |
| seed | integer | No | |
| output_path | string | No |; notwill `data/generated_images/` |

## Return Values

Returns JSON, Includes: 
- `saved_to`: PNG
- `image_url`: URL ( 24 have) 

## Send IM () 

GenerationSend, Call `deliver_artifacts`: 

```json
{
 "artifacts": [
{"type": "image", "path": "data/generated_images/xxx.png", "caption": "Generation "}
 ]
}
```