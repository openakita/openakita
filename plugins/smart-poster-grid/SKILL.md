# Skill — `smart-poster-grid`

Render the **same** poster in 4 social aspect ratios in one shot:
1:1 (Instagram square), 3:4 (RED note vertical), 9:16 (TikTok / Reels
/ Shorts cover), 16:9 (YouTube / Twitter banner).

This plugin is a thin wrapper around the sibling ``poster-maker``
plugin: layout / Pillow rendering / font discovery all live in
``poster-maker/poster_engine.py`` — keep all algorithmic changes
there, never inline new heuristics here.

## When to use

| Scenario | Recommended ratios |
|---|---|
| Marketing campaign across multiple socials | all 4 (default) |
| User only ships on TikTok + IG | `["1x1", "9x16"]` |
| Web banner + post | `["16x9", "1x1"]` |
| 3:4 RED note style only | `["3x4"]` (1 ratio is fine) |

## Tools the brain can call

* `smart_poster_grid_create(text_values, background_image_path?, ratio_ids?)`
* `smart_poster_grid_status(task_id)`
* `smart_poster_grid_list()`
* `smart_poster_grid_cancel(task_id)`
* `smart_poster_grid_ratios()` — list the 4 supported ratios

## HTTP routes

* `GET /healthz`
* `GET /ratios`
* `GET/POST /config`
* `POST /upload-background` — returns ``{path, url}`` for re-use in `/tasks`
* `POST /preview` — return plan WITHOUT rendering
* `POST /tasks` — queue a multi-ratio render
* `GET /tasks` / `GET /tasks/{id}` / `DELETE /tasks/{id}`
* `POST /tasks/{id}/cancel`
* `GET /tasks/{id}/poster/{ratio_id}` — download one PNG (e.g. `/tasks/abc/poster/1x1`)

## Quality Gates

| Gate | What it checks | When | Pass criterion |
|------|---------------|------|----------------|
| G1 输入完整性 | Body is non-empty | route entry | `QualityGates.check_input_integrity` non-blocking |
| G2 ratio_ids 合法 | Every id is in `RATIO_PRESETS` | route entry (synchronous) | `build_grid_plan` returns without `ValueError` |
| G3 部分失败的可观察性 | Failed ratios surface as yellow flags | after worker | `to_verification` emits `LowConfidenceField` for every failed ratio |

## Failure modes the user might see

* **"ratio 9x16 failed; the other ratios still rendered"** — one of
  the renders raised (corrupt background image, missing font...).  The
  task is still **succeeded** at the task level — partial output is
  always preferable to "throw away 3 working posters because the 4th
  broke".  Surfaced as a yellow `Verification` flag.
* **"every render reported ok=True but no output file was found"** —
  rare; usually a quota / permissions issue on the data dir.  Yellow
  flag.

## Coordination with sibling plugins

* **Hard dependency** on `poster-maker` (the Pillow renderer).  If
  ``plugins/poster-maker/poster_engine.py`` is missing, the engine
  raises ``ImportError`` at render time.
* **Reuses** `poster-maker`'s 3 native templates (1:1 ``social-square``,
  3:4 ``vertical-poster``, 16:9 ``banner-wide``).  9:16 is synthesized
  by cloning ``vertical-poster`` and resizing the canvas — slot
  positions are normalized, so the layout reflows cleanly.
