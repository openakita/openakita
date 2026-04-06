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

支持进行视频解析、生成 AI 笔记的工具，可满足学习、会议等视频内容提取、总结场景。

## 配置

export BAIDU_API_KEY="your_key"

## 功能

- 视频内容解析
- 关键信息提取
- 结构化笔记生成
- 时间戳标注
- 要点摘要

## 预置脚本

### scripts/video_notes.py
视频解析笔记生成（百度千帆 AppBuilder），需设置 APPBUILDER_TOKEN。

```bash
python3 scripts/video_notes.py analyze "https://example.com/video.mp4"
python3 scripts/video_notes.py notes "视频内容总结"
```
