---
name: generate-image
description: 文生图（Qwen-Image）。根据提示词生成图片并保存为本地 PNG 文件；需要配置 DASHSCOPE_API_KEY，可选配置 DASHSCOPE_IMAGE_API_URL。生成后可用 deliver_artifacts 发送到 IM。
system: true
handler: system
tool-name: generate_image
category: System
priority: high
---

# generate_image - 文生图（Qwen-Image）

使用通义百炼 Qwen-Image 系列模型（如 `qwen-image-max`）根据提示词生成图片，并自动下载保存为本地 PNG 文件。

## 前置条件

- 环境变量：`DASHSCOPE_API_KEY`（与通义其它模型共用）
- 可选：`DASHSCOPE_IMAGE_API_URL`
  - 北京地域（默认）：`https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`
  - 新加坡地域：`https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`

（API 参考：`https://help.aliyun.com/zh/model-studio/qwen-image-api`）

## 用法

```json
{
  "prompt": "一张极简风格的产品海报，白色背景，中心是一只橘猫的线稿，标题“OPENAKITA”",
  "model": "qwen-image-max",
  "size": "1328*1328",
  "prompt_extend": true,
  "watermark": false
}
```

## 参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| prompt | string | 是 | 正向提示词 |
| model | string | 否 | 模型名，默认 `qwen-image-max` |
| negative_prompt | string | 否 | 反向提示词 |
| size | string | 否 | 分辨率，格式 `宽*高`，默认 `1664*928` |
| prompt_extend | boolean | 否 | 是否开启提示词智能改写，默认 true |
| watermark | boolean | 否 | 是否加水印，默认 false |
| seed | integer | 否 | 随机种子 |
| output_path | string | 否 | 输出路径；不填会落到 `data/generated_images/` |

## 返回值

返回 JSON 字符串，包含：
- `saved_to`: 本地 PNG 路径
- `image_url`: 临时图片 URL（通常 24 小时有效）

## 发送到 IM（可选）

生成后如需发送图片到聊天，请调用 `deliver_artifacts`：

```json
{
  "artifacts": [
    {"type": "image", "path": "data/generated_images/xxx.png", "caption": "生成的图片"}
  ]
}
```

