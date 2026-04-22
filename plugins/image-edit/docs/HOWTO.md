# How to call image-edit from another agent

## REST

```python
res = await client.post("/api/plugins/image-edit/api/tasks",
    json={"source_path": "/abs/in.png", "prompt": "把背景换成樱花",
          "mask_path": "/abs/mask.png", "size": "1024x1024", "n": 1,
          "provider": "auto"})
```

Then poll `GET /api/plugins/image-edit/api/tasks/{task_id}`.

## Output schema

```jsonc
{
  "status": "succeeded",
  "result": {
    "provider": "openai-gpt-image-1",
    "output_paths": ["/data/.../outputs/<uuid>_0.png"]
  }
}
```

## Composing

- Pass `output_paths[0]` to `poster-maker` (P2) for typography
- Pass to `storyboard` to generate variants in matching style
