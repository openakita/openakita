# 通义生图 / Tongyi Image

基于阿里云百炼 DashScope 的 AI 图片生成插件 — 文生图、图像编辑、风格重绘、背景生成、画面扩展、涂鸦作画、电商套图。

## 给小白用户

1. 在 Settings 里粘贴 **DashScope API Key**（[百炼控制台领取](https://dashscope.console.aliyun.com/)）
2. 打开"通义生图"页签
3. 写一句中文描述（如 _"国风少女在竹林前抚琴，水墨风"_），选模型 + 尺寸
4. 点【生成】，任务异步执行 — 进度自动推送到 UI
5. 成功后图片自动下载到本地 `data_dir/images/`，可右键复制到剪贴板

## 三大特点

- **7 种模式覆盖** — 从纯文生图到电商套图，一个插件搞定
- **任务队列 + 自动下载** — 关掉浏览器也不会丢，下次回来直接看结果
- **安全加固** — SQL 白名单、上传路径校验、async 资源清理（Sprint 1）

## 配置 / Config

| 字段                    | 默认                | 说明                                        |
| ----------------------- | ------------------- | ------------------------------------------- |
| `dashscope_api_key`     | _empty_             | DashScope API Key（必填）                  |
| `default_model`         | `wan27-pro`         | 默认模型                                   |
| `default_size`          | `1024*1024`         | 默认尺寸（DashScope 用 `*` 而非 `x`）     |
| `poll_interval_sec`     | `10`                | 轮询间隔                                   |
| `auto_download`         | `true`              | 任务成功后自动下载到本地                  |

## API 速查

```bash
# 创建任务
curl -X POST localhost:8000/api/plugins/tongyi-image/tasks \
  -H 'content-type: application/json' \
  -d '{"prompt":"国风少女水墨画","model":"wan27-pro","size":"1024*1024"}'

# 查询任务
curl localhost:8000/api/plugins/tongyi-image/tasks/{task_id}

# 上传参考图
curl -X POST localhost:8000/api/plugins/tongyi-image/upload \
  -F file=@ref.png
# 响应: { "ok": true, "path": "...", "url": "/api/plugins/tongyi-image/uploads/<file>" }

# 存储统计（异步、有上限）
curl localhost:8000/api/plugins/tongyi-image/storage/stats
```

## 测试

> 当前缺单元测试。Sprint 2 会补 `tests/test_tongyi_task_manager.py` + `tests/test_routes.py`，
> 然后再做 BaseTaskManager / BaseVendorClient 的深度迁移。请勿在没补测试前改写 task_manager。

## 相关插件 / Related

- `image-edit` — 需要精确蒙版的局部修图
- `poster-maker` — 海报排版（用本插件出图，再合成）
- `seedance-video` skill — 想把图片做成视频
- `storyboard` — 把脚本拆成多镜头，喂本插件批量出图
