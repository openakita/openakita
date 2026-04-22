# How to call subtitle-maker from another agent

```python
res = await client.post("/api/plugins/subtitle-maker/api/tasks",
    json={"source_path": "/abs/video.mp4", "language": "auto",
          "asr_model": "base", "output_format": "both", "burn_into_video": False})
task_id = res.json()["task_id"]
```

Outputs are at:
- `GET /api/plugins/subtitle-maker/api/tasks/{task_id}/srt`
- `GET /api/plugins/subtitle-maker/api/tasks/{task_id}/vtt`
- `GET /api/plugins/subtitle-maker/api/tasks/{task_id}/burned-video` (if requested)
