# How to call storyboard from another agent

## REST

```python
res = await client.post("/api/plugins/storyboard/api/tasks",
    json={"script": "...", "target_duration_sec": 30, "title": "...", "style_hint": "..."})
task_id = res.json()["task_id"]

# Poll
detail = await client.get(f"/api/plugins/storyboard/api/tasks/{task_id}")
sb = detail.json()["result"]["storyboard"]   # {title, shots:[{...}], ...}
check = detail.json()["result"]["self_check"]  # {ok, suggestions:[...]}
```

## CSV export

`GET /api/plugins/storyboard/api/tasks/{task_id}/export.csv` → text/csv attachment.

## Output schema

```jsonc
{
  "storyboard": {
    "title": "...", "target_duration_sec": 30, "style_notes": "...",
    "actual_duration_sec": 31.0,
    "shots": [
      {"index": 1, "duration_sec": 5, "visual": "...", "camera": "...",
       "dialogue": "...", "sound": "...", "notes": "..."}
    ]
  },
  "self_check": {"ok": true, "duration_match": "✓ 时长匹配", ...}
}
```

## Composing

- `tongyi-image` — for each shot, send `visual` as the prompt → get reference image
- `seedance-video` — for each shot, send `visual + camera` → get a video clip
- `subtitle-maker` (P2) — feed `dialogue` lines as subtitle script
