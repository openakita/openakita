# How to call avatar-speaker from another agent

## REST

```python
res = await client.post("/api/plugins/avatar-speaker/api/tasks",
    json={"text": "...", "voice": "zh-CN-XiaoxiaoNeural", "provider": "auto"})
task_id = res.json()["task_id"]

# Poll
detail = await client.get(f"/api/plugins/avatar-speaker/api/tasks/{task_id}")
audio = detail.json()["result"]["audio_path"]   # absolute path on disk
```

The audio file is also served at `GET /api/plugins/avatar-speaker/api/audio/{task_id}` for streaming/download.

## Composing

- `highlight-cutter` — pass `audio_path` to its `/burn-audio` route (P2)
- `video-translator` (P2) — request a TTS in the target language
