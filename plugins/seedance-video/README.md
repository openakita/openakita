# 即梦工作室 / Seedance Studio

基于火山引擎 Ark API 的 AI 视频生成插件 — 文生视频、图生视频、多模态、视频编辑、视频续写、长视频分镜拼接。

| | |
|---|---|
| **版本** | 1.3.0 |
| **SDK 范围** | `>=0.7.0,<0.8.0` |
| **Plugin API** | `~2` / UI API `~1` |
| **入口** | `plugin.py` (`PluginBase`) + `ui/dist/index.html` |

## 给小白用户

1. 在 Settings 里粘贴 **ARK API Key**（[火山引擎控制台领取](https://console.volcengine.com/ark)）
2. 打开 "即梦工作室" 页签
3. 选择模式（文生视频 / 图生视频 / 编辑 / 续写 …），写一段描述
4. 点【生成】，任务异步执行 — 进度自动推送到 UI
5. 成功后视频自动下载到本地 `data_dir/videos/`，可直接预览播放

## 三大特点

- **6 种模式覆盖** — 文生视频、图生视频(首帧/首尾帧)、多模态、编辑、续写，一个插件搞定
- **长视频 pipeline** — AI 自动拆解分镜脚本，串行/并行生成 + ffmpeg 拼接，轻松做长视频
- **安全加固** — BaseVendorClient 继承(自动 retry/VendorError 分类)、upload 路径校验、async 资源清理

## 配置 / Config

| 字段                  | 默认              | 说明                                   |
| --------------------- | ----------------- | -------------------------------------- |
| `ark_api_key`         | _empty_           | 火山引擎 Ark API Key（必填）          |
| `default_model`       | `seedance-1-lite` | 默认模型                              |
| `default_ratio`       | `16:9`            | 默认宽高比                            |
| `default_duration`    | `5`               | 默认时长（秒）                        |
| `default_resolution`  | `720p`            | 默认分辨率                            |
| `poll_interval_sec`   | `10`              | 轮询间隔                              |
| `auto_download`       | `true`            | 任务成功后自动下载到本地             |
| `service_tier`        | `default`         | 推理等级（default / flex）            |

## API 速查

```bash
# 创建任务
curl -X POST localhost:8000/api/plugins/seedance-video/tasks \
  -H 'content-type: application/json' \
  -d '{"prompt":"一只小猫在阳光下的花园中追蝴蝶","model":"seedance-1-lite","mode":"t2v"}'

# 查询任务
curl localhost:8000/api/plugins/seedance-video/tasks/{task_id}

# 上传参考文件（图片/视频/音频）
curl -X POST localhost:8000/api/plugins/seedance-video/upload \
  -F file=@ref.png
# 响应: { "ok": true, "path": "...", "url": "/api/plugins/seedance-video/uploads/<file>" }

# 长视频: 分镜拆解
curl -X POST localhost:8000/api/plugins/seedance-video/storyboard/decompose \
  -H 'content-type: application/json' \
  -d '{"story":"一只小猫探索花园的故事","total_duration":30}'

# 存储统计
curl localhost:8000/api/plugins/seedance-video/storage/stats
```

## 测试

> 当前缺单元测试。后续会补 `tests/test_task_manager.py` + `tests/test_routes.py`。

## 相关插件 / Related

- `tongyi-image` — AI 图片生成（用来出首帧参考图，再做图生视频）
- `storyboard` — 脚本分镜管理（可导出到本插件批量生成视频）
- `bgm-suggester` — BGM 推荐（为生成的视频配乐）
