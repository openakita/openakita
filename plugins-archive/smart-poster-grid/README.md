# smart-poster-grid

> 一次生成 4 个社交尺寸（1:1 / 3:4 / 9:16 / 16:9）的同款海报，复用
> ``poster-maker`` 插件的模板和 Pillow 渲染器。

## 设计原则

1. **完全复用 poster-maker**：所有布局 / 字体 / Pillow 调用都活在
   ``poster-maker/poster_engine.py``。本插件只负责"为每个目标尺寸找
   到一个合适的模板，调一次 ``render_poster``，把 4 张 PNG 汇总成一
   个任务记录"。
2. **9:16 通过 clone 合成**：poster-maker 自带 3 种尺寸（1:1、3:4、
   16:9），9:16 没有原生模板。我们克隆 3:4 的 ``vertical-poster``，
   只换画布尺寸 — 因为模板的 slot 坐标是归一化的（0-1），自动适配新
   宽高比。
3. **部分失败不致命**：4 张里只有 1 张挂了，其余 3 张照常输出，挂
   掉的那一张通过 ``Verification`` 黄色字段告知用户。

## 4 个尺寸

| ID | 尺寸 | 用途 | 底层模板 |
|----|------|------|----------|
| `1x1`  | 1080×1080  | 朋友圈 / 小红书封面 / IG | `social-square` |
| `3x4`  |  900×1200  | 公众号竖图 / 活动海报 | `vertical-poster` |
| `9x16` | 1080×1920  | TikTok / Reels / Shorts 封面 | `vertical-poster` (clone) |
| `16x9` | 1920×1080  | YouTube / Twitter Banner / 视频封面 | `banner-wide` |

## 用法（HTTP）

```bash
# 1. 看看默认会出哪 4 个 ratio（不渲染）
curl -X POST http://127.0.0.1:8000/api/plugins/smart-poster-grid/preview \
  -H 'Content-Type: application/json' \
  -d '{"text_values": {"title": "新品发布"}}'

# 2. 真正跑一次（4 张全出）
curl -X POST http://127.0.0.1:8000/api/plugins/smart-poster-grid/tasks \
  -H 'Content-Type: application/json' \
  -d '{"text_values": {"title": "新品发布", "subtitle": "5月10日上线"}}'

# 3. 只跑 1:1 + 9:16
curl -X POST http://127.0.0.1:8000/api/plugins/smart-poster-grid/tasks \
  -H 'Content-Type: application/json' \
  -d '{"text_values": {"title": "Hi"}, "ratio_ids": ["1x1", "9x16"]}'

# 4. 拉成品
curl -O http://127.0.0.1:8000/api/plugins/smart-poster-grid/tasks/<id>/poster/9x16
```

## 测试

```bash
.\.venv\Scripts\python.exe -m pytest plugins/smart-poster-grid/tests/ -v
```

50 个测试覆盖：4 个 ratio 的元数据、模板合成（包含 9:16 clone 不污
染原模板）、ratio_ids 校验（去重 / 拒绝未知 / 拒绝空字符串 / 拒绝
空列表）、render 成功路径、部分失败路径、verification envelope 4
种触发条件、worker 生命周期、HTTP 路由 5 个 brain tool。

## 与其他插件的关系

* **硬依赖** sibling plugin `poster-maker`（共用其 ``templates`` 和
  ``poster_engine.render_poster``）— 缺失会在渲染时报 ``ImportError``。
* SDK ≥ 0.4.0
* 与 `video-color-grade`、`bgm-mixer` 同属 D 系列消费者插件，可以串成
  "海报多尺寸 → 视频调色 → BGM 对齐"的发布流。
