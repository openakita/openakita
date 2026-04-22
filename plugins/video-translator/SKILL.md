---
name: video-translator
description: 把视频整段翻译成另一种语言：ASR + LLM 翻译 + TTS + 合并配音字幕。
env_any: [DASHSCOPE_API_KEY, OPENAI_API_KEY]
---

# Video Translator

## 是什么 / What

上传一段视频，自动：识别语音 → 翻译字幕 → 用目标语言合成新配音 → 封装成新视频（带字幕，原音可保留少量）。

## 何时用 / When

- 用户做的视频要发到海外（中→英 / 中→日）
- 用户搬运国外内容回国（英→中）
- 用户做多语言版本宣传片
- **不要用于**: 只要字幕（用 `subtitle-maker`）；只要配音（用 `tts-studio`）

## 流水线

```
upload → extract audio → ASR (whisper.cpp) → translate (host LLM)
       → TTS (Edge / CosyVoice / OpenAI) → concat → mux back into video
```

## 工具 / Tools

- `video_translator_create(source_video_path, target_language, voice?, burn_subtitles?, keep_original_audio_volume?)`
- `video_translator_status(task_id)` / `video_translator_list()` / `video_translator_cancel(task_id)`

## 复用关系 (无重复实现)

| 模块 | 复用自 |
|------|--------|
| ASR (whisper.cpp) | `highlight-cutter.highlight_engine.whisper_cpp_transcribe` |
| SRT/VTT 写入      | `subtitle-maker.subtitle_engine.to_srt / to_vtt`           |
| TTS provider 选择 | `tts-studio.studio_engine.select_tts_provider`             |
| LLM 翻译          | host `api.get_brain().think_lightweight()`                  |
| FFmpeg 命令构建   | 本插件内部纯函数（`translator_engine.build_*_cmd`）         |

## Quality Gates

| Gate | 检查内容              | 通过条件                                                |
|------|-----------------------|---------------------------------------------------------|
| G1   | source_video 存在     | `Path(source_video_path).is_file()`                     |
| G2   | output_video 存在     | `Path(result["output_video_path"]).is_file()`           |
| G3   | 错误用 ErrorCoach     | `RenderedError.pattern_id != "_fallback"`               |

## 已知坑 / Known Pitfalls

- **没装 ffmpeg/whisper.cpp → 直接报错（不静默降级）**：影响整段流水线
- **LLM 翻译失败 → 自动 fallback 用 `[TR] 原文`**：不阻塞，但用户能看出来
- **TTS 时长 ≠ 原句时长**：当前简单 concat，时间对不齐；不做 forced-align
- **硬字幕 = 重新编码 = 慢**：默认软字幕（可关闭），mp4 用 mov_text
- **OpenAI TTS 没 zh 声**：选 zh 时建议用 Edge TTS 默认
