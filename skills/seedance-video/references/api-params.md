# Seedance 2.0 API Parameters Reference

## API Endpoints

### Volcengine Ark API (ARK_API_KEY)
- Create: POST https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks
- Status: GET https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/{task_id}
- List: GET https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks?page_num=1&page_size=10
- Delete: DELETE https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/{task_id}

### EvoLink API (EVOLINK_API_KEY)
- Create: POST https://api.evolink.ai/v1/videos/generations
- Status: GET https://api.evolink.ai/v1/tasks/{task_id}

## Common Parameters

| Parameter | Type | Default | Options |
|-----------|------|---------|---------|
| duration | int | 5 | 4-15 |
| quality/resolution | string | 720p | 480p, 720p, 1080p(Ark only) |
| aspect_ratio/ratio | string | 16:9 | 16:9, 9:16, 1:1, 4:3, 3:4, 21:9, adaptive |
| generate_audio | bool | true | Audio generation |

## Image Constraints
- Formats: jpeg, png, webp, bmp, tiff, gif, heic, heif
- Aspect ratio: 0.4-2.5
- Dimensions: 300-6000px
- Max size: 30MB

## Video Constraints (2.0 reference mode)
- Formats: mp4, mov
- Duration: 2-15s, max 3 videos, total <=15s
- Max size: 50MB per video

## Audio Constraints (2.0 reference mode)
- Formats: wav, mp3
- Duration: 2-15s, max 3 files, total <=15s
- Max size: 15MB per file
