# shorts-batch — Skill

Batch-generate multiple short videos in one job.  Each "brief"
(topic + duration + style + aspect) is expanded into a 4-12 shot
scene plan, scored with the SDK's `slideshow_risk` heuristic
(D2.1), then rendered through a pluggable downstream renderer.

## When to invoke

- The user wants to produce *N* shorts on related topics in one go
  ("生成 5 条关于秋季穿搭的 15 秒竖版视频").
- The user has a list of briefs/headlines and wants per-item cost
  estimates + risk scores **before** spending API quota.
- A pipeline step needs to fan out a topic queue into render jobs.

Do **not** invoke for one-off single-video generation — call the
underlying renderer (seedance-video, ppt-to-video) directly.

## Brain tools

| Tool | Use |
|------|-----|
| `shorts_batch_create` | Submit a batch (sync API: returns task id; rendering happens in the background). |
| `shorts_batch_status` | Get one job's status. |
| `shorts_batch_list` | List recent jobs (id / status / N succeeded). |
| `shorts_batch_cancel` | Cancel a running job. |
| `shorts_batch_preview_risk` | Plan all briefs *without* rendering and return `slideshow_risk` verdicts + cost estimates.  Use this before paying for a high-risk batch. |

## HTTP routes

`GET /healthz`, `GET /config`, `POST /config`, `POST /preview-risk`,
`POST /tasks`, `GET /tasks`, `GET /tasks/{id}`,
`POST /tasks/{id}/cancel`, `DELETE /tasks/{id}`.

`POST /preview-risk` body:

```json
{
  "briefs": [
    {"topic": "秋季穿搭", "duration_sec": 15.0,
      "style": "vlog", "target_aspect": "9:16",
      "language": "zh-CN"}
  ]
}
```

`POST /tasks` accepts the same shape plus an optional
`risk_block_threshold` (`"high"` / `"medium"`) which causes the
worker to *skip* (not render) any plan at or above that risk
verdict — the brief is recorded as failed with a clear `error`
message, no quota is spent.

## Pipeline (per brief)

1. **expand** — `scene_planner(brief) -> [shot]`.  Default is a
   deterministic stub that intentionally scores "medium" risk;
   embed a real LLM planner via `Plugin.set_planner`.
2. **score** — `evaluate_slideshow_risk(scene_plan)` returns a
   verdict (`low / medium / high`) on 6 dimensions.
3. **render** — `renderer(plan) -> (output_path, bytes)`.  Default
   stub writes a 1-byte placeholder.  Production wiring goes
   through `Plugin.set_renderer`.
4. **aggregate** — risk distribution, success/failure counts,
   total cost.
5. **verify (D2.10)** — yellow-flag failures, majority-high-risk
   batches, and zero-byte outputs.

## Quality gates

- **G1 input**: `briefs` non-empty (≤ 50); each brief's
  `target_aspect` ∈ ALLOWED_ASPECTS; `duration_sec` ∈ [1, 600].
- **G2 plan**: each plan has ≥ `min_shots`; planner overshoot is
  trimmed to `max_shots`.
- **G3 render**: per-brief renderer exceptions are caught — one
  bad short never poisons the batch.
- **G4 risk gate** (optional): `risk_block_threshold` skips
  high-risk plans before render.
- **G5 verification (D2.10)**: failures, majority-high-risk, and
  zero-byte outputs surface in `verification.low_confidence_fields`.

## Reuse pattern

```python
from plugins.shorts_batch.shorts_engine import (
    ShortBrief, plan_briefs, run_briefs, to_verification,
)

plans = plan_briefs(
    [ShortBrief(topic="秋季穿搭", duration_sec=15.0)],
    scene_planner=my_llm_planner,
)
batch = run_briefs(plans, renderer=my_seedance_renderer,
                    risk_block_threshold="high")
print(batch.to_dict()["risk_distribution"])
print(to_verification(batch).to_dict())
```

## Notes for new contributors

- `default_scene_planner` is intentionally "medium-risk" — its
  job is to surface the flag "you didn't supply a real planner"
  rather than silently producing slideshow-y output.
- Sequential rendering is on purpose (most production renderers
  are GPU-bound and parallel work just causes queue contention).
  Add concurrency at the renderer level, not at the engine.
