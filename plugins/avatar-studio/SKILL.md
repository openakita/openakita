---
name: avatar-studio
description: DashScope-powered digital human studio — photo speak, video relip, video reface, avatar compose. Use when the user asks for an AI talking-head video, lip-sync replacement on an existing video, replacing a person inside a video, or composing a new character from multiple reference images.
---

---
name: avatar-studio
description: Generate digital-human speaking photos, relip/reface videos, voices, and avatar compositions through Avatar Studio.
risk_class: network_out
---

# avatar-studio · Cursor Skill Card

> 一线插件。DashScope 万相 + s2v + cosyvoice + qwen-vl 全链路。

## 1 · 何时触发我

代理在用户出现以下意图时**优先**调用 avatar-studio 的工具，而不是手写
ffmpeg / ComfyUI 脚本或调其他视觉插件：

- "让这张照片说话 / 帮我做一个数字人开场白"
- "我录了一段视频，能不能换个嘴型 / 配音口型"
- "这段视频里的人换成另一个人"
- "把这两张参考图融合成一个新的角色，让他说一段话"
- "估算一下做 5 秒数字人视频要多少钱"

**不**应触发 avatar-studio：

- 纯文生视频（无人脸）→ 调 `seedance-video`
- 纯文生图 → 调 `tongyi-image`
- 仅做 TTS、不出视频 → 直接调 `cosyvoice` 工具或主端 TTS

## 2 · 工具清单

| 工具名 | 用途 | 关键参数 |
|---|---|---|
| `avatar_cost_preview` | 估算费用，**不**实际提交 | `mode`, 各 mode 必需的素材或 `text_chars` / `audio_duration_sec` |
| `avatar_photo_speak` | 创建照片说话任务 | `image_url` 或 `assets.image`, `text` 或 `audio_url`, `voice_id`, `resolution` |
| `avatar_video_relip` | 创建视频换嘴任务 | `video_url` 或 `assets.video`, `text` 或 `audio_url`, `voice_id` |
| `avatar_video_reface` | 创建视频换人任务 | `image_url`, `video_url`, `mode_pro` (bool), `watermark` (bool) |
| `avatar_compose` | 创建数字人合成任务 | `ref_images_url[]` (1-3 张), `prompt`, `text`, `voice_id`, `resolution` |
| `avatar_voice_create` | 注册自定义克隆音色 | `label`, `source_audio_path`, `dashscope_voice_id` |
| `avatar_voice_delete` | 删除自定义音色 | `voice_id` |
| `avatar_figure_create` | 注册自定义形象 | `label`, `image_path`, `preview_url` |
| `avatar_figure_delete` | 删除自定义形象 | `figure_id` |

每个 mode 工具返回 `任务已创建：{id}（mode=...）`；后续状态走 SSE
`plugin:avatar-studio:task_update` 推送，UI 端 Tasks Tab 自动更新。

## 3 · 输入 schema 速查

所有 `POST` body 都用 Pydantic + `extra="forbid"` 严格校验（落实 Pixelle
C6，不会静默丢字段）。下面是 4 个生成 mode 在 `/tasks` 端点的差异：

```jsonc
// photo_speak
{
  "mode": "photo_speak",
  "image_url": "https://...",
  // 二选一：text + voice_id  OR  audio_url
  "text": "你好",
  "voice_id": "longxiaochun_v2",
  // "audio_url": "https://...",
  "resolution": "480P",                  // 480P | 720P
  "audio_duration_sec": 3.0,             // 估价用；s2v 自身由音频时长决定
  "cost_approved": false                 // > threshold 时需为 true
}

// video_relip
{
  "mode": "video_relip",
  "video_url": "https://...",
  "text": "你好", "voice_id": "longxiaochun_v2"
  // 或 "audio_url"
}

// video_reface（最贵 — 务必先 cost-preview）
{
  "mode": "video_reface",
  "image_url": "https://...",
  "video_url": "https://...",
  "mode_pro": false,                     // false=wan-std ¥0.6/s，true=wan-pro ¥1.2/s
  "watermark": true,
  "video_duration_sec": 5,
  "cost_approved": true
}

// avatar_compose（多图 + 可选 AI prompt 助手）
{
  "mode": "avatar_compose",
  "ref_images_url": ["https://...", "https://..."],   // 1-3 张
  "prompt": "把人物融入古风街景，保留人物五官",
  "text": "你好", "voice_id": "longxiaochun_v2",
  "resolution": "480P",
  "audio_duration_sec": 5
}
```

> Tip：让 `qwen-vl-max` 替你写 `prompt` —— 调
> `POST /api/plugins/avatar-studio/ai/compose-prompt`，body
> `{"ref_images_url":[...], "user_intent":"古风街景"}`，返回 `{prompt}`。

## 4 · 输出 schema 速查

```jsonc
// POST /tasks
{ "ok": true, "task": { "id": "tsk_xxx", "mode": "...", "status": "pending", ... } }

// GET /tasks/{id}（任务跑完后）
{
  "ok": true,
  "task": {
    "id": "tsk_xxx",
    "mode": "photo_speak",
    "status": "succeeded",                 // pending | submitted | polling | succeeded | failed | cancelled
    "output_path": "/.../output.mp4",
    "output_url": "/api/plugins/avatar-studio/uploads/preview/...",
    "video_duration_sec": 3.2,
    "cost_breakdown": {
      "currency": "CNY",
      "items": [
        { "name": "wan2.2-s2v-detect", "units": 1, "unit_price": 0.004, "subtotal": 0.004 },
        { "name": "wan2.2-s2v 480P", "units": 3.0, "unit_price": 0.10, "subtotal": 0.30 },
        { "name": "cosyvoice-v2", "units": 0.0018, "unit_price": 0.20, "subtotal": 0.0036 }
      ],
      "total": 0.31,
      "formatted_total": "¥0.31"
    },
    "error_kind": null,
    "error_message": null,
    "error_hints": null,
    ...
  }
}
```

`status` 流转：`pending → submitted → polling → succeeded/failed/cancelled`。

## 5 · 错误码表

| `error_kind` | 触发场景 | 给用户的话 |
|---|---|---|
| `network` | 连接超时 / DNS 失败 / SSL | 检查网络；若用代理确认 `dashscope.aliyuncs.com` 可达；自动重试 3 次 |
| `timeout` | 请求或任务超时（600 s） | 任务可能仍在 DashScope 队列；30 s 后刷新 Tasks Tab |
| `rate_limit` | 同时处理中 > 1 / 429 | DashScope 异步任务并发上限 = 1，等当前任务完成 |
| `auth` | 401 / 403 | 重填 API Key；确认地域（北京 / 新加坡）与 base_url 匹配 |
| `not_found` | 404 / task_id 24 h 过期 | 重新提交；本地 sqlite 仍保留 metadata |
| `moderation` | 内容审核未通过 | 换素材；常见敏感：人脸不清晰 / 水印 / 暴力 / 政治 |
| `quota` | 余额不足 | 阿里云百炼控制台充值 |
| `dependency` | s2v-detect humanoid=false / 视频时长超限 | 必须真人正脸；参考视频 ≤ 30 s |
| `unknown` | 其他 | 反馈 task_id；截图 metadata json |

完整中英双语文案：`avatar_models.py::ERROR_HINTS`。

## 6 · 模式决策树

```
用户已经有完整视频，只想换嘴型？     → video_relip
用户有一张照片，想让她说话？           → photo_speak
用户有视频 A 和人物 B，想把 A 里的人换成 B？ → video_reface（最贵，先 cost-preview）
用户有多张参考图，想做一个新角色？     → avatar_compose
不确定：先用 avatar_cost_preview 跑 4 个 mode 报价，让用户挑
```

## 7 · 费用估算公式（按官方价目）

| 子项 | 单价 | 单位 |
|---|---|---|
| `wan2.2-s2v-detect` | ¥0.004 | 每张图 |
| `wan2.2-s2v` 480P | ¥0.10 | 每秒视频 |
| `wan2.2-s2v` 720P | ¥0.20 | 每秒视频 |
| `videoretalk` | ¥0.30 | 每秒视频 |
| `wan2.2-animate-mix` `wan-std` | ¥0.60 | 每秒视频 |
| `wan2.2-animate-mix` `wan-pro` | ¥1.20 | 每秒视频 |
| `wan2.5-i2i-preview` | ¥0.20 | 每张合图输出 |
| `qwen-vl-max` | ¥0.02 / ¥0.06 | 每 1k input / output token |
| `cosyvoice-v2` | ¥0.20 | 每 10k 字符 |

> 公式细节见 `avatar_models.py::PRICE_TABLE` 与 `estimate_cost`。
> **金额一律 `¥{:.2f}` 直显，禁止任何「奶茶单位」翻译**。

## 8 · 常见提示词模板

| Mode | 模板 | 说明 |
|---|---|---|
| `photo_speak` 文本 | `你好，欢迎来到 [场景]。今天我想分享 [主题]。` | 控制在 2-15 s 音频内成本最低 |
| `avatar_compose` prompt | `把 [主体描述] 融入 [场景描述]，保留 [关键细节]，整体风格 [风格词]。` | 60 字以内最稳；超长 qwen-vl 容易跑偏 |
| `video_reface` 元参 | 不需要 prompt — wan-animate-mix 直接看素材 | 务必关注 `mode_pro` 单价差 2× |

## 9 · 怎么测

```bash
# 单元测试（hermetic，必须 0 fail）
cd plugins/avatar-studio && py -3.11 -m pytest tests -q
# 期望输出：85 passed, 1 skipped

# 集成测试（opt-in，烧真钱 ≈ ¥0.31）
$env:DASHSCOPE_API_KEY = "sk-..."
py -3.11 -m pytest tests/integration -m integration -v
```

UI 烟测：见 [`README.md` §7](README.md)。

## 10 · 已知限制 & 不要做的事

- 不要绕过 `avatar_cost_preview` 直接提交 `video_reface` `mode_pro=true` 的
  长视频任务 —— 一次就是几十块。
- 不要把 `<CostBreakdown>` 移到 Create Tab 实时显示 —— 早期做过、被
  否决（浪费空间、用户不关心提交前的逐字符费用）。成本明细只在 Task
  详情抽屉里出现。
- 不要假设 DashScope 任务并发 > 1 —— 平台级硬限。`Semaphore(1)` 是
  正确实现，不是 bug。
- 不要在模块顶层 `import dashscope`、`import ffmpeg` 之类的强依赖 ——
  落实 Pixelle C4，所有可选依赖**惰性 import**。
- 不要 `from _shared import ...` —— 那是 archive 的兼容桩，仅旧插件用。
  avatar-studio 全部依赖 vendored 在 `avatar_studio_inline/` 里。
- 不要写 `from openakita_plugin_sdk.contrib import ...` —— SDK 0.7.0 已
  移除该子包（commit `d6d0c964`）。
- API Key 缺失只能 **warn**，绝不能在 `on_load` 抛异常 —— 落实 Pixelle
  C5，否则用户没法进 Settings 填 Key。
