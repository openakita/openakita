---
name: image-edit
description: 用一句话改一张图。支持框选要改的局部（mask）。基于 gpt-image-1，可降级到通义万相，也支持纯本地 stub 模式（dev/demo）。
env_any: [OPENAI_API_KEY, DASHSCOPE_API_KEY]
---

# Image Edit

## 是什么 / What

让用户在浏览器里上传一张图、用画笔涂出"要改的部分"、然后用一句话描述想要的效果。

## 何时用 / When

- 用户想去掉/替换/添加图片里的局部元素
- 用户想换背景、换风格、修瑕疵
- **不要用于**: 从无到有生成全新图片（那是 `tongyi-image` 的活）；只想加文字（用 `poster-maker`）

## 工具 / Tools

- `image_edit_create(source_path, prompt, mask_path?)` — 创建任务
- `image_edit_status(task_id)` — 查询
- `image_edit_list()` — 列出最近任务
- `image_edit_cancel(task_id)` — 取消

## 流程 / Pipeline

```
upload → 涂 mask（可选）→ 意图复核 → 成本预览 → providers.select_provider() → 落地输出
```

## Quality Gates

| Gate | 检查内容                      | 通过条件                                         |
|------|-------------------------------|--------------------------------------------------|
| G1   | source_path 存在 + prompt 非空 | `Path(source_path).is_file() and prompt.strip()` |
| G2   | result 含 output_paths 非空    | `len(result["output_paths"]) > 0`               |
| G3   | 错误用 ErrorCoach 渲染         | `RenderedError.pattern_id != "_fallback"`        |

## 已知坑 / Known Pitfalls

- **OpenAI 只支持 PNG**：上传 JPG 会被服务端拒绝；前端 canvas 已统一转 PNG。
- **DashScope 需要公网 URL**：本地图片必须先上传 OSS；当前实现在 `DashScopeWanxProvider.edit()` 里直接 raise，让上层回退到 OpenAI。
- **mask 的颜色约定**：白色 = 改的区域，黑色 = 保留区域。前端 canvas 已做转换。
- **n > 1 时计费成倍**：成本预览已经把 n 算进去。

## 用户决策点 / Checkpoints

1. 上传后展示原图，让用户决定是否涂 mask
2. 意图复核必须显示，等待用户点【开始改图】
3. 如果 OpenAI key 缺失而用户选了 `auto`，自动降级到 `dashscope`，再降级到 `stub`，每次降级都在 UI 提示
