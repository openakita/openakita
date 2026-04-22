# How to call tts-studio from another agent

```python
res = await client.post("/api/plugins/tts-studio/api/tasks",
    json={"script": "A: 你好\nB: 再见", "default_voice": "zh-CN-XiaoxiaoNeural",
          "voice_map": {"A": "zh-CN-XiaoxiaoNeural", "B": "zh-CN-YunxiNeural"}})
task_id = res.json()["task_id"]
```

Output served at `GET /api/plugins/tts-studio/api/tasks/{task_id}/audio`.
