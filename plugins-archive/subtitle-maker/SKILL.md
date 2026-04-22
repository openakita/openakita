---
name: subtitle-maker
description: 上传视频/音频，自动生成 SRT/VTT 字幕，可选烧录回视频。复用 highlight-cutter 的 ASR 引擎；本地处理。
env_any: []
---

# Subtitle Maker

## 是什么 / What

把视频/音频里的语音转成时间码字幕文件 (SRT/VTT)，并可选烧录到视频里成为硬字幕。

## 何时用 / When

- 用户要给视频加字幕（剪映/Pr/抖音）
- 用户要给老视频补字幕
- 用户要把音频转文字稿
- **不要用于**: 翻译字幕（用 `video-translator`）；只挑精彩段（用 `highlight-cutter`）

## 工具 / Tools

- `subtitle_maker_create(source_path, language?, burn_into_video?)`
- `subtitle_maker_status(task_id)` / `subtitle_maker_list()` / `subtitle_maker_cancel(task_id)`

## Quality Gates

| Gate | 检查内容            | 通过条件                                       |
|------|---------------------|------------------------------------------------|
| G1   | source_path 存在    | `Path(source_path).is_file()`                  |
| G2   | result 含 srt_path   | `Path(result["srt_path"]).is_file()`          |
| G3   | 错误用 ErrorCoach   | `RenderedError.pattern_id != "_fallback"`      |

## 已知坑 / Known Pitfalls

- **whisper.cpp 必装**：本插件不再降级；缺失时会用 ErrorCoach 给出 winget/brew 命令
- **ffmpeg 必装(若选烧录)**：`burn_into_video=true` 时缺 ffmpeg 会触发 `ffmpeg_missing`
- **复用引擎 = 共享 bug**：highlight-cutter 的 ASR 改了之后这个插件自动受益（也包括 bug）
