# Seedance API Parameters Reference

## API Endpoints (Volcengine Ark)

- Create: POST https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks
- Status: GET https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/{task_id}
- List: GET https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks?page_num=1&page_size=10
- Delete: DELETE https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/{task_id}

## Models

| Model | Model ID | Key Capabilities | Duration | Resolution |
|-------|----------|-----------------|----------|------------|
| Seedance 2.0 | doubao-seedance-2-0-260128 | Full multimodal, editing, extension, web search | 4-15s | 480p, 720p |
| Seedance 2.0 Fast | doubao-seedance-2-0-fast-260128 | Same as 2.0, faster & cheaper | 4-15s | 480p, 720p |
| Seedance 1.5 Pro | doubao-seedance-1-5-pro-251215 | T2V, I2V, audio, draft, offline | 4-12s | 480p, 720p, 1080p |
| Seedance 1.0 Pro | doubao-seedance-1-0-pro-250528 | T2V, I2V, offline | 2-12s | 480p, 720p, 1080p |
| Seedance 1.0 Pro Fast | doubao-seedance-1-0-pro-fast-251015 | Same as 1.0 Pro, faster | 2-12s | 480p, 720p, 1080p |
| Seedance 1.0 Lite T2V | doubao-seedance-1-0-lite-t2v-250428 | Text-to-video only | 2-12s | 480p, 720p, 1080p |
| Seedance 1.0 Lite I2V | doubao-seedance-1-0-lite-i2v-250428 | I2V, reference images | 2-12s | 480p, 720p, 1080p* |

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| model | string | required | Model ID |
| content | array | required | Multimodal content array |
| ratio | string | 16:9 | 16:9, 9:16, 1:1, 4:3, 3:4, 21:9, adaptive |
| duration | int | 5 | Video duration in seconds |
| resolution | string | 720p | Output resolution |
| generate_audio | bool | true | Generate synchronized audio |
| watermark | bool | false | Add watermark |
| seed | int | — | Random seed for reproducibility |
| camera_fixed | bool | false | Lock camera (1.x only, 2.0 not supported) |
| draft | bool | false | Draft mode (1.5 Pro only) |
| return_last_frame | bool | false | Return last frame image URL |
| tools | array | — | [{"type":"web_search"}] for web search (2.0 text-only) |
| service_tier | string | default | "default" (online) or "flex" (offline, 50% cost) |
| execution_expires_after | int | 172800 | Offline task timeout in seconds |
| callback_url | string | — | Webhook URL for status notifications |

## Content Types

| type | Sub-field | role | Description |
|------|-----------|------|-------------|
| text | text | — | Text prompt |
| image_url | image_url.url | (none/first_frame) | First frame image (default when no role) |
| image_url | image_url.url | first_frame | First frame image (explicit) |
| image_url | image_url.url | last_frame | Last frame image |
| image_url | image_url.url | reference_image | Reference image (2.0 multimodal) |
| video_url | video_url.url | reference_video | Reference video (2.0) |
| audio_url | audio_url.url | reference_audio | Reference audio (2.0) |

## Resolution Pixel Values

### 480p
| Ratio | 2.0 / 1.5 Pro | 1.0 Pro / Lite |
|-------|---------------|----------------|
| 16:9 | 864×496 | 864×480 |
| 4:3 | 752×560 | 736×544 |
| 1:1 | 640×640 | 640×640 |
| 3:4 | 560×752 | 544×736 |
| 9:16 | 496×864 | 480×864 |
| 21:9 | 992×432 | 960×416 |

### 720p
| Ratio | 2.0 / 1.5 Pro | 1.0 Pro / Lite |
|-------|---------------|----------------|
| 16:9 | 1280×720 | 1248×704 |
| 4:3 | 1112×834 | 1120×832 |
| 1:1 | 960×960 | 960×960 |
| 3:4 | 834×1112 | 832×1120 |
| 9:16 | 720×1280 | 704×1248 |
| 21:9 | 1470×630 | 1504×640 |

### 1080p (1.x only)
| Ratio | Pixels |
|-------|--------|
| 16:9 | 1920×1080 |
| 4:3 | 1664×1248 |
| 1:1 | 1440×1440 |
| 3:4 | 1248×1664 |
| 9:16 | 1080×1920 |
| 21:9 | 2206×946 |

## Input Constraints

### Image
- Formats: jpeg, png, webp, bmp, tiff, gif, heic, heif
- Aspect ratio: 0.4-2.5
- Dimensions: 300-6000px per side
- Max size: 30MB per image, request body <64MB
- Count: first-frame=1, first+last=2, 2.0 reference=1-9, 1.0 Lite reference=1-4

### Video (2.0 only)
- Formats: mp4 (H.264/H.265), mov (H.264/H.265)
- Resolution: 480p, 720p
- Duration: 2-15s per video
- Count: max 3 videos, total duration ≤15s
- Max size: 50MB per video
- Aspect ratio: 0.4-2.5, pixels 409600-927408
- FPS: 24-60

### Audio (2.0 only)
- Formats: wav, mp3
- Duration: 2-15s per file
- Count: max 3 files, total duration ≤15s
- Max size: 15MB per file, request body <64MB

### Unsupported Combinations
- Text + Audio only (no image/video)
- Audio only (no text)
- 2.0 does not support real human face reference images/videos (use virtual avatars)

## Rate Limits

| Model | RPM (online) | Concurrency (online) | TPD (offline) |
|-------|-------------|---------------------|---------------|
| 2.0 / 2.0 Fast | 600 | 10 | — |
| 1.5 Pro | 600 | 10 | 5000B |
| 1.0 Pro / Pro Fast | 600 | 10 | 5000B |
| 1.0 Lite T2V / I2V | 300 | 5 | 2500B |

## Image Cropping Rules

When video ratio differs from uploaded image ratio, the system center-crops:
- If image is "too tall" (W/H < target): crop height, keep full width
- If image is "too wide" (W/H > target): crop width, keep full height
- Recommendation: match image ratio to target video ratio for best results
