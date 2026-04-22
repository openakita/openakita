# 精彩瞬间剪辑 / Highlight Cutter

把一段长视频自动剪成 3~10 段精彩瞬间。**纯本地运行，不花钱**。

## 给小白用户

打开插件 → 拖一个视频进来 → 点【开剪】→ 等几秒 → 完事。

## 技术要点

- 上游：`whisper-cli` (whisper.cpp) 做语音转文字
- 评分：基于关键词密度 + 句子完整度的可解释打分（不依赖 LLM）
- 采样：六分之一时段均匀采样（防止 LLM 偏好聚集前段）
- 渲染：`openakita_plugin_sdk.contrib.RenderPipeline` + ffmpeg concat demuxer

## 前置依赖（可选但强烈推荐）

```bash
# Windows
winget install Gyan.FFmpeg
# 装 whisper.cpp（也有 Windows 预编译包）
# https://github.com/ggerganov/whisper.cpp/releases
```

不装也能用 — 没有转写时会等长切分，效果一般。

## 测试

```bash
pytest plugins/highlight-cutter/tests
```

## 集成给别的 Agent / Plugin

见 `docs/HOWTO.md`。
