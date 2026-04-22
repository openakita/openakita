# AI 视频翻译 / Video Translator

上传视频 → 自动识别 → 翻译 → 配音 → 输出新视频。

- 复用 `highlight-cutter` 的 ASR、`subtitle-maker` 的 SRT 写入、`tts-studio` 的 TTS provider
- 翻译走 host LLM brain（`think_lightweight`），失败自动降级
- 输出双语：保留原音少量音量 + 新配音 + 软字幕

## 前置条件

- FFmpeg 在 PATH（**必需**）
- whisper-cli (whisper.cpp) 在 PATH（**必需**，用于 ASR）

## 测试

```bash
pytest plugins/video-translator/tests
```
