# video-bg-remove

> 视频抠像（去背景 / 换背景）：基于 RVM (Robust Video Matting) MobileNetV3 +
> onnxruntime，把任意视频里的人物抠出来，再合成到纯色 / 自定义图片 / 透明
> 背景上。

## 设计原则

1. **算法住在 `matting_engine.py`**：`run_matting` 接受可注入的
   `session_factory`、`write_frame`、`on_progress`，方便 `shorts-batch`（D3）
   后续把它当一道工序串进流水线，不需要再起 HTTP。
2. **依赖懒加载**：`onnxruntime` 是百兆级依赖，不在插件 import 时就加载。
   `check_deps` 把缺失原因（onnxruntime / 模型文件 / ffmpeg）拆成三栏，避免
   "为什么我装了 ffmpeg 还是不行" 的歧义。
3. **透明背景强制 `.mov`**：libx264 不支持 alpha；与其让 ffmpeg 静默丢通道，
   不如在 `plan_matting` 阶段就 raise `ValueError`，配合 `ErrorCoach` 给
   "请把 output_path 改成 .mov" 的建议。
4. **D2.10 verification**：4 个 yellow flag（零帧 / 低 alpha / 零字节 /
   截断渲染），全部非阻塞 —— 输出还是给用户，但把不确定性暴露出来。

## 三种背景

| `background.kind` | 必填字段 | 输出容器 | 用途 |
|---|---|---|---|
| `color`（默认）| `color: [r,g,b]`，默认 `[0,177,64]`（chroma green）| `.mp4` | 直接出"绿幕"，方便 DaVinci 二次抠 |
| `image` | `image_path`（必须存在）| `.mp4` | 把人物贴到一张静态图上 |
| `transparent` | —— | `.mov`（强制）| 留 RGBA 给下游合成 |

## 用法（HTTP）

```bash
# 1. 检查依赖
curl http://127.0.0.1:8000/api/plugins/video-bg-remove/check-deps

# 2. 看看会出什么 plan（不渲染）
curl -X POST http://127.0.0.1:8000/api/plugins/video-bg-remove/preview \
  -H 'Content-Type: application/json' \
  -d '{"input_path": "/path/to/clip.mp4"}'

# 3. 真正跑一次（默认绿幕）
curl -X POST http://127.0.0.1:8000/api/plugins/video-bg-remove/tasks \
  -H 'Content-Type: application/json' \
  -d '{"input_path": "/path/to/clip.mp4"}'

# 4. 透明输出（自动 .mov）
curl -X POST http://127.0.0.1:8000/api/plugins/video-bg-remove/tasks \
  -H 'Content-Type: application/json' \
  -d '{"input_path": "/path/to/clip.mp4",
       "background": {"kind": "transparent"}}'

# 5. 拉成品
curl -O http://127.0.0.1:8000/api/plugins/video-bg-remove/tasks/<id>/video
```

## 一次性准备

把 `rvm_mobilenetv3_fp32.onnx`（约 100 MB）从
[PeterL1n/RobustVideoMatting releases](https://github.com/PeterL1n/RobustVideoMatting/releases)
下载下来，丢到：

```text
data/plugins/video-bg-remove/models/rvm_mobilenetv3_fp32.onnx
```

然后 `pip install onnxruntime`（CPU）或 `onnxruntime-gpu`（GPU）。
`check_deps` 会三栏告诉你哪一项还缺。

## 测试

```bash
.\.venv\Scripts\python.exe -m pytest plugins/video-bg-remove/tests/ -v
```

72 个测试覆盖：颜色解析、`Background` 校验、`plan_matting`（含 transparent
强制 `.mov`）、`probe_video_meta` 兜底、`composite_frame`（color / image /
transparent / RGBA→RGB）、`onnxruntime_available`、`model_available`、
`ffmpeg_available`、`to_verification` 4 种触发条件、worker 生命周期、
`check_deps` HTTP+brain tool、`/preview`/`/tasks`/`/tasks/{id}/video` 全套。

测试用 `monkeypatch` 替换 `onnxruntime.InferenceSession` 和 ffmpeg 写入器，
所以 CI runner 不需要装 onnxruntime 或下载模型也能跑。

## 与其他插件的关系

* **依赖** SDK ≥ 0.4.0（需要 `BaseTaskManager`、`Verification` D2.10、
  `ffprobe_json_sync`、`ErrorCoach`、`QualityGates`）。
* **被** `shorts-batch`（D3，未来 Sprint 17）通过
  `from matting_engine import run_matting` 直接复用，作为"去背景 → 换背景 →
  叠字幕 → 出片"链中的一环。
* 与 `video-color-grade`、`bgm-mixer` 同级，可以串成
  "去背景 → 换背景 → 调色 → 配 BGM → 出片" 的完整后期链。
