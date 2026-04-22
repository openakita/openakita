# How to call video-translator from another agent

```python
# 1) Upload video
with open("input.mp4", "rb") as f:
    up = await client.post("/api/plugins/video-translator/api/upload-video",
                           files={"file": ("input.mp4", f, "video/mp4")})
src = up.json()["path"]

# 2) Create task
res = await client.post("/api/plugins/video-translator/api/tasks",
    json={"source_video_path": src, "target_language": "en",
          "voice": "en-US-AriaNeural"})
task_id = res.json()["task_id"]
```

Outputs: `GET .../tasks/{id}/video` and `.../tasks/{id}/srt`.

## Composition

- `video-translator` 内部已经是 `subtitle-maker` (ASR/SRT) + `tts-studio` (TTS) 的组合，无需再串外部插件。
- 想要更便宜：把 `tts_provider` 设为 `"stub"`（生成静音视频），仅获取翻译字幕。
