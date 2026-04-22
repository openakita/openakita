---
name: avatar-speaker
description: 文字转配音 (TTS)，支持中文/英文多种声线，零依赖即用 (Edge TTS 免费)。预留数字人形象骨架，未来可一键合成「会说话的头像」。
env_any: [DASHSCOPE_API_KEY, OPENAI_API_KEY]
---

# Avatar Speaker

## 是什么 / What

把一段文字变成自然好听的配音 (mp3)；可选的"数字人"模块在 P3 backlog 实现。

## 何时用 / When

- 用户要给视频配旁白
- 用户要把一段文章读出来
- 用户要做教学/有声书
- **不要用于**: 已经有现成音频要剪辑（用 `highlight-cutter`）；要配字幕（用 `subtitle-maker`）

## 工具 / Tools

- `avatar_speaker_synthesize(text, voice?, rate?, pitch?)` — 创建任务
- `avatar_speaker_status(task_id)` / `avatar_speaker_list()` / `avatar_speaker_cancel(task_id)`

## 引擎选择 / Provider Matrix

| Provider           | 是否需 Key | 价格         | 中文质量 | 备注                       |
|--------------------|-----------|--------------|----------|----------------------------|
| `edge` (Edge TTS)  | 否        | 免费         | ★★★★    | 默认首选；30+ 中文声音      |
| `dashscope-cosyvoice` | DASHSCOPE_API_KEY | ¥0.05/千字 | ★★★★★   | 支持声音克隆                |
| `openai-tts`       | OPENAI_API_KEY | $0.015/千字 | ★★★      | 中文略硬                    |
| `stub-silent`      | 否        | 免费         | -        | 无音频，dev/demo 使用       |

## Quality Gates

| Gate | 检查内容                  | 通过条件                                    |
|------|---------------------------|---------------------------------------------|
| G1   | text 非空                 | `text.strip()`                              |
| G2   | result 含 audio_path 文件  | `Path(result["audio_path"]).is_file()`     |
| G3   | 错误用 ErrorCoach 渲染    | `RenderedError.pattern_id != "_fallback"`   |

## 已知坑 / Known Pitfalls

- **edge-tts 偶尔被微软风控**：自动 fallback 到 dashscope/openai/stub
- **CosyVoice 需要公网**：DashScope 不能离线
- **数字人尚未实现**：P3 backlog；目前 `avatar_provider != "none"` 会得到 stub 文本
