# How to call poster-maker from another agent

```python
templates = await client.get("/api/plugins/poster-maker/api/templates")
res = await client.post("/api/plugins/poster-maker/api/tasks",
    json={"template_id": "social-square",
          "text_values": {"title": "夏日特惠", "subtitle": "全场 5 折",
                          "cta": "立即购买"}})
task_id = res.json()["task_id"]
```

Output PNG at `GET /api/plugins/poster-maker/api/tasks/{task_id}/poster`.
