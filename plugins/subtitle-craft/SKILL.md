# Subtitle Craft Skill Definition

## 1. Trigger Scenarios

Use this skill when the user wants to:

- 自动给视频/音频生成字幕(听写)
- 把已有 SRT 翻译为另一种语言(单语 or 双语字幕)
- 修复时间轴对齐 / 断行重排 / 重叠问题
- 把字幕烧入视频(软字幕 sidecar 或硬烧 in-stream)
- 区分多说话人 / 把 SPEAKER_xx 自动映射成角色名
- 估算「这段视频做字幕大概要花多少 ¥」

Keywords (zh): 字幕, 听写, 转录, 翻译字幕, 字幕修复, 字幕烧制, 双语字幕, 角色识别, 说话人分离
Keywords (en): subtitle, caption, transcribe, translate srt, burn subtitle, diarization, speaker

## 2. Command Reference (4 tools)

| Tool | Purpose |
|------|---------|
| `subtitle_craft_create` | Create a subtitle task (mode = auto_subtitle / translate / repair / burn) |
| `subtitle_craft_status` | Inspect a single task's status, pipeline step, error_kind |
| `subtitle_craft_list`   | List recent tasks (default 10) |
| `subtitle_craft_cancel` | Cooperative cancel of a running task |

> v1.0 explicitly does **NOT** declare any `subtitle_craft_handoff_*` tool —
> cross-plugin dispatch is deferred to v2.0 with schema reservation only.

## 3. Input Schema

### `subtitle_craft_create`
```json
{
  "mode": "auto_subtitle | translate | repair | burn",
  "source_path": "/abs/path/to/video.mp4",      // for auto_subtitle / burn
  "srt_path":    "/abs/path/to/input.srt",      // for translate / repair / burn
  "source_lang": "",                             // empty = auto-detect (Paraformer language_hints)
  "target_lang": "en",                           // for translate
  "translation_model": "qwen-mt-flash",          // qwen-mt-flash | qwen-mt-plus | qwen-mt-lite
  "diarization_enabled": false,                  // auto_subtitle only
  "speaker_count": 0,                            // 0 = auto
  "character_identify_enabled": false,           // requires diarization_enabled=true
  "disfluency_removal_enabled": false,           // remove um/uh/嗯/啊
  "bilingual": false,                            // translate only — keep both lines
  "subtitle_style": "default",                   // default | cinema | youtube | tiktok | tv | <custom_id>
  "burn_engine": "ass",                          // ass | html
  "burn_mode": "hard"                            // hard (in-stream) | soft (mp4 sidecar)
}
```

### `subtitle_craft_status`
```json
{ "task_id": "abc123def456" }
```

### `subtitle_craft_cancel`
```json
{ "task_id": "abc123def456" }
```

## 4. Output Schema

### Task envelope (returned by create / get / list elements)
```json
{
  "id": "abc123def456",
  "mode": "auto_subtitle",
  "status": "pending|running|succeeded|failed|canceled",
  "pipeline_step": "setup_environment|estimate_cost|prepare_assets|asr_or_load|identify_characters|translate_or_repair|render_output|burn_or_finalize",
  "progress": 0.42,
  "source_path": "...",
  "output_srt_path": "...",
  "output_vtt_path": "...",
  "output_video_path": "...",
  "error_kind": "network|timeout|auth|quota|moderation|dependency|format|duration|unknown",
  "error_message": "...",
  "error_hints": "..."
}
```

## 5. Mode Selection Heuristics (when the user is vague)

| User says | Choose mode | Notes |
|---|---|---|
| "给这段视频加字幕" / "transcribe video" | `auto_subtitle` | If the user mentions multiple people, also turn on `diarization_enabled` |
| "把字幕翻译成英文" / "translate srt to english" | `translate` | Default `translation_model=qwen-mt-flash` (best value) |
| "字幕重叠了" / "时间轴乱" / "fix overlap" | `repair` | No API cost — pure local rewrite |
| "烧制" / "硬字幕" / "burn subtitle" | `burn` | Default `burn_engine=ass` (FFmpeg native) — switch to `html` only when the user wants custom CSS |
| 提到 "区分谁在说话" / "speaker" | `auto_subtitle` + `diarization_enabled=true` | Add `character_identify_enabled=true` if the user wants character names instead of SPEAKER_xx |

## 6. Cost Hints (for setting expectations before invoking)

- Paraformer-v2: ¥0.00027 / second of audio (~¥1 / hour)
- Qwen-MT Flash:  ¥0.000007 / token (~¥0.005 / 1k tokens)
- Qwen-MT Plus:   ¥0.000016 / token
- Qwen-MT Lite:   ¥0.000003 / token
- Qwen-VL Max (character ID): one call per task, ¥0.020 typical
- Local burn (FFmpeg/Playwright): free

A 30-min YouTube → English subtitles round-trip is usually ¥0.50–¥0.80.

## 7. Error Handling Contract (9 canonical kinds)

The `error_kind` field always falls into one of these 9 values, identical
to `clip-sense`'s taxonomy:

| `error_kind` | What to tell the user |
|---|---|
| `network`    | Suggest checking VPN / DNS, then retry |
| `timeout`    | Increase Paraformer timeout in Settings, then retry |
| `auth`       | API key wrong / 4xx → open Settings |
| `quota`      | Bailian balance / quota exhausted |
| `moderation` | Content flagged → ask the user to trim sensitive parts |
| `dependency` | FFmpeg / Playwright missing — HTML burn auto-falls back to ASS |
| `format`     | Bad SRT encoding (must be UTF-8) / unsupported video container |
| `duration`   | File >2 GB or audio >12 h — ask user to split first |
| `unknown`    | Surface the raw `error_message` and link to logs |

## 8. SSE Live Updates

The plugin emits a single event name `task_update` with payload:

```json
{
  "task_id": "...",
  "status":  "...",
  "mode":    "...",
  "pipeline_step": "...",
  "progress": 0.42,
  "error_kind": "...",
  "error_message": "...",
  "error_hints": "..."
}
```

Subscribe via `onEvent("task_update", handler)` — the plugin host fans
this out to the iframe via `bridge:event`.

## 9. v1.0 Scope Limit (read carefully before suggesting v2 features)

v1.0 ships **without**:

- `/handoff/*` routes  → no cross-plugin dispatch surface
- `subtitle_craft_handoff_*` tools  → only the 4 above
- "Send to clip-sense" / "送往 …" UI buttons  → no cross-plugin CTA
- `assets_bus.write` / `tasks.origin_*` fills → schema is reserved but
  always NULL in v1.0

When the user asks "can it pipe into clip-sense?" answer with: "v1.0
exports SRT/VTT files; v2.0 will add a one-click Handoff. For now you
can manually re-upload the SRT into clip-sense's `burn_subtitle` mode."

## 10. Quick Reference Recipe

```json
// Pure transcription, Chinese, no diarization
{ "mode": "auto_subtitle", "source_path": "/x.mp4", "source_lang": "zh" }

// Transcription + diarization + character ID (the only place the toggle
// is active — embedded inside auto_subtitle, not a standalone mode)
{ "mode": "auto_subtitle", "source_path": "/x.mp4",
  "diarization_enabled": true, "character_identify_enabled": true,
  "speaker_count": 3 }

// Translate an existing SRT to English, bilingual output
{ "mode": "translate", "srt_path": "/x.srt", "target_lang": "en",
  "bilingual": true, "translation_model": "qwen-mt-flash" }

// Repair a glitchy SRT (no API cost)
{ "mode": "repair", "srt_path": "/x.srt" }

// Burn an existing SRT into video using ffmpeg ASS (recommended)
{ "mode": "burn", "source_path": "/x.mp4", "srt_path": "/x.srt",
  "burn_engine": "ass", "burn_mode": "hard", "subtitle_style": "youtube" }

// AI Hook Picker (v1.1) — Qwen-Plus selects an opening hook
{ "mode": "hook_picker", "srt_path": "/x.srt",
  "instruction": "find the strongest opening line",
  "target_duration_sec": 12, "hook_model": "qwen-plus" }
```

## 11. AI Hook Picker (mode `hook_picker`, added in v1.1)

A 5th processing mode that runs an existing SRT through Qwen-Plus to
pick the strongest opening "hook" dialogue for short-form video.  The
algorithm is ported 1:1 from CutClaw's
`Screenwriter_scene_short.py` and lives in the dedicated
`subtitle_hook_picker.py` module — the prompt (`SELECT_HOOK_DIALOGUE_PROMPT`),
the fuzzy-match threshold (0.55) and the 3-window-with-2-retries loop
are red lines: do NOT change them without re-validating against
CutClaw output.

### Inputs (extends `CreateTaskBody`)

| field | type | default | meaning |
|---|---|---|---|
| `srt_path` | str | "" | Required when `from_task_id` is empty |
| `from_task_id` | str | "" | Reuse another task's `output_srt_path` |
| `instruction` | str | "" | Free-form direction for the AI |
| `main_character` | str | "" | Constrain selection to this speaker |
| `target_duration_sec` | float | 12.0 | Hook length target (6–30) |
| `prompt_window_mode` | str | `"tail_then_head"` | or `"random_window"` |
| `random_window_attempts` | int | 3 | 1–5; cost scales linearly |
| `hook_model` | str | `"qwen-plus"` | Or `qwen-plus-2025-09-11` / `qwen-max` |

### Outputs

- `task_dir/hook.srt` — single-cue SRT for the chosen window.
- `task_dir/hook.json` — `{ hook: {...}, telemetry: {...} }` payload
  (lines, timed_lines, source_start/_end, duration_seconds,
  selected_window, selected_attempt, reason).
- `GET /tasks/{task_id}` enriches succeeded hook tasks with `hook` +
  `hook_telemetry` so the UI right-pane `HookResultPanel` renders
  without an extra fetch.
- `GET /library/hooks` aggregates every succeeded hook task
  (`{items: [...], total: N}`).

### Skipped pipeline steps

`hook_picker` declares `skip_steps = ("prepare_assets",
"identify_characters", "translate_or_repair", "burn_or_finalize")`.
ASR is NOT skipped; instead `_step_asr_or_load` short-circuits to
`_load_srt_input` (the SRT must contain ≥5 cues — fewer raises
`PipelineError(kind="format")`).

### Cost guard

Server-side `estimate_cost` bills `qwen-plus` (¥0.005/round) ×
`(2 + random_window_attempts)` rounds; a typical run costs < ¥0.01.
The UI surfaces this estimate in the right-pane `oa-preview-card`
before the user clicks Start.

