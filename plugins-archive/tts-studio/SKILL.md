---
name: tts-studio
description: 把多角色对话稿一次性转成多条配音，自动拼接成完整音频。复用 avatar-speaker 的 TTS 引擎。
env_any: [DASHSCOPE_API_KEY, OPENAI_API_KEY]
---

# TTS Studio

## 是什么 / What

写一段类似剧本的对话稿（`A: 你好`、`B: 再见` 这种格式），自动给每个角色配不同声音并合成成一条完整音频。

## 何时用 / When

- 用户要做播客双人对话
- 用户要做有声小说 / 角色扮演内容
- 用户要给故事配旁白 + 角色台词
- **不要用于**: 单段配音（用 `avatar-speaker`）；翻译已有音频（用 `video-translator`）

## 工具 / Tools

- `tts_studio_create(script, title?, default_voice?, voice_map?)`
- `tts_studio_status(task_id)` / `tts_studio_list()` / `tts_studio_cancel(task_id)`

## 解析格式

```
旁白: 在一个夏天的午后...
A: 你怎么还在加班？
B: 没办法，明天就要交了。
旁白: A 摇了摇头，转身离去。
```

每行 `Speaker: text` 解析为一段；连续的不带 speaker 的行会附加到上一段。

## Quality Gates

| Gate | 检查内容          | 通过条件                                                |
|------|-------------------|---------------------------------------------------------|
| G1   | script 非空       | `script.strip()`                                        |
| G2   | merged_audio 存在 | `Path(result["merged_audio_path"]).is_file()`           |
| G3   | 错误用 ErrorCoach | `RenderedError.pattern_id != "_fallback"`               |

## 已知坑 / Known Pitfalls

- **没装 ffmpeg → 退化为「只取第一段」**：缺 ffmpeg 时不会失败，但只有第一段音频
- **声音切换可能有跳频**：默认 mp3 直接 concat（c copy），不同 provider 的采样率可能差，建议同 provider
- **复用 avatar-speaker = 共享 bug**：avatar-speaker provider 改了之后这个插件自动受益
