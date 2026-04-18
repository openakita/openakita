---
name: openakita/skills@seedance-video
description: "Generate AI videos using ByteDance Seedance models via Volcengine Ark API. Supports text-to-video, image-to-video (first frame, first+last frame, reference images), audio generation, and draft mode. Use when user wants to generate, create, or produce AI videos from text prompts or images."
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Seedance Video Generation

通过火山方舟 API 使用字节跳动 Seedance 模型生成 AI 视频。

## Prerequisites

需设置 ARK_API_KEY 环境变量：
export ARK_API_KEY="your-api-key-here"

Base URL: https://ark.cn-beijing.volces.com/api/v3

## 支持模型

| 模型 | 模型 ID | 能力 |
|------|---------|------|
| Seedance 1.5 Pro | doubao-seedance-1-5-pro-251215 | 文生视频、图生视频、音频、草稿模式 |
| Seedance 1.0 Pro | doubao-seedance-1-0-pro-250428 | 文生视频、图生视频 |
| Seedance 1.0 Lite T2V | doubao-seedance-1-0-lite-t2v-250219 | 仅文生视频 |
| Seedance 1.0 Lite I2V | doubao-seedance-1-0-lite-i2v-250219 | 图生视频、参考图 |

默认模型: doubao-seedance-1-5-pro-251215

## 文生视频

curl -s -X POST "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -d '{"model":"doubao-seedance-1-5-pro-251215","content":[{"type":"text","text":"YOUR_PROMPT"}],"ratio":"16:9","duration":5,"resolution":"720p","generate_audio":true}'

## 图生视频（首帧）

将用户提供的图片作为视频首帧，content 中添加 type=image_url, role=first_frame 的元素。ratio 建议设为 adaptive。

## 图生视频（首尾帧）

同时提供首帧和尾帧图片，分别设置 role=first_frame 和 role=last_frame。

## 查询任务状态

curl -s -X GET "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/${TASK_ID}" \
  -H "Authorization: Bearer $ARK_API_KEY"

状态为 succeeded 时从 content.video_url 获取视频地址。视频 URL 24 小时内有效，需立即下载。

## Parameters参考

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| ratio | string | 16:9 | 16:9, 4:3, 1:1, 3:4, 9:16, 21:9, adaptive |
| duration | int | 5 | 视频时长（秒），4-12 |
| resolution | string | 720p | 480p, 720p, 1080p |
| generate_audio | bool | true | 生成同步音频（仅 1.5 Pro） |
| draft | bool | false | 草稿模式，低成本预览（仅 1.5 Pro） |

## Notes

- 轮询间隔建议 15 秒
- 视频 URL 24 小时过期，需立即下载
- 任务历史保留 7 天
- 本地图片需转为 base64 data URL

## Pre-built Scripts

本 skill 提供以下可执行 Python 脚本（纯 stdlib，零依赖）：

### scripts/seedance.py
视频生成 CLI 工具，支持 Volcengine Ark 和 EvoLink 双后端。

```bash
# 文生视频（创建 + 等待 + 下载）
python3 scripts/seedance.py create --prompt "小猫打哈欠" --wait --download ~/Desktop

# 图生视频
python3 scripts/seedance.py create --prompt "人物转头微笑" --image photo.jpg --wait --download ~/Desktop

# 2.0 参考视频模式（需 EVOLINK_API_KEY）
python3 scripts/seedance.py create --prompt "续拍这个画面" --video clip.mp4 --audio bgm.mp3 --wait

# 查询任务状态
python3 scripts/seedance.py status <TASK_ID>
```
