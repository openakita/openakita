---
name: highlight-cutter
description: 从一段长视频里挑出 3~10 段精彩瞬间并自动剪好。纯本地处理，无 API 调用费用。适合不会用剪辑软件的小白。
env_any: []
---

# Highlight Cutter

## 是什么 / What

把一段几分钟到一小时的视频，自动挑几段「值得看」的片段，剪成一个短片输出到本地。

## 何时用 / When

- 用户上传了一段较长的视频，想要"高光集锦"或"精彩片段"
- 用户说"帮我挑几段"、"剪个短的"、"做个 highlight"
- **不要用于**: 视频还没拍 / 用户要的是创作而不是裁剪 / 用户只想要字幕（用 `subtitle-maker`）

## 工具 / Tools

- `highlight_cutter_create(source_path, target_count?)` — 创建任务
- `highlight_cutter_status(task_id)` — 查询任务状态
- `highlight_cutter_list()` — 列出最近任务
- `highlight_cutter_cancel(task_id)` — 取消运行中任务

## 流程 / Pipeline

```
upload → 意图复核 (verify) → 成本预览 → ASR 转写 → 关键句打分 → 三分自检均匀采样 → ffmpeg 渲染
```

## Quality Gates

| Gate | 检查内容                              | 通过条件                                            |
|------|---------------------------------------|-----------------------------------------------------|
| G1   | source_path 存在且非空                | `Path(source_path).is_file()` 为真                  |
| G2   | 输出 result 含 output_path + segments | `{"output_path", "segments"} ⊆ result.keys()`       |
| G3   | 错误用 ErrorCoach 渲染（3 段式）       | `RenderedError.pattern_id != "_fallback"`           |

## 已知坑 / Known Pitfalls

- **whisper-cli 未装**：插件会自动 fallback 到等长切分。提示用户安装可显著提升质量。
- **ffmpeg 缺失**：`ErrorCoach` 会输出 `ffmpeg_missing` 模式 — 给出 winget 一键命令。
- **超长视频 (> 60 min)**：转写会很慢；可在【设置】里改 ASR 模型为 `tiny` 或 `small`。

## 用户决策点 / Checkpoints

1. 上传后展示视频时长 + 预估剪辑时间，让用户调整参数
2. 意图复核（IntentVerifier）显示后等待用户点【开剪】
3. 成功后 confetti + 推荐 `subtitle-maker` / `avatar-speaker`
