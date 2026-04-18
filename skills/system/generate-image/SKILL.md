---
name: generate-image
description: Generate images from text prompts using Qwen-Image (Dashscope). Saves output as local PNG files. Requires DASHSCOPE_API_KEY. Use deliver_artifacts to send generated images to IM chat.
system: true
handler: system
tool-name: generate_image
category: System
priority: high
---

# generate_image - 文生图（Qwen-Image）

Use通义百炼 Qwen-Image 系列模型（如 `qwen-image-max`）根据提示词Generate image，并AutomaticDownloadSave为本地 PNG 文件。

## Prerequisites

- 环境变量：`DASHSCOPE_API_KEY`（与通义其它模型共用）
- 可选：`DASHSCOPE_IMAGE_API_URL`
  - 北京地域（Default）：`https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`
  - 新加坡地域：`https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`

（API 参考：`https://help.aliyun.com/zh/model-studio/qwen-image-api`）

## 用法

```json
{
  "prompt": "一张极简风格的产品海报，白色背景，中心Yes一只橘猫的线稿，标题“OPENAKITA”",
  "model": "qwen-image-max",
  "size": "1328*1328",
  "prompt_extend": true,
  "watermark": false
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| prompt | string | Yes | 正向提示词 |
| model | string | No | 模型名，Default `qwen-image-max` |
| negative_prompt | string | No | 反向提示词 |
| size | string | No | 分辨率，格式 `宽*高`，Default `1664*928` |
| prompt_extend | boolean | No | YesNo开启提示词智能改写，Default true |
| watermark | boolean | No | YesNo加水印，Default false |
| seed | integer | No | 随机种子 |
| output_path | string | No | 输出路径；不填会落到 `data/generated_images/` |

## Return Values

Returns JSON 字符串，Includes：
- `saved_to`: 本地 PNG 路径
- `image_url`: 临时图片 URL（通常 24 小时有效）

## Send到 IM（可选）

Generation后如需Send图片到聊天，请Call `deliver_artifacts`：

```json
{
  "artifacts": [
    {"type": "image", "path": "data/generated_images/xxx.png", "caption": "Generation的图片"}
  ]
}
```

