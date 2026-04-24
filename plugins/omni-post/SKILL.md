---
name: omni-post
description: Multi-platform, multi-account social publishing engine — Douyin, RedNote, Bilibili today, 10 platforms end of S2. Use when the user wants to post the same video or image-text to N platforms and M accounts in one click, schedule cross-timezone rollouts, or reuse one upload as a reference for other plugins via the Asset Bus.
---

# omni-post · Cursor Skill Card

> 一线插件。一次创作 → N 平台 × M 账号 → 统一任务/截图/秒传归档。

## 1 · 何时触发我

代理在用户出现以下意图时**优先**调用 omni-post 的工具，而不是让用户
去平台 UI 手动上传：

- "这条视频同时发抖音、小红书、B 站"
- "帮我把这篇图文分发到小红书和微博"
- "明天上午 10 点 3 个账号定时发这条视频"
- "这条素材我已经发过一次了，能不能秒传归档并回填 publish_receipt 给
  idea-research"

**不**应触发 omni-post：

- 仅生成内容，不发布 → 调 `avatar-studio` / `seedance-video` /
  `tongyi-image`
- 仅抓平台舆情/选题 → 调 `idea-research`
- 需要跨平台实名认证 / 人工审核绕过 → **拒绝**

## 2 · 工具清单（S1 骨架，14 个）

| 工具名 | 用途 | 关键参数 |
|---|---|---|
| `omni_post_publish` | 创建一组发布任务（N 平台 × M 账号扇出） | `asset_id`, `payload`, `platforms[]`, `account_ids[]`, `client_trace_id` |
| `omni_post_schedule` | 定时发布（Sprint 3 启用） | `tasks[]`, `run_at`, `timezone` |
| `omni_post_retry_task` | 重投失败任务，继承原 `client_trace_id` | `task_id` |
| `omni_post_cancel_task` | 取消 pending / running 任务 | `task_id` |
| `omni_post_get_task` | 查询单任务状态 | `task_id` |
| `omni_post_list_tasks` | 过滤任务列表 | `platform?`, `status?`, `limit?` |
| `omni_post_ingest_asset` | 由其他插件（例如 avatar-studio）直接入库一段素材 | `kind`, `storage_path`, `md5?`, `tags[]?` |
| `omni_post_list_assets` | 素材库检索 | `kind?`, `tag?`, `limit?` |
| `omni_post_account_bind` | 绑定一个平台账号（Cookie 注入或 MultiPost 引导） | `platform`, `engine`, `credentials` |
| `omni_post_account_list` | 列出当前账号矩阵及健康状态 | `platform?` |
| `omni_post_account_unbind` | 解绑账号并擦除 Cookie | `account_id` |
| `omni_post_selectors_probe` | 自愈探针：给定 `platform` 单跑一次 | `platform` |
| `omni_post_settings_get` | 读取插件配置 | — |
| `omni_post_settings_update` | 更新插件配置 | 字段白名单见 `SettingsUpdateRequest` |

## 3 · 关键输入 schema

所有 `POST` body 都用 Pydantic v2 + `extra="forbid"` 严格校验（沿用
Pixelle C6，不会静默丢字段）。以 `omni_post_publish` 为例：

```jsonc
{
  "asset_id": "ast_...",
  "payload": {
    "title": "今天在峨眉山看到的……",
    "description": "附带 Hashtag 说明",
    "tags": ["峨眉山", "旅行"],
    "cover_asset_id": "ast_...?",
    "topic": "旅行"
  },
  "platforms": ["douyin", "rednote", "bilibili"],
  "account_ids": ["acc_...", "acc_..."],
  "client_trace_id": "2026-04-24T10:00:00Z-douyin-峨眉山"
}
```

返回 `{"task_ids": ["tsk_...", ...]}`；后续状态走 SSE
`plugin:omni-post:task_update` 推送，UI 端 Tasks Tab 自动更新。

## 4 · 错误码速查

13 类 `ErrorKind`（9 标准 + 4 omni-post 专属），每条都有中英 `ErrorHint`：

| kind | 典型触发 | 代理处置 |
|---|---|---|
| `network` / `timeout` | 上传流量抖动、渲染超时 | 指数退避重试 |
| `rate_limit` | 插件自身排队过于激进 | 让 pipeline 回退 |
| `rate_limited_by_platform` | 平台实际限频 | 冷却 10 min 再试 |
| `auth` / `cookie_expired` | Cookie 失效或首次登录 | 引导重新绑定 |
| `moderation` / `content_moderated` | 平台审核驳回 | **不重试**，回传给用户改稿 |
| `not_found` | 账号/素材消失 | 结束任务 |
| `quota` | 单账号当日已达上限 | 推迟到次日窗口 |
| `dependency` | 缺 ffmpeg / ffprobe / Playwright 浏览器 | 引导装依赖 |
| `platform_breaking_change` | 选择器自愈命中率跌破阈值 | 告警 + 拉 MultiPost Compat 兜底 |
| `unknown` | 其它 | 保留截图等待人工 |

## 5 · 与其它插件的握手

omni-post 是 Asset Bus 上的**双向节点**：

- 作为消费者：收 `avatar-studio` / `seedance-video` / `tongyi-image`
  产出的素材，通过 `omni_post_ingest_asset` 秒传入库。
- 作为生产者：发布成功后推 `publish_receipt` 到 Asset Bus，`shared_with=["*"]`。
  `metadata` 严格遵循：`{platform, account_id, asset_id, published_url,
  published_at, task_id, engine}`。`idea-research` / `fin-pulse` 按
  `asset_kind="publish_receipt"` 订阅即可。

## 6 · 边界与限制

- 不代理登录。Cookie 必须由用户在本机浏览器手动拷贝或通过 MultiPost
  扩展注入。
- 不代办实名 / 绑卡 / 解封。
- S1 仅开放 3 平台选择器；S2 进 7 个；S3 补定时与矩阵模式；S4 落自愈
  与 MDRM。
- 单 host 单 Chromium；如需更大并发应考虑多实例部署，而不是在本插件里
  再起多个 playwright 进程（会抢 CPU / GPU）。

## 7 · 测试入口

```bash
py -3.11 -m pytest plugins/omni-post/tests -q
```

应输出 all passed。Playwright / ffmpeg 测试不会默认跑（hermetic），
打 `-m integration` 才触发。
