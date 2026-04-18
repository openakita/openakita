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

# 视频 AI 笔记

Supports进行视频解析、Generation AI 笔记的工具，可满足学习、会议等视频内容Extract、总结场景。

## Configuration

export BAIDU_API_KEY="your_key"

## Features

- 视频内容解析
- 关键信息Extract
- 结构化笔记Generation
- 时间戳标注
- 要点摘要

## Pre-built Scripts

### scripts/video_notes.py
视频解析笔记Generation（百度千帆 AppBuilder），需Set APPBUILDER_TOKEN。

```bash
python3 scripts/video_notes.py analyze "https://example.com/video.mp4"
python3 scripts/video_notes.py notes "视频内容总结"
```
