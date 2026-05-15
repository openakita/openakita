---
name: happyhorse-video
description: Bailian-powered unified video studio — HappyHorse 1.0 (text-to-video / image-to-video / reference-to-video / video-edit) + Wan 2.6/2.7 + 5 digital-human modes (photo speak / video relip / video reface / pose drive / avatar compose) + CosyVoice & Edge-TTS + storyboard long-video pipeline. Use when the user asks for AI video generation on Aliyun Bailian, multi-character video with audio sync, video editing/style transfer, talking-head, lip-sync, video reface, or storyboard-driven long video.
env_any:
  - DASHSCOPE_API_KEY
---

# 快乐马工作室 / HappyHorse Studio · Cursor Skill Card

> 一线插件。阿里云百炼 HappyHorse 1.0 + 万相 2.6/2.7 + s2v + animate +
> videoretalk + cosyvoice + qwen-vl 全链路。OSS 签名 URL 喂 DashScope。

## 1 · 何时触发我

代理在用户出现以下意图时**优先**调用 happyhorse-video 的工具：

- "生成一段视频 / 文生视频 / 图生视频 / 多角色互动视频 / 参考生视频"
- "视频编辑 / 风格替换 / 局部换装 / 视频里的物体改成..."
- "首尾帧视频 / 视频续写 / 视频接龙"
- "拍一段长视频 / 30 秒 / 1 分钟短片，自动分镜并拼接"
- "数字人 / 照片说话 / 视频换嘴 / 换人 / 图生动作"
- "估算一下视频生成要多少钱"

**不**应触发 happyhorse-video：

- 火山 Seedance 任务 → 调 `seedance-video`
- 文生图 / 图生图（无视频）→ 调 `tongyi-image`
- 仅做 TTS、不出视频 → 直接调 `cosyvoice` 工具或主端 TTS

## 2 · 工具清单

| 工具名 | 用途 | 关键参数 |
|---|---|---|
| `hh_t2v` | 文生视频 | `prompt`, `model?`, `duration?`, `resolution?`, `aspect_ratio?` |
| `hh_i2v` | 图生视频（首帧 / 首尾帧 / 续写） | `prompt`, `first_frame_url` 或 `from_asset_ids`, `last_frame_url?`, `task_type?` |
| `hh_r2v` | 参考生视频（多角色互动） | `prompt`, `reference_urls` 或 `from_asset_ids`, `shot_type?` |
| `hh_video_edit` | 视频编辑 | `prompt`, `video_url`, `reference_image_url[]?` |
| `hh_photo_speak` | 照片说话 | `image_url`, `text` 或 `audio_url`, `voice_id?` |
| `hh_video_relip` | 视频换嘴 | `video_url`, `text` 或 `audio_url`, `voice_id?` |
| `hh_video_reface` | 视频换人 | `image_url`, `video_url`, `mode_pro?` |
| `hh_pose_drive` | 图生动作 | `image_url`, `video_url`, `mode_pro?` |
| `hh_avatar_compose` | 数字人合成 | `ref_images_url[]`, `prompt`, `text`/`audio_url` |
| `hh_long_video_create` | 长视频分镜拼接 | `story`, `total_duration`, `mode (serial/parallel)` |
| `hh_cost_preview` | 估算费用，**不**实际提交 | 各 mode 必需的素材或 `text_chars`/`audio_duration_sec` |
| `hh_status` | 查询任务状态 | `task_id` |
| `hh_list` | 列最近任务 | `limit?` |

工具返回 JSON 包含：`ok`, `task_id`, `status`, `mode`, `model_id`,
`video_url`, `video_path`, `last_frame_url`, `last_frame_path`,
`local_paths`, `asset_ids`。失败时 `ok=false` + `error_kind` +
`error_message` + `terminal=true`。

## 3 · 工作台联动协议

每个 `hh_*` 创建工具的输入 schema 都接受 `from_asset_ids: string[]`，
runtime 把上游图片工作台（如 `tongyi-image`）产出的 asset_ids 透传过来：

| mode | from_asset_ids 角色映射 |
|---|---|
| `i2v` | `[0]` → first_frame，`[1+]` → reference_image |
| `i2v_end` | `[0]` → first_frame，`[1+]` → last_frame |
| `r2v` | 全数组 → `reference_urls`（按角色顺序） |
| `video_edit` | 全数组 → `reference_image_url[]` |
| `avatar_compose` | 全数组 → `ref_images_url` |

工具返回的 `asset_ids` 是本插件登记到 Asset Bus 的视频资产，下游可继续消费。

## 4 · 模型派发表（client 由 registry 驱动，无硬编码 if-else）

| 模型族 | 协议 | 关键参数 |
|---|---|---|
| `happyhorse-1.0-*` | 新版 video-synthesis | `resolution: "720P"` / `"1080P"`（**P 大写**）。**禁用** `with_audio` / `size` / `quality` / `fps` / `audio` |
| `wan2.7-i2v` | 新版 video-synthesis + 多模态 | `resolution`, `task_type` (`first-frame`/`first-and-last-frame`/`video-continuation`) |
| `wan2.6-*` | 旧版 video-synthesis | `size: "1280*720"`（**星号 W*H**）, `audio: true` |
| `wan2.2-s2v` / `s2v-detect` | s2v 旧版 | OSS 签名 URL 必填 |
| `wan2.2-animate-*` | animate 旧版 | `mode: "wan-std"\|"wan-pro"` |
| `videoretalk` | videoretalk 旧版 | 视频 + 音频 URL |

## 5 · 烟测脚本（≈¥0.50, < 5 分钟）

```text
1. Settings 填 DashScope API Key + OSS 四件套
2. Create Tab → t2v → prompt: "海上日出，慢镜头，电影感"
3. duration=5, resolution=720P → 估算费用 ≈ ¥4.5（HappyHorse 1.0）
4. 「直接提交」→ Tasks Tab 看进度 → 60-180 秒后 done
5. 抽屉看视频，下载 mp4
```

## 6 · 已知陷阱

- **DashScope 异步任务并发 = 1 / Key**：本插件 `Semaphore(1)` 串行化。
- **HappyHorse 1080P = 1.6 元/秒**：5 秒 = ¥8，请先估价。
- **OSS bucket 跨地域不可达**：endpoint 区域必须和 API Key 区域一致。
- **task_id 24 h 过期**：超期 `hh_status` 返 `not_found`；本地 sqlite 仍存 metadata。

## 7 · 相关插件

- [`tongyi-image`](../tongyi-image/) — 上游分镜图工作台（asset_ids → from_asset_ids）
- [`seedance-video`](../seedance-video/) — 火山版视频工作台（双轨并存，不替代）
- [`avatar-studio`](../avatar-studio/) — 数字人专项（多 backend，不替代）
