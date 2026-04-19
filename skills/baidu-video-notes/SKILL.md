---
name: openakita/skills@baidu-video-notes
description: "AI Video Notes skill for video content analysis and note generation. Extract key information from videos for learning, meetings, and content summarization. Use when user wants to analyze video content or generate notes from videos."
license: MIT
metadata:
  author: baidu
  version: "1.0.0"
requires:
  env: [BAIDU_API_KEY]
---

# Video AI Notes

A tool that supports video parsing and AI note generation, suitable for extracting and summarizing video content for learning, meetings, and other scenarios.

## Configuration

```bash
export BAIDU_API_KEY="your_key"
```

## Features

- Video content parsing
- Key information extraction
- Structured note generation
- Timestamp annotation
- Key point summaries

## Pre-built Scripts

### scripts/video_notes.py
Video parsing and note generation (Baidu Qianfan AppBuilder). Requires `APPBUILDER_TOKEN`.

```bash
python3 scripts/video_notes.py analyze "https://example.com/video.mp4"
python3 scripts/video_notes.py notes "Video content summary"
```
