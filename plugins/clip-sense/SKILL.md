# ClipSense Skill Definition

## 1. Trigger Scenarios

Use this skill when the user wants to:
- Edit, trim, or cut a video
- Extract highlights from a long video
- Remove silence from a video
- Split a video by topics
- Clean up talking-head / podcast content
- Generate subtitles from a video

Keywords: 剪辑, 高光, 静音, 拆条, 口播, 字幕, video edit, clip, trim, silence, highlight

## 2. Command Reference

| Tool | Purpose |
|------|---------|
| `clip_sense_create` | Create an editing task |
| `clip_sense_status` | Check task status |
| `clip_sense_list` | List recent tasks |
| `clip_sense_transcribe` | Transcribe a video |
| `clip_sense_cancel` | Cancel a running task |

## 3. Input Schema

### clip_sense_create
```json
{
  "mode": "highlight_extract|silence_clean|topic_split|talking_polish",
  "source_video_path": "/path/to/video.mp4",
  "flavor": "optional: funny/controversial/informative",
  "target_count": 5,
  "target_duration": 30,
  "threshold_db": -40,
  "min_silence_sec": 0.5,
  "padding_sec": 0.1,
  "burn_subtitle": false
}
```

## 4. Output Schema

### Task Response
```json
{
  "id": "abc123def456",
  "status": "pending|running|succeeded|failed|cancelled",
  "mode": "silence_clean",
  "pipeline_step": "setup|check_deps|transcribe|analyze|execute|subtitle|finalize",
  "output_path": "/path/to/output.mp4",
  "subtitle_path": "/path/to/subtitle.srt",
  "error_kind": "network|timeout|auth|...",
  "error_message": "...",
  "error_hints": ["hint1", "hint2"]
}
```

## 5. Error Codes

| Kind | Meaning | User Action |
|------|---------|-------------|
| `network` | Connection failed | Check network/proxy |
| `timeout` | Task timed out (>15min) | Refresh, may still be running |
| `auth` | Invalid API key | Reconfigure in Settings |
| `quota` | Insufficient balance | Top up at Alibaba Cloud |
| `moderation` | Content flagged | Use different video |
| `dependency` | FFmpeg missing | Install ffmpeg >= 4.0 |
| `format` | Invalid video format | Use MP4/MOV/MKV |
| `duration` | Video too long (>120min) | Trim before upload |
| `unknown` | Unexpected error | Report task_id |

## 6. Mode Decision Tree

```
User wants to edit video →
  ├── "Remove silence/pauses" → silence_clean
  ├── "Get best parts/highlights" → highlight_extract
  ├── "Split into chapters/topics" → topic_split
  ├── "Clean up talking/podcast" → talking_polish
  └── Not sure → Ask about the goal, default to highlight_extract
```

## 7. Cost Estimation

- `silence_clean`: ¥0 (pure local FFmpeg)
- Others: ~¥0.05/min (ASR) + ~¥0.002/min (Qwen) ≈ ¥1.5 for 30-min video

## 8. Common Templates

### Extract 5 highlights from a podcast
```
clip_sense_create mode=highlight_extract source_video_path=/uploads/podcast.mp4 target_count=5 flavor=informative
```

### Quick silence removal
```
clip_sense_create mode=silence_clean source_video_path=/uploads/talk.mp4 silence_preset=standard
```

### Split lecture into chapters
```
clip_sense_create mode=topic_split source_video_path=/uploads/lecture.mp4 target_segment_duration=180
```

## 9. Testing

```bash
# Unit tests (no network)
python -m pytest tests/ -q -m "not integration"

# Integration test (needs DASHSCOPE_API_KEY + ffmpeg)
DASHSCOPE_API_KEY=sk-... python -m pytest tests/integration/ -m integration
```

## 10. Known Limitations

- Paraformer provides sentence-level timestamps (not word-level); cut boundaries have ~0.5-2s precision
- Pure-Python silence detection is slower than numpy-based for files >30min
- No auto-installation of FFmpeg; user must install manually
- Maximum video duration: 120 minutes
- Transcript text fed to Qwen is truncated at 20,000 characters
- Topic split outputs multiple files; only the first is shown in preview
