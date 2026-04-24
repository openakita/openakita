# Launch Kit 发布物料工坊

> Plugin id 保持 `media-post` 不变；仅中/英文展示名由"媒体发布套件 / Media Post"更名为"发布物料工坊 / Launch Kit"（强调：这是**发布前**的物料整理工具，不是发布动作本身）。

OpenAkita first-class plugin that turns an edited video into platform-ready
publishing assets — covers, vertical recompose, SEO copy, chapter cards.

## 1. Overview

`media-post` ships **four** post-edit modes wired to DashScope Qwen-VL-max
(vision) and Qwen-Plus (text), all driven from a single React UI that
mirrors `tongyi-image`'s look and feel.

| Mode | What it does | Vendor model | Local tool |
|------|--------------|--------------|------------|
| `cover_pick` | 6-axis aesthetic scoring, picks top covers | Qwen-VL-max | ffmpeg `thumbnail` |
| `multi_aspect` | Smart 16:9 → 9:16 / 1:1 with EMA-smoothed subject tracking | Qwen-VL-max | ffmpeg crop expression |
| `seo_pack` | 5-platform SEO bundle (TikTok / Bilibili / WeChat / Xiaohongshu / YouTube) | Qwen-Plus | — |
| `chapter_cards` | Chapter PNGs from HTML templates | — | Playwright (primary) + ffmpeg `drawtext` (fallback) |

Single source of truth: [`docs/media-post-plan.md`](../../docs/media-post-plan.md) v1.0.

## 2. Installation

`media-post` is a built-in plugin — no separate install step. Requirements:

- **OpenAkita** ≥ 1.27.0 with SDK `>=0.7.0,<0.8.0`
- **ffmpeg** ≥ 4.0 on `PATH` (every mode except `seo_pack` needs it)
- **DashScope API Key** for `cover_pick` / `multi_aspect` / `seo_pack`
- **Playwright Chromium** *(optional)* for the high-fidelity
  `chapter_cards` path; without it the plugin transparently falls back to
  ffmpeg `drawtext` (works without Chinese fonts on Linux but the type is
  blockier)

The plugin **does not introduce any new Python or npm dependencies** — see
[`docs/media-post-plan.md` §13](../../docs/media-post-plan.md) red-line list.

## 3. Configuration

1. Open `Media Post` from the sidebar
2. **Settings** tab → enter your DashScope API Key → **Save**
3. (Optional) tune VLM batch size, scene-cut threshold, EMA alpha, and
   cost guardrails (warn ≥ ¥10, danger ≥ ¥30)

## 4. Usage

### Quick start

1. **Create** tab → pick a mode in the 4-button row
2. Upload a video (or fill `instruction` for `seo_pack` / chapter list for
   `chapter_cards`)
3. Click **Estimate** to preview cost; if it crosses ¥10 the primary
   button flips to "Confirm & continue (¥xx)" and requires explicit consent
4. Click **Start** → watch progress in the right pane
5. **Tasks** tab → inspect history, retry, cancel, or open the
   mode-specific detail panel (cover gallery / aspect viewer / SEO sub-tabs
   / chapter gallery)

### Mode notes

- **cover_pick** — `quantity` 1-12 (default 8), `min_score_threshold`
  filters out low scores. Output PNGs land under
  `<data_dir>/tasks/<id>/covers/`.
- **multi_aspect** — currently locked to `9:16` and `1:1` per v1.0 scope.
  Scene cuts come from ffmpeg `select='gt(scene,0.4)'`; subject tracking
  reads bounding boxes from Qwen-VL-max and smooths centers with EMA
  (`alpha=0.15`). Crop expression depth is hard-capped at **95** (see
  [`VALIDATION.md`](VALIDATION.md) §3); longer videos are downsampled
  before expression assembly.
- **seo_pack** — 5 platforms run in parallel `asyncio.gather`; one
  platform failing does not poison the others.
- **chapter_cards** — Playwright path renders HTML templates with custom
  DSL placeholders (`{{name:type=default}}`); fallback uses ffmpeg
  `drawtext` and skips background images.

## 5. Architecture

```
plugins/media-post/
├── plugin.json                      # 4 tools, no /handoff/* in v1.0
├── plugin.py                        # 22 routes, Pydantic extra="forbid"
├── mediapost_models.py              # 4 modes + 5 platforms + 9 error_kind
├── mediapost_task_manager.py        # aiosqlite, 6 tables (assets_bus reserved)
├── mediapost_vlm_client.py          # Qwen-VL-max + Qwen-Plus
├── mediapost_cover_picker.py        # ffmpeg thumbnail + VLM score + sort
├── mediapost_recompose.py           # 4-step EMA smart-recompose
├── mediapost_seo_generator.py       # 5-platform parallel SEO
├── mediapost_chapter_renderer.py    # Playwright + drawtext fallback
├── mediapost_pipeline.py            # 8-step orchestrator (4 modes)
├── mediapost_inline/                # vendored upload_preview + storage_stats
├── tests/                           # 231 unit + 2 opt-in integration
├── ui/dist/                         # single-file React UI + 5 _assets
├── README.md  SKILL.md  CHANGELOG.md
└── VALIDATION.md  USER_TEST_CASES.md (gitignored)
```

## 6. Troubleshooting

| Symptom | Action |
|---------|--------|
| `error_kind=auth` | Settings → re-enter DashScope API Key |
| `error_kind=quota` | DashScope console → top up balance |
| `error_kind=dependency` "ffmpeg missing" | `ffmpeg -version`, install if blank |
| `error_kind=format` on `multi_aspect` | Re-encode source to H.264/H.265 MP4 |
| `error_kind=duration` | Trim to ≤ 120 minutes |
| `chapter_cards` falls back to drawtext on every render | Install Playwright + Chromium (`pip install playwright && playwright install chromium`) |
| Cost preview says ¥35 for a long video | Either trim, lower `recompose_fps` from 2.0 → 1.0, or pre-confirm the spend |

## 7. 5-minute manual smoke

Run after install / upgrade to verify the plugin end-to-end. Mirrors
[`docs/media-post-plan.md` §10.4](../../docs/media-post-plan.md).

```bash
# 1. Make sure ffmpeg is on PATH
ffmpeg -version

# 2. Hermetic unit tests pass
cd plugins/media-post
py -3.11 -m pytest tests -q

# 3. Open OpenAkita → sidebar → Media Post → Settings → paste API Key → Save
#    Verify the green "API key configured" pill appears in the header.

# 4. Create tab → pick `cover_pick` → upload a short clip (≤ 30 s) →
#    Estimate (~¥0.32) → Start. Within ~15 s the right pane shows 8 PNG
#    candidates with 6-axis score badges.

# 5. Switch to `multi_aspect` → same clip → check 9:16 → Estimate
#    (~¥0.32 for 30 s @ fps=2) → Start. Within ~30 s the right pane shows
#    a vertical preview and a trajectory polyline.

# 6. Switch to `seo_pack` → instruction "Pet daily vlog" → Start. The 5
#    sub-tabs (TikTok / Bilibili / WeChat / Xiaohongshu / YouTube) populate
#    in <10 s; per-platform Copy buttons round-trip to clipboard.

# 7. Switch to `chapter_cards` → fill 3 chapters by hand → Render. 3 PNGs
#    appear in the gallery. (If Playwright is missing, the cards still
#    render via the drawtext fallback path — visually blockier but valid.)

# 8. Tasks tab → see all four runs in history with their costs and modes.
```

## 8. Cost reference

| Operation | Cost | Notes |
|-----------|------|-------|
| `cover_pick` 1 run (4 batches × 8 frames) | ~¥0.32 | independent of video length |
| `multi_aspect` 30 s clip @ fps=2 | ~¥0.32 | 60 frames → 8 batches |
| `multi_aspect` 30 min clip @ fps=2 | ~¥35 | **gated by ApprovalRequired** |
| `seo_pack` 5 platforms (Qwen-Plus) | ~¥0.025 | ~¥0.005 / platform |
| `chapter_cards` | ¥0 | local rendering only |

Set warn / danger thresholds in **Settings → Cost control** (defaults: 10 / 30 ¥).

## 9. Known limitations (v0.1.0)

- `multi_aspect` only emits `9:16` and `1:1`. `3:4` and `21:9` are deferred to v0.2.
- No automatic chapter detection — you supply the chapter list (or import it
  from a `seo_pack` output).
- `assets_bus` table is **schema-only** in v1.0. Cross-plugin handoff (e.g.
  `subtitle-craft` → `media-post`) ships in v0.2 alongside `/handoff/*` routes.
- The chapter-card template DSL parses with `re` (no `BeautifulSoup4` —
  red-line §13); deeply nested templates need to use the documented
  `{{name:type=default}}` form.
- All cost figures are **estimates**. Actual DashScope billing is the
  source of truth.

## 10. Contributing

- Read [`docs/media-post-plan.md`](../../docs/media-post-plan.md) end-to-end
  before touching anything in this directory.
- Every change must keep all 231 hermetic unit tests green:
  `py -3.11 -m pytest plugins/media-post/tests -q`.
- Every commit must be English, Conventional, with the trailer
  `Refs: docs/media-post-plan.md §<n>`.
- No new Python or npm dependencies. Vendor what you need under
  `mediapost_inline/`.
