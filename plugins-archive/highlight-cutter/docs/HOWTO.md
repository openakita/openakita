# How to call highlight-cutter from another agent

This guide is for **internal agents** (planner, orchestrator, sibling
plugins). End-user docs live in `README.md`.

## Direct REST (when you have apiBase)

```python
import httpx
async with httpx.AsyncClient(base_url=API_BASE) as cli:
    res = await cli.post("/api/plugins/highlight-cutter/api/tasks",
                         json={"source_path": "/abs/path/to/video.mp4",
                               "target_count": 5})
    task_id = res.json()["task_id"]
```

Then poll `GET /api/plugins/highlight-cutter/api/tasks/{task_id}` until
`status` is one of `succeeded` / `failed` / `cancelled`.

## As an LLM tool

Register interest in these tool names:

| tool                          | when to call                                           |
|-------------------------------|--------------------------------------------------------|
| `highlight_cutter_create`     | user wants highlights from a video they already have   |
| `highlight_cutter_status`     | polling                                                |
| `highlight_cutter_list`       | answering "show my recent cuts"                        |
| `highlight_cutter_cancel`     | user changed their mind                                |

The host LLM will pass `tool_name` + `arguments` to `Plugin._handle_tool_call`.

## Output schema

```jsonc
{
  "status": "succeeded",
  "result": {
    "output_path": "/data/.../outputs/<task_id>.mp4",
    "segments": [
      { "start": 12.4, "end": 19.2, "score": 1.05, "reason": "...",
        "text": "...", "label": "段1" }
    ]
  }
}
```

## Cancellation contract

- `BaseTaskManager.cancel_task(task_id)` flips status to `cancelled`.
- The in-flight asyncio worker is also cancelled — the running ffmpeg
  process is left to finish its current segment then exits via the next
  `subprocess.run` boundary.

## Composing with other plugins

- `subtitle-maker` (P2) — pass `output_path` to its `/burn-subtitles` route
- `video-translator` (P2) — pass the same `output_path` for translation
