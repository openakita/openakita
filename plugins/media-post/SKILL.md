# Media Post Skill Definition

Single source of truth: [`docs/media-post-plan.md`](../../docs/media-post-plan.md) v1.0.

## 1. Trigger Scenarios

Use this skill when the user wants to:

- Pick the best cover image from an edited video
- Repurpose a 16:9 video to 9:16 (Reels / Shorts / TikTok) or 1:1 (feed)
- Generate platform-specific titles, descriptions, hashtags, and chapter
  timestamps for TikTok / Bilibili / WeChat / Xiaohongshu / YouTube
- Render chapter / section cards as PNGs from a chapter list

Keywords (zh + en): 封面, 选封面, 竖版, 9:16, 重构图, 多端, SEO, 标题,
hashtag, 章节卡, cover, vertical, recompose, repurpose, hashtags,
description, chapter cards, vlog publishing, social media kit.

## 2. Command Reference

| Tool | Purpose |
|------|---------|
| `media_post_create` | Submit a new task (any of 4 modes) |
| `media_post_status` | Get the current state and outputs of one task |
| `media_post_list` | List recent tasks (filterable by mode and status) |
| `media_post_cancel` | Cancel a running task |

## 3. Input Schema

### `media_post_create`

```json
{
  "mode": "cover_pick | multi_aspect | seo_pack | chapter_cards",
  "video_path": "/uploads/edited.mp4",
  "params": {
    // cover_pick
    "quantity": 8,
    "min_score_threshold": 3.0,
    "platform_hint": "universal | tiktok | bilibili | wechat | xiaohongshu | youtube",

    // multi_aspect
    "target_aspects": ["9:16", "1:1"],
    "recompose_fps": 2.0,
    "letterbox_fallback": true,

    // seo_pack
    "platforms": ["tiktok", "bilibili", "wechat", "xiaohongshu", "youtube"],
    "instruction": "Pet daily vlog",
    "subtitle_path": "/uploads/sub.srt",
    "include_chapters": true,

    // chapter_cards
    "template_id": "modern | minimal | retro | youtube_style | custom",
    "chapters": [
      { "title": "Intro", "subtitle": "Why we made this", "start_time": 0 }
    ]
  },
  "cost_approved": false
}
```

`cost_approved=true` is required only when the cost preview crosses the
configured warn threshold (default ¥10) — the API replies with HTTP 402
`ApprovalRequired` otherwise.

### `media_post_status`

```json
{ "task_id": "abc123def456" }
```

### `media_post_list`

```json
{ "mode": "cover_pick", "status": "succeeded", "limit": 20, "offset": 0 }
```

### `media_post_cancel`

```json
{ "task_id": "abc123def456" }
```

## 4. Output Schema

### Task envelope (shared by all modes)

```json
{
  "id": "abc123def456",
  "mode": "cover_pick",
  "status": "pending | running | succeeded | failed | cancelled",
  "pipeline_step": "setup | check_deps | estimate_cost | prepare_assets | <mode_run> | finalize",
  "progress": 0.0,
  "cost_estimate_cny": 0.32,
  "cost_actual_cny": 0.31,
  "error_kind": "network | timeout | auth | quota | moderation | dependency | format | duration | unknown",
  "error_message": "...",
  "error_hints": ["...", "..."],
  "outputs": { /* mode-specific, see below */ }
}
```

### Mode-specific outputs

| Mode | Output shape |
|------|--------------|
| `cover_pick` | `{ "covers": [{ "path": "...", "score": 4.2, "axis_scores": {...}, "best_for": "...", "bbox": {...} }] }` |
| `multi_aspect` | `{ "outputs": [{ "aspect": "9:16", "path": "...", "trajectory": [[t, x], ...] }] }` |
| `seo_pack` | `{ "platforms": { "tiktok": { "title": "...", "description": "...", "hashtags": [...] }, ... } }` |
| `chapter_cards` | `{ "cards": [{ "index": 0, "path": "...", "title": "..." }] }` |

## 5. Error Codes (canonical 9-key taxonomy)

| Kind | Meaning | User Action |
|------|---------|-------------|
| `network` | Connection failed / DNS / proxy | Check network, retry |
| `timeout` | Vendor request exceeded the timeout window | Retry, or split into smaller jobs |
| `auth` | Invalid / missing DashScope key | Settings → re-enter API Key |
| `quota` | Insufficient balance or hitting QPS limits | Top up at Alibaba Cloud DashScope |
| `moderation` | Content flagged by the vendor | Edit out the flagged section |
| `dependency` | ffmpeg / Playwright not installed | Install per README §2 |
| `format` | Unparseable VLM JSON / unsupported video container | Re-encode or retry |
| `duration` | Source video > 120 minutes | Trim before upload |
| `unknown` | Unexpected error | Report `task_id` to maintainers |

Hints are localised — see `mediapost_models.ERROR_HINTS` for the full
zh + en text the UI surfaces.

## 6. Mode Decision Tree

```
User wants something from an edited video →
  ├── "I need a thumbnail / cover image" → cover_pick
  ├── "Make it vertical / fit Shorts / TikTok / Reels" → multi_aspect
  ├── "Write the title / description / hashtags" → seo_pack
  ├── "I have chapters and need cards / overlays" → chapter_cards
  └── Not sure → ask which platform they're publishing to first
```

For multi-platform launches the typical chain is `cover_pick` →
`multi_aspect` → `seo_pack` (cross-plugin handoff to a publishing plugin
is reserved for v0.2 — `assets_bus` schema is already in place).

## 7. Cost Estimation

| Operation | Cost | Triggers warn / danger? |
|-----------|------|-------------------------|
| `cover_pick` (any video) | ~¥0.32 | no |
| `multi_aspect` 30 s @ fps=2 | ~¥0.32 | no |
| `multi_aspect` 30 min @ fps=2 | ~¥35 | **danger** — requires explicit `cost_approved=true` |
| `seo_pack` (5 platforms) | ~¥0.025 | no |
| `chapter_cards` | ¥0 | no |

Defaults: warn ≥ ¥10, danger ≥ ¥30 (configurable in Settings).

## 8. Common Templates

### Pick 6 covers from a YouTube-style video

```
media_post_create
  mode=cover_pick
  video_path=/uploads/episode_42.mp4
  params={"quantity":6,"platform_hint":"youtube","min_score_threshold":3.5}
```

### Repurpose to 9:16 only (cheaper)

```
media_post_create
  mode=multi_aspect
  video_path=/uploads/episode_42.mp4
  params={"target_aspects":["9:16"],"recompose_fps":1.0}
```

### SEO pack for a pet vlog (skip Xiaohongshu)

```
media_post_create
  mode=seo_pack
  video_path=/uploads/cat.mp4
  params={"platforms":["tiktok","bilibili","youtube"],"instruction":"Pet daily vlog"}
```

### Render 3 chapter cards in modern template

```
media_post_create
  mode=chapter_cards
  video_path=/uploads/lecture.mp4
  params={"template_id":"modern","chapters":[
    {"title":"Intro","subtitle":"Why we made this"},
    {"title":"Demo","subtitle":"Live walkthrough"},
    {"title":"Wrap-up","subtitle":"Key takeaways"}
  ]}
```

## 9. Testing

```bash
# Unit tests — fully hermetic, no network, no API key
py -3.11 -m pytest plugins/media-post/tests -q -m "not integration"

# Integration smokes — needs DASHSCOPE_API_KEY (cost < ¥1.5 total)
DASHSCOPE_API_KEY=sk-... py -3.11 -m pytest plugins/media-post/tests/integration -m integration

# Recompose smoke also needs MEDIA_POST_SMOKE_VIDEO=/abs/path/to/clip.mp4 (≤ 30 s)
```

## 10. Known Limitations (v0.1.0)

- `multi_aspect` only emits `9:16` and `1:1` (no `3:4`, no `21:9` yet).
- No auto-chapter detection — you bring the chapter list.
- `assets_bus` is schema-only; cross-plugin handoff lands in v0.2 with
  `/handoff/*` routes.
- `chapter_cards` Playwright path needs Chromium + (for CJK) suitable
  fonts; otherwise transparently falls back to ffmpeg `drawtext`.
- Crop expression depth is hard-capped at 95 levels (see `VALIDATION.md`
  §3); longer videos are downsampled before expression assembly.
- Per-platform SEO templates are baked-in; user-provided YAML overrides
  are out of scope for v1.0.
