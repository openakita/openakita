# video-color-grade

> 一键调色：自动给视频加微调（亮度/对比度/饱和度），所有调整最多 ±8%，
> 不做任何"创意调色"。也可以直接选 ``warm_cinematic`` 等预设。

## 设计原则

1. **所有算法住在 SDK**：`sample_signalstats` + `auto_color_grade_filter`
   都在 `openakita_plugin_sdk.contrib.ffmpeg` 里，本插件只做"接线"。
2. **±8% 上限**：任何一根轴（contrast/gamma/saturation）的调整都被
   `DEFAULT_GRADE_CLAMP_PCT = 0.08` 硬卡住，不会出现"AI 调出离谱效果"。
3. **始终重新编码**：即使 filter 为空（无须调整），也会用 libx264 + faststart
   重新封装一次，保证下游消费者拿到的总是一致的 mp4 容器。

## 模式

* `auto` — 默认。先用 ffmpeg signalstats 抽 10 帧（约 10 秒窗口），算 y_mean / y_range / sat_mean，再生成 `eq=...` 字符串。
* `preset:subtle` — 极轻微基线，几乎看不出。
* `preset:neutral_punch` — 轻对比度 + 柔和 S 曲线。
* `preset:warm_cinematic` — 暖调电影感（**OPT-IN**，不要默认用）。
* `preset:none` — 不调色，仅重新封装。

## 用法（HTTP）

```bash
# 1. 看看 auto 模式会出什么 filter（不渲染）
curl -X POST http://127.0.0.1:8000/api/plugins/video-color-grade/preview \
  -H 'Content-Type: application/json' \
  -d '{"input_path": "/path/to/clip.mp4"}'

# 2. 真正跑一次
curl -X POST http://127.0.0.1:8000/api/plugins/video-color-grade/tasks \
  -H 'Content-Type: application/json' \
  -d '{"input_path": "/path/to/clip.mp4"}'

# 3. 拉成品
curl -O http://127.0.0.1:8000/api/plugins/video-color-grade/tasks/<id>/video
```

## 测试

```bash
.\.venv\Scripts\python.exe -m pytest plugins/video-color-grade/tests/ -v
```

49 个测试覆盖：参数验证、模式分发、ffprobe 失败兜底、ffmpeg 失败兜底、
worker 生命周期、verification envelope 5 种触发条件、brain tool 5 个。

## 与其他插件的关系

* **依赖** SDK ≥ 0.4.0（需要 B7 `auto_color_grade_filter`）
* **被** `shorts-batch`（D3，未来 Sprint 17）通过 `from grade_engine import build_grade_command` 直接复用
* 与 `bgm-mixer` 同级，一前一后跑可以做"BGM 对齐 → 一键调色 → 出片"的链
