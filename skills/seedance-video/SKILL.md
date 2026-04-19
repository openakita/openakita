---
name: openakita/skills@seedance-video
description: "Generate AI videos using ByteDance Seedance models via Volcengine Ark API. Supports text-to-video, image-to-video (first frame, first+last frame), multimodal reference (images+videos+audio), video editing, video extension, web search enhancement, audio generation, draft mode, offline inference, and continuous video chaining. Use when user wants to generate, create, edit, or extend AI videos from text prompts, images, videos, or audio."
license: MIT
metadata:
  author: openakita
  version: "2.1.0"
---

# Seedance Video Generation

Generate AI videos using ByteDance Seedance models via the Volcengine Ark API.

## Prerequisites

Set ARK_API_KEY: 
export ARK_API_KEY="your-api-key-here"

Base URL: https://ark.cn-beijing.volces.com/api/v3

## Supports

| | ID | |
|------|---------|------|
| Seedance 2.0 | doubao-seedance-2-0-260128 | All capabilities: text/image/multimodal/edit/extend/web search/audio |
| Seedance 2.0 Fast | doubao-seedance-2-0-fast-260128 | Same as 2.0, faster and cheaper |
| Seedance 1.5 Pro | doubao-seedance-1-5-pro-251215 | Text-to-video, image-to-video, audio, draft mode, offline inference |
| Seedance 1.0 Pro | doubao-seedance-1-0-pro-250528 | Text-to-video, image-to-video, offline inference |
| Seedance 1.0 Pro Fast | doubao-seedance-1-0-pro-fast-251015 | Same as 1.0 Pro, faster |
| Seedance 1.0 Lite T2V | doubao-seedance-1-0-lite-t2v-250428 | Text-to-video only |
| Seedance 1.0 Lite I2V | doubao-seedance-1-0-lite-i2v-250428 | Image-to-video, reference images |

Default model: doubao-seedance-2-0-260128

## Seedance 2.0 Capabilities Overview

- **Text-to-video**: Generate video from a plain text prompt
- **Image-to-video**: First frame (first_frame) / first+last frame (first_frame+last_frame)
- **Multimodal reference**: Combine images (0–9), videos (0–3), and audio (0–3)
- **Video editing**: Replace subjects, add/remove/modify objects, local repaint/inpainting
- **Video extension**: Extend forward/backward, chain multiple segments
- **Web search**: Web search in text-only mode via web_search, improves timeliness
- **Audio video**: generate_audio=true produces synchronized audio
- **Return last frame**: return_last_frame=true retrieves the final frame of a video (for continuous generation)

## Content Structure

The Ark API uses a content array to pass multimodal inputs:

| type | Sub-field | role | Description |
|------|--------|------|------|
| text | text | — | Text prompt |
| image_url | image_url.url | first_frame | First-frame image |
| image_url | image_url.url | last_frame | Last-frame image |
| image_url | image_url.url | reference_image | Reference image (2.0 multimodal) |
| video_url | video_url.url | reference_video | Reference video (2.0 editing/extension/multimodal) |
| audio_url | audio_url.url | reference_audio | Reference audio (2.0 multimodal) |

**Asset reference rules**: Reference assets in your prompt using "asset type + index", e.g. "image 1", "video 2", "audio 1". The index is the sequential order of that asset type within the content array (starting at 1). Asset IDs cannot be used to reference assets.

## Prompt Tips

### Basic Formula
**Prompt = Subject + Motion, Background + Motion, Camera + Motion**

### General Advice
- Use concise, precise natural language to describe the desired effect
- Replace abstract descriptions with concrete ones; put important content first
- Text-to-video has higher randomness — useful for inspiration
- For image-to-video, upload high-resolution, high-quality images when possible
- If you have a clear vision, consider generating an image first and then using image-to-video

### 2.0 Multimodal Reference Formulas
- **Image reference**: Reference/extract/combine the "subject description" from "image N", generate "scene description", maintain consistent features
- **Video reference**: Reference the "action/camera movement/effect description" from "video N", maintain consistency
- **Audio reference**: Voice → "[character]" says: "[dialogue]", voice reference from "audio N"; Content → timing + "audio N"

### 2.0 Video Editing Formulas
- **Add element**: Describe "element features" + "timing of appearance" + "position"
- **Remove element**: Specify the target to remove, emphasize elements that should stay unchanged
- **Modify element**: Clearly describe what to replace it with

### 2.0 Video Extension Formulas
- **Single segment extend**: Extend "video N" forward/backward + "description of extended content"
- **Multi-segment chain**: "video 1" + "transition description" + followed by "video 2" + "transition description" + followed by "video 3"

## Parameters

| Parameter | Type | Default | Description |
|------|------|--------|------|
| ratio | string | 16:9 | 16:9, 4:3, 1:1, 3:4, 9:16, 21:9, adaptive |
| duration | int | 5 | Video duration: 2.0=4–15s, 1.5=4–12s, 1.0=2–12s |
| resolution | string | 720p | 480p, 720p (2.0); 480p, 720p, 1080p (1.x) |
| generate_audio | bool | true | Generate synchronized audio |
| watermark | bool | false | Add watermark |
| seed | int | — | Random seed (for reproducibility) |
| camera_fixed | bool | false | Fix camera position (1.x) |
| draft | bool | false | Draft mode, low-cost preview (1.5 Pro only) |
| return_last_frame | bool | false | Return the last frame of the video (for continuous generation) |
| tools | array | — | [{"type":"web_search"}] web search (2.0 text-only mode) |
| service_tier | string | default | default=online inference, flex=offline inference at half price (1.5 Pro / 1.0 series only) |
| execution_expires_after | int | 172800 | Offline task timeout in seconds (applies in flex mode) |
| callback_url | string | — | Webhook callback URL; notified when task status changes |

## Advanced Usage

### Offline Inference (Half Price)
Set service_tier="flex" for 50% off compared to online inference. Supported by 1.5 Pro and 1.0 series models only; not available for 2.0. Best for batch generation where latency is not a concern.

### Draft Mode (Two-Step Workflow)
1. Generate a low-cost preview video with draft=true to validate composition, camera movement, and motion.
2. After confirmation, use the draft video URL as reference_video to generate the final video.

### Continuous Video Generation
Set return_last_frame=true and use the last frame of the previous video as the first frame of the next, iterating to produce multiple continuous segments. Use FFmpeg to concatenate them into a longer video.

## Usage Limits

- **Images**: jpeg/png/webp/bmp/tiff/gif/heic/heif, 300–6000px, <30MB, aspect ratio 0.4–2.5
- **Videos**: mp4/mov, 2–15s each, up to 3 videos, total duration ≤15s, <50MB each
- **Audio**: wav/mp3, 2–15s each, up to 3 files, total duration ≤15s, <15MB each
- **Unsupported combinations**: "text + audio" or "audio only" inputs
- **Video URLs expire in 24 hours** — download immediately
- **2.0 does not support uploading reference images/videos containing real human faces** — use virtual avatars (asset://ASSET_ID)

## Pre-built Scripts

This skill provides a Python CLI (pure stdlib, zero dependencies): `scripts/seedance.py`

```bash
# Text-to-video
python3 scripts/seedance.py create --prompt "cat yawning" --wait --download ~/Desktop

# Image-to-video (first frame)
python3 scripts/seedance.py create --prompt "person turns head and smiles" --image photo.jpg --wait

# Multimodal reference (2.0)
python3 scripts/seedance.py create --prompt "reference the style in image 1" \
  --ref-images style.jpg --ref-videos clip.mp4 --ref-audios bgm.mp3 --wait

# Video editing (2.0)
python3 scripts/seedance.py create --prompt "replace the cat in video 1 with a dog" \
  --ref-videos original.mp4 --wait

# Video extension (2.0)
python3 scripts/seedance.py create --prompt "continue video 1 and transition into video 2" \
  --ref-videos clip1.mp4 clip2.mp4 --duration 10 --wait

# Web search
python3 scripts/seedance.py create --prompt "glass frog macro close-up" --web-search --wait

# Offline inference (half price, 1.5 Pro / 1.0 series only)
python3 scripts/seedance.py create --prompt "sunset beach" \
  --model doubao-seedance-1-5-pro-251215 --service-tier flex --wait

# Continuous video chain generation (auto-stitches via last frame)
python3 scripts/seedance.py chain \
  "girl holding a fox, gazing gently at the camera" \
  "girl and fox running through a meadow" \
  "girl and fox resting under a tree" \
  --image first_frame.jpg --download ~/Desktop

# Query / list / delete
python3 scripts/seedance.py status <TASK_ID>
python3 scripts/seedance.py list
python3 scripts/seedance.py delete <TASK_ID>
```
