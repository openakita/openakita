# dub-it

视频配音翻译插件：把视频音轨转写、翻译、TTS 后混回原视频，实现"原画面 + 译制配音"。流程上游接 SDK 的 `source_review` (D2.3) 做体检，避免在小屏录像 / 极短片段上烧配额。

## 设计原则

- **体检先行（D2.3）**：源视频不达标（分辨率 < 720×480、时长 < 3s、无音轨…）直接失败，不进入 ASR / 翻译/TTS 任何一步。
- **可插拔 ASR / LLM / TTS**：默认是确定性 stub；生产环境通过 `Plugin.set_transcriber/set_translator/set_synthesizer` 注入真实后端（whisper / OpenAI / EdgeTTS / DashScope CosyVoice 等）。
- **D2.10 校验包络**：失败 / 0 字节 / 0 段 / 空译文 / 体检警告都会变成黄旗。
- **依赖透明**：仅需要 `ffmpeg` + `ffprobe`，通过 `/check-deps` 暴露。

## 安装

```bash
# 系统二进制
winget install Gyan.FFmpeg     # Windows
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Linux
```

无额外 Python 依赖。需要 OpenAkita >= 1.27.0 / SDK >= 0.4.0。

## HTTP 用法

```bash
# 1) 体检（不花钱）
curl -X POST http://localhost:8000/plugins/dub-it/review \
  -H "Content-Type: application/json" \
  -d '{"source_video":"/path/to/in.mp4"}'

# 2) 提交任务
curl -X POST http://localhost:8000/plugins/dub-it/tasks \
  -H "Content-Type: application/json" \
  -d '{
    "source_video":"/path/to/in.mp4",
    "target_language":"zh-CN",
    "output_path":"/path/to/out.mp4",
    "duck_db":-18,
    "keep_original_audio":true
  }'

# 3) 查询 / 下载
curl http://localhost:8000/plugins/dub-it/tasks/<task_id>
curl http://localhost:8000/plugins/dub-it/tasks/<task_id>/output -o out.mp4
```

## Brain Tools

```text
dub_it_review_source → 仅做 source_review，不开始任何工作
dub_it_check_deps    → ffmpeg / ffprobe 是否就位
dub_it_create        → 提交任务（异步）
dub_it_status        → 查询状态 + 段数
dub_it_list          → 列出最近 20 个
dub_it_cancel        → 取消运行中
```

## 配置项

| key | 默认 |
|-----|------|
| `default_target_language` | `zh-CN` |
| `default_output_format` | `mp4` |
| `default_duck_db` | `-18` |
| `default_keep_original_audio` | `true` |
| `default_ffmpeg_timeout_sec` | `1800.0` |
| `default_ffprobe_timeout_sec` | `15.0` |

## 测试

```bash
.\.venv\Scripts\python.exe -m pytest plugins/dub-it/tests -q
```

81 个测试覆盖：DubSegment 数据模型、`plan_dub` 校验、`source_review` 短路、ffmpeg argv 构造（提取 / 混音 / 闪避）、`run_dub` 全流程（依赖注入版）、D2.10 校验、HTTP 路由、brain tool 五件套。
