---
name: seedance-video
description: 通过火山引擎 Ark API 生成 AI 视频 — 文生视频、图生视频、多模态、视频编辑、视频续写、长视频分镜拼接。任务异步执行+轮询，成功后可自动下载到本地。
env_any:
  - ARK_API_KEY
---

# Seedance 视频生成 / Seedance Video

## 是什么 / What

输入一句中文/英文描述（可选参考图/视频），输出一段 AI 生成的高清视频。
所有调用都是 Ark API 异步任务：先 `POST /contents/generations/tasks` 拿 `task_id`，
再轮询 `GET task_id`，就绪后下载视频 URL 并落盘到 `data_dir/videos/`。

## 何时用 / When

- 用户描述了一个视频创意，想直接生成短视频
- 用户有一张或多张图片，想变成视频（图生视频）
- 用户需要编辑已有视频（局部替换、特效叠加）
- 用户需要续写/延伸视频（向前、向后、链式）
- 用户需要多段视频按分镜脚本批量生成并拼接成长视频
- **不要用于**: 图片生成（用 `tongyi-image` skill）；纯文本/音频（用其他插件）

## 工具 / Tools

- `seedance_create({prompt, model?, mode?, ratio?, duration?, resolution?, ...})`
- `seedance_status({task_id})`
- `seedance_list({limit?})`

## 模式 / Modes

| mode       | 描述                              | 典型模型               |
| ---------- | --------------------------------- | ---------------------- |
| t2v        | 纯文本生视频                      | seedance-1-lite        |
| i2v        | 首帧图生视频                      | seedance-1-lite        |
| i2v_end    | 首尾帧图生视频                    | seedance-1-lite        |
| multimodal | 多图/视频/音频参考生成            | seedance-1-lite        |
| edit       | 视频编辑（替换/叠加）            | seedance-1-lite        |
| extend     | 视频续写（向前/向后/链式）       | seedance-1-lite        |

## 流程 / Pipeline

```
prompt + (可选 image/video/audio) → Ark POST → task_id →
  poll loop（默认 10s）→ status==completed →
    extract video URL → (可选 auto_download) → 落盘 + 广播 task_update
```

## 长视频 / Long Video Pipeline

```
故事脚本 → LLM 分镜拆解(decompose_storyboard) → N 段分镜 →
  串行模式: 上一段末帧 → 下一段首帧参考 (视觉连贯)
  并行模式: 各段独立生成 →
    ffmpeg concat → 最终长视频(含转场)
```

## Quality Gates (G1–G3)

| Gate | 检查内容                                   | 通过条件                                                        |
| ---- | ------------------------------------------ | --------------------------------------------------------------- |
| G1   | Ark API Key 已配置                         | `await tm.get_config("ark_api_key")` 非空                      |
| G2   | upload 路径在插件 data_dir 内（防遍历）   | `add_upload_preview_route` 已注册 + `path.relative_to(base)` OK |
| G3   | task 异常用日志 + UI broadcast 兜底        | `_broadcast_update(task_id, "failed")` + `error_message` 落库   |

## Trust Hooks（你怎么知道我没乱花钱 / 偷数据）

| 信任点              | 怎么自查                                                      |
| ------------------- | ------------------------------------------------------------- |
| 钱花在哪 / Cost     | Ark API 唯一调用方；本插件不引入第三方计费                    |
| 数据流向 / Data     | 上传文件先存 `data_dir/uploads/`，仅作为 base64 发给 Ark API  |
| 出错怎么办 / Errors | 全部异常落库 `error_message`，UI 通过 task_update 推送        |
| 远程依赖 / Network  | 仅 `https://ark.cn-beijing.volces.com/`；无遥测               |

## 已知坑 / Known Pitfalls

- **API Key 没填**: 所有请求 502 → UI 提示去 Settings 配置
- **Ark content moderation**: 敏感内容（含人脸）会触发安全审核直接 fail — 改 prompt 重试
- **视频 URL 过期**: 必须开 `auto_download=true`（默认开）才能离线复用
- **长时间任务排队**: 默认 poll 间隔 10s，繁忙时段可能等几分钟 — 不会卡 UI
- **串行长视频人脸限制**: Seedance 不接受含人脸的参考图，串行链接会回退到纯文本生成，连续性下降 — 优先使用动画角色/动物/风景
- **ffmpeg 依赖**: 长视频拼接需要系统安装 ffmpeg — 缺失时 UI 会提示

## 安全升级 / Hardening Notes

- `ArkClient` 继承 `BaseVendorClient`，自动获得 retry/timeout/VendorError 分类
- `on_unload` 清理后台任务和资源，避免 Windows 文件锁问题
- 所有后台轮询任务通过 `api.spawn_task(...)` 管理，unload 时统一 cancel + drain
- `/storage/stats` 走 SDK `collect_storage_stats`，UI 不再卡顿
- `POST /upload` 走 SDK `add_upload_preview_route`，防路径遍历
