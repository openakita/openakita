---
name: storyboard
description: 把一段文字脚本拆成分镜表（每镜头有画面/镜头/时长/台词），AI 自动平衡时长分布，可一键导出 CSV 喂给摄影或 AI 生图/生视频。
env_any: []
---

# Storyboard

## 是什么 / What

输入一段脚本/想法 + 目标时长，输出一份 **JSON 分镜表**：每镜头有 visual / camera / duration / dialogue / sound / notes。

## 何时用 / When

- 用户描述了一个故事或场景，但不知道怎么拆镜头
- 用户已经写好脚本，想把它转成可执行的分镜表
- 用户要喂给 `tongyi-image` / `seedance-video` 一组连续镜头
- **不要用于**: 视频已经拍好（用 `highlight-cutter`）；只要文案不要分镜（用 `tts-studio`）

## 工具 / Tools

- `storyboard_create(script, target_duration_sec=30, title?, style_hint?)`
- `storyboard_status(task_id)` / `storyboard_list()` / `storyboard_cancel(task_id)`

## 流程 / Pipeline

```
script → think_lightweight(SYSTEM_PROMPT) → 5-level fallback parse → 三分自检 → 输出 + CSV 导出
```

## 5 级解析降级 / 5-level Fallback Parser

| Level | 形式                       | 实现                                         |
|-------|----------------------------|----------------------------------------------|
| 1     | 直接 JSON                  | `json.loads(text)`                          |
| 2     | ` ```json … ``` ` 包裹     | regex 抓 fenced block                        |
| 3     | 文本里夹带 `{...}`         | regex 第一段 `{.*}`                         |
| 4     | 编号列表 `1. … 2. …`       | 行解析 → Shot                                |
| 5     | 全部失败                   | stub 单镜头 + 提示 "请重写脚本"               |

## 三分自检 / Three-Thirds Self-Check

| Gate                | 检查内容                                            | 通过条件                       |
|---------------------|-----------------------------------------------------|--------------------------------|
| duration_match      | sum(duration_sec) ≈ target ±10%                     | True                           |
| distribution_balance| 三段时长里没有任何一段占 > 60%                       | True                           |
| minimum_count       | shots 数 ≥ ceil(target / 6)                         | True                           |

不通过会在结果里给出 `suggestions[]`，UI 用浅黄 chip 显示。

## Quality Gates (G)

| Gate | 检查内容                                  | 通过条件                                          |
|------|-------------------------------------------|---------------------------------------------------|
| G1   | script 非空                               | `script.strip()`                                  |
| G2   | result 含 storyboard.shots[]              | `len(result["storyboard"]["shots"]) > 0`         |
| G3   | 错误用 ErrorCoach 渲染                    | `RenderedError.pattern_id != "_fallback"`         |

## 已知坑 / Known Pitfalls

- **LLM 偏好聚集前段**：通过 `_SYSTEM` 显式提示「均匀分布」+ 三分自检兜底
- **没配 LLM 大脑**：自动退化为均匀按 60 字一段切，不会卡住流程
- **过长脚本**：不主动截断，但会在 self-check 里报 minimum_count 超额

## 桥接 / Bridges (Sprint 1 + Sprint 2)

| 路由                                              | 目标插件 / 工具      | 用法                                            |
|--------------------------------------------------|----------------------|-------------------------------------------------|
| `GET /tasks/{id}/export.csv`                     | 摄影 / 拍摄团队      | 直接给摄影看的 CSV，时长 + 画面 + 镜头         |
| `GET /tasks/{id}/export-seedance.json`           | `seedance-video` CLI | shots 数组 + cli_examples，喂 `scripts/seedance.py` |
| `GET /tasks/{id}/export-tongyi.json`             | `tongyi-image` 插件  | post_examples 可直接 POST `/api/plugins/tongyi-image/tasks` |

`export-tongyi.json` 把每个分镜转成一条 `tongyi-image` 任务请求体（`mode=text2img`，
`prompt = visual + 构图 + 风格`，省去音效/台词），`size` 默认 `1024*1024`（DashScope 用 `*`），
`n` 自动 clamp 到 `1..4`。内置 `curl_examples` 已做单引号转义，可直接 paste 到 bash。
