---
name: tongyi-image
description: 通过阿里云百炼 DashScope 生成 AI 图片 — 文生图、图像编辑、风格重绘、背景生成、画面扩展、涂鸦作画、电商套图。任务异步执行+轮询，成功后可自动下载到本地。
env_any:
  - DASHSCOPE_API_KEY
---

# 通义生图 / Tongyi Image

## 是什么 / What

输入一句中文/英文描述（可选参考图、风格、尺寸），输出一张或多张高清图片。
所有调用都是 DashScope 异步任务：先 `POST` 拿 `task_id`，再轮询 `GET task_status`，
就绪后下载图片 URL（24h 过期，自动落盘到 `data_dir/images/`）。

## 何时用 / When

- 用户描述了一个画面想法，想要参考图 / 海报 / 头图
- 用户已有一张图，想换风格、改局部、补背景、扩画面
- 用户在做电商，想一键给同一商品出多场景图
- **不要用于**: 视频生成（用 `seedance-video` skill）；需要精确蒙版的局部修图（用 `image-edit` 插件）

## 工具 / Tools

- `tongyi_image_create({prompt, model?, size?, negative_prompt?, n?})`
- `tongyi_image_status({task_id})`
- `tongyi_image_list({limit?})`

## 模式 / Modes

| mode             | 描述                          | 主流模型                               |
| ---------------- | ----------------------------- | -------------------------------------- |
| text2img         | 纯文本生图                    | wan27-pro, qwen-pro                    |
| img_edit         | 参考图改写（无蒙版）          | wan27-edit                             |
| style_repaint    | 把一张人像换成预设风格        | wanx-style-repaint                     |
| bg_generation    | 给商品/前景图生成新背景       | wanx-background-generation             |
| outpainting      | 画面外扩 / 改变长宽比         | wanx-outpainting                       |
| sketch2img       | 涂鸦/线稿成图                 | wanx-sketch-to-image                   |
| ecommerce        | 电商套图（一组商品场景图）    | wanx-ecommerce                         |

## 流程 / Pipeline

```
prompt + (可选 ref image) → DashScope POST → task_id →
  poll loop（默认 10s）→ status==SUCCEEDED →
    extract image URLs → (可选 auto_download) → 落盘 + 广播 task_update
```

## Quality Gates (G1–G3)

| Gate | 检查内容                                     | 通过条件                                                          |
| ---- | -------------------------------------------- | ----------------------------------------------------------------- |
| G1   | DashScope API Key 已配置                     | `await tm.get_config("dashscope_api_key")` 非空                  |
| G2   | upload 路径在插件 data_dir 内（防遍历）     | `add_upload_preview_route` 已注册 + `path.relative_to(base)` OK  |
| G3   | task 异常用日志 + UI broadcast 兜底          | `_broadcast_update(task_id, "failed")` + `error_message` 落库     |

## Trust Hooks（你怎么知道我没乱花钱 / 偷数据）

| 信任点              | 怎么自查                                                  |
| ------------------- | --------------------------------------------------------- |
| 钱花在哪 / Cost     | DashScope API 唯一调用方；本插件不引入第三方计费          |
| 数据流向 / Data     | 上传图先存 `data_dir/uploads/`，仅作为 base64 发给百炼    |
| 出错怎么办 / Errors | 全部异常落库 `error_message`，UI 通过 task_update 推送    |
| 远程依赖 / Network  | 仅 `https://dashscope.aliyuncs.com/`；无遥测              |

## 已知坑 / Known Pitfalls

- **API Key 没填**: 所有请求 502 → UI 提示去 Settings 配置
- **DashScope content moderation**: 敏感词命中会直接 fail，不可重试 — 改 prompt 重试
- **图片 URL 24h 过期**: 必须开 `auto_download=true`（默认开）才能离线复用
- **长时间任务排队**: 默认 poll 间隔 10s，繁忙时段可能等几分钟 — 不会卡 UI
- **大批量电商套图**: 一次提交 6+ 场景会显著增加成本 — 先用 1 张试效果
- **路径遍历攻击**: 已通过 SDK `add_upload_preview_route` 兜底（`relative_to(base)`）

## 安全升级 (Sprint 1) / Hardening Notes

- `update_task` SQL 列名走白名单，杜绝注入（见 `tongyi_task_manager._UPDATABLE_COLUMNS`）
- `on_unload` 改为 async，await `_poll_task` / `_client.close()` / `_tm.close()`，避免 Windows WinError 32
- 所有 fire-and-forget 后台任务改用 `api.spawn_task(...)`，host 在 unload 时统一 cancel + drain
- `/storage/stats` 走 SDK `collect_storage_stats`（`asyncio.to_thread` + `max_files` 上限），UI 不再卡顿
- `POST /upload` 响应新增 `url` 字段（`build_preview_url`），UI 端可渐进迁移到可访问 URL（见 issue #479）

## storyboard 一键投喂 / Storyboard Bridge (Sprint 2)

`storyboard` 插件新增 `GET /api/plugins/storyboard/tasks/{task_id}/export-tongyi.json`，
把分镜直接转成本插件可消费的请求体数组：

```bash
# 拿到分镜 → 一键批量出图
curl -s localhost:8000/api/plugins/storyboard/tasks/<sb_id>/export-tongyi.json \
  | jq -c '.post_examples[]' \
  | while read -r row; do
      body=$(echo "$row" | jq -c '.body')
      curl -s -X POST localhost:8000/api/plugins/tongyi-image/tasks \
        -H 'content-type: application/json' -d "$body"
    done
```

`prompt = visual + 构图 + 风格`（省去音效/台词），`size` 默认 `1024*1024`，
`n` 自动 clamp 到 `1..4`，body 字段名与 `CreateTaskBody` 完全一致 — 测试断言保护，
后续如重命名会立即报错。
