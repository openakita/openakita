# ClipSense 智剪工坊

AI-powered video editing plugin for OpenAkita — upload a long video, AI transcribes and understands it, then produces polished short clips via 4 editing modes.

## 1. Overview

ClipSense combines cloud AI intelligence (DashScope Paraformer for ASR, Qwen for content analysis) with local FFmpeg execution to deliver intelligent video editing directly within OpenAkita.

**4 Editing Modes:**
| Mode | Description | Requires API Key |
|------|-------------|-----------------|
| Highlight Extract | AI identifies exciting segments and auto-clips a highlight reel | Yes |
| Silence Clean | Detect and remove silent/blank segments for tighter cuts | No (local only) |
| Topic Split | AI splits long video into topic-based short clips (multi-file output) | Yes |
| Talking Polish | AI removes filler words, stutters, and repetitions | Yes |

## 2. Installation

ClipSense is a built-in OpenAkita plugin. It requires:

- **OpenAkita** >= 1.27.0 with SDK >= 0.7.0
- **FFmpeg** >= 4.0 installed and accessible via PATH
- **DashScope API Key** (for modes other than Silence Clean)

## 3. Configuration

1. Open ClipSense from the sidebar (智剪工坊)
2. Go to **Settings** tab
3. Enter your DashScope API Key
4. Verify FFmpeg is detected (green indicator)
5. Set default output format (MP4 or MKV)

## 4. Usage

### Quick Start
1. Select an editing mode in the **Create** tab
2. Upload a video (MP4, MOV, MKV) or pick one from the **Library** tab
3. Adjust mode-specific parameters
4. Click **Start Editing**
5. Monitor progress in the preview panel or **Tasks** tab

### Modes in Detail

**Highlight Extract:**
- Set "flavor" preference (funny/controversial/informative)
- Choose target clip count (3-10) and duration (15-90s)
- AI ensures highlights are distributed across early/middle/late sections
- Output SRT subtitles are timeline-adjusted to match the concatenated highlight reel

**Silence Clean:**
- Choose a preset (Conservative/Standard/Aggressive) or fine-tune sliders
- Threshold is relative to peak volume (not absolute dB)
- Runs entirely locally — no API key needed, no cost
- If no silence is detected, a copy of the original video is placed in the output directory with a UI warning suggesting parameter adjustment

**Topic Split:**
- Choose target segment duration (1-3 min / 3-5 min / Auto)
- Each topic segment gets its own video file
- UI provides ◀ ▶ navigation to preview individual segments
- Download delivers a ZIP archive containing all segments

**Talking Polish:**
- Toggle removal of filler words, stutters, and repetitions
- Combines AI-detected segments with silence detection for thorough cleanup
- Subtitle burning is disabled for this mode (timeline compression makes SRT sync impractical)

### Material Library
Previously transcribed videos appear in the **Library** tab and can be reused for new editing tasks without re-uploading or re-transcribing.

### Storage Management
The **Settings** tab provides:
- **Clear upload cache** — removes uploaded source files
- **Clear output files** — removes generated output; associated task records have their output links cleared automatically
- **Task deletion** — removes both the database record and the task's disk directory

## 5. Architecture

```
plugins/clip-sense/
├── plugin.json              # Manifest
├── plugin.py                # Entry point (18 routes, 5 tools)
├── clip_models.py           # Mode definitions, presets, pricing
├── clip_task_manager.py     # SQLite (tasks + transcripts + config)
├── clip_asr_client.py       # DashScope Paraformer + Qwen
├── clip_dashscope_upload.py # Temp OSS upload for local files (Paraformer file_urls)
├── clip_pipeline.py         # 7-step pipeline engine
├── clip_ffmpeg_ops.py       # FFmpeg operations wrapper
├── clip_sense_inline/       # Vendored SDK helpers
├── tests/                   # Unit + integration tests (140 cases)
└── ui/dist/index.html       # React UI (4 tabs: Create, Tasks, Library, Settings)
```

### Pipeline Steps
```
setup → check_deps → transcribe → analyze → execute → subtitle → finalize
```
Each mode skips irrelevant steps (e.g. Silence Clean skips transcribe + analyze).

### Key Design Decisions
- **Relative silence threshold**: `thr = max_db + threshold_db` adapts to each video's dynamic range
- **Cross-drive safety**: Uses `shutil.move` instead of `Path.rename` for Windows compatibility
- **Output format**: Configurable per-task or via global default; pipeline respects the `output_format` param throughout
- **Topic split storage**: Individual MP4s + ZIP archive; `ctx.params._topic_files` and `_topics_zip` persisted to DB

## 6. Troubleshooting

| Error | Solution |
|-------|----------|
| "FFmpeg not found" | Install ffmpeg and add to PATH, or set path in Settings |
| "API Key not configured" | Enter DashScope API Key in Settings |
| "Video duration exceeds limit" | Maximum 120 minutes; trim the video first |
| "Content moderation" | Video content was flagged; try different material |
| Transcription timeout | Long videos may take 15+ minutes; check Tasks tab |
| "No silence detected" warning | Lower `threshold_db` (e.g. -30) or reduce `min_silence_sec` (e.g. 0.3s) |
| "Paraformer task … FAILED" after **local / LAN upload** | Paraformer needs a URL DashScope can fetch. Local paths and `http://127.0.0.1/...` are not reachable from the cloud. ClipSense uploads your file to **DashScope temporary OSS** first (same API key); ensure the key is valid, outbound HTTPS is allowed, and the file is within size/time limits. If it still fails, copy the **task_id** from the error message for support. |
| "DashScope temp upload failed" | Check API key, region/network, and file size; retry when the service returns 429/5xx (treated as retryable). |
| `SUCCESS_WITH_NO_VALID_FRAGMENT` / **无有效语音** | The file reached DashScope but **no usable speech** was detected (music-only, ambience, very quiet audio, or B-roll without dialogue). Highlight / topic / talking modes need clear human speech — try another clip or re-export with a normalised AAC audio track. |

## 7. Smoke Test

Quick 5-step verification:

```bash
# 1. Check FFmpeg
ffmpeg -version

# 2. Run unit tests
cd plugins/clip-sense
python -m pytest tests/ -q -m "not integration"

# 3. Open UI in browser (via OpenAkita)
# Navigate to ClipSense → Settings → verify FFmpeg green

# 4. Test Silence Clean (no API key needed)
# Upload a short video → Silence Clean → Start

# 5. Test Highlight Extract (needs API key)
# Enter API key → Create → Highlight Extract → Upload → Start
```

## 8. Cost Estimation

| API | Rate | 30-min Video |
|-----|------|-------------|
| Paraformer-v2 (ASR) | ¥0.0008/sec | ¥1.44 |
| Qwen-Plus (analysis) | ¥0.004/K tokens | ~¥0.05 |
| FFmpeg (local) | Free | Free |
| **Silence Clean total** | | **¥0** |
| **Full pipeline total** | | **~¥1.5** |
