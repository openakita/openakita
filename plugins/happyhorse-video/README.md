# happyhorse-video / 快乐马工作室

> 阿里云百炼一体化视频工作室。HappyHorse 1.0 主力 + 万相 2.6/2.7 + 数字人 5 模式 + CosyVoice/Edge-TTS + 长视频分镜，统一 OSS 签名 URL，单后端 DashScope。
> 与 [`plugins/seedance-video`](../seedance-video/)（火山版）/ [`plugins/avatar-studio`](../avatar-studio/)（数字人专项）并列同构、互不替代。

| | |
|---|---|
| **版本** | 1.0.0 |
| **SDK 范围** | `>=0.7.0,<0.8.0` |
| **Plugin API** | `~2` / UI API `~1` |
| **入口** | `plugin.py` (`PluginBase`) + `ui/dist/index.html` |
| **形态** | 12 模式 + 7 Tab + 13+ 工具 + 黑色主题 + Iconify SVG |

## 1 · 核心特点

1. **HappyHorse 1.0 主力**：`happyhorse-1.0-t2v` / `-i2v` / `-r2v` / `-video-edit`，原生音视频同步、7 语种唇形、24fps MP4、3-15 秒、720P / 1080P。
2. **Wan 2.6 / 2.7 备选**：`wan2.6-t2v` / `wan2.6-i2v(-flash)` / `wan2.6-r2v(-flash)` / `wan2.7-i2v`（多模态：首帧 / 首尾帧 / 视频续写）。
3. **数字人 5 模式**（沿用 avatar-studio 的链路）：照片说话 / 视频换嘴 / 视频换人 / 图生动作 / 数字人合成。
4. **TTS 双引擎**：CosyVoice-v2（12 系统音色 + 自定义克隆，0.20 元/万字）+ Edge-TTS（免费，12 中文音色）。
5. **每 mode 可选模型 + Settings 默认**：Create 表单顶部一个 `<select>` 候选来自 registry；不选就回落到 Settings 里的 `default_model_<mode>`。
6. **长视频流水线**：AI 自动拆分镜、串行 / 并行生成、ffmpeg 拼接（可选交叉淡化）。
7. **OSS 签名 URL**：用户上传素材自动推到自有 bucket，再以 6h 签名 URL 喂给 DashScope。
8. **工作台联动**：每个 `hh_*` 工具返回 `video_url` / `video_path` / `local_paths` / `asset_ids`；接受 `from_asset_ids` 消费上游 `tongyi-image` / `wan2.7-image` 等图片工作台的产物，组织运行时自动登记附件。
9. **黑色主题 UI**：`--bg: #0a0a0a` / `--primary: #fafafa`，Iconify SVG 内联渲染。

## 2 · 模式与默认模型

| Mode | 中文名 | 默认模型 | 备选 |
|---|---|---|---|
| `t2v` | 文生视频 | `happyhorse-1.0-t2v` | `wan2.6-t2v` |
| `i2v` | 图生视频（首帧） | `happyhorse-1.0-i2v` | `wan2.6-i2v` / `wan2.6-i2v-flash` / `wan2.7-i2v` |
| `i2v_end` | 首尾帧生视频 | `wan2.7-i2v` | — |
| `video_extend` | 视频续写 | `wan2.7-i2v` | — |
| `r2v` | 参考生视频（多角色） | `happyhorse-1.0-r2v` | `wan2.6-r2v` / `wan2.6-r2v-flash` |
| `video_edit` | 视频编辑 | `happyhorse-1.0-video-edit` | — |
| `photo_speak` | 照片说话 | `wan2.2-s2v` | — |
| `video_relip` | 视频换嘴 | `videoretalk` | — |
| `video_reface` | 视频换人 | `wan2.2-animate-mix` | — |
| `pose_drive` | 图生动作 | `wan2.2-animate-move` | — |
| `avatar_compose` | 数字人合成 | `wan2.7-image` → s2v | `wan2.7-image-pro` / `wan2.5-i2i-preview` |
| `long_video` | 长视频拼接 | 复用所选 i2v 模型 | — |

> HappyHorse 1.0 原生音视频同步，t2v / i2v / r2v / video_edit 不需要走 TTS step；其它 mode 仍走 cosyvoice / Edge-TTS。

## 3 · 价格速查（2026 年百炼公开价）

| 服务 | 价格 |
|---|---|
| HappyHorse 1.0 (720P / 1080P) | 0.90 / 1.60 元/秒 |
| wan2.2-s2v (480P / 720P) | 0.50 / 0.90 元/秒 |
| wan2.2-s2v-detect | 0.004 元/张 |
| videoretalk | 0.30 元/秒 |
| wan2.2-animate-mix (std / pro) | 0.60 / 1.20 元/秒 |
| wan2.2-animate-move (std / pro) | 0.40 / 0.60 元/秒 |
| wan2.7-image / image-pro | 0.20 / 0.50 元/张 |
| cosyvoice-v2 TTS | 0.20 元/万字 |

具体到 mode 的拆分见 [`happyhorse_models.py::PRICE_TABLE`](happyhorse_models.py)。

## 4 · 安装

happyhorse-video 跟随 OpenAkita 主仓发布，不需要额外 `pip install`。开发态：

```bash
cd plugins/happyhorse-video
py -3.11 -m pytest tests -q          # 跑全部单元 + workbench 协议测试
py -3.11 -m ruff check .             # 0 error
```

依赖懒加载：

| 包 | 何时需要 | 安装方式 |
|---|---|---|
| `oss2>=2.18` | 一切 DashScope 视频任务 | Settings → Python 依赖 → 一键安装；或 `pip install oss2` |
| `dashscope>=1.20` | CosyVoice TTS | 同上 |
| `edge-tts>=7.0` | Edge-TTS 引擎 | 同上 |
| `mutagen>=1.47` | 计算 TTS 音频时长用于 s2v 计费 | 同上 |
| `ffmpeg` | 长视频拼接 | Settings → FFmpeg → 一键安装（Windows winget / macOS brew / Linux apt） |

## 5 · 配置

打开 OpenAkita → 插件 → HappyHorse Studio → 进入插件 UI → **Settings** Tab：

| 字段 | 默认 | 说明 |
|---|---|---|
| `api_key` | 空 | 阿里云百炼控制台 → API Key |
| `base_url` | `https://dashscope.aliyuncs.com` | 国际版填 `https://dashscope-intl.aliyuncs.com` |
| `oss_endpoint` / `oss_bucket` / `oss_access_key_id` / `oss_access_key_secret` | 空 | OSS 必填 |
| `oss_path_prefix` | `happyhorse-video` | OSS object 前缀 |
| `default_model_<mode>` | 各 mode 的默认（见上表） | 创建任务不选时回落到这里 |
| `default_resolution` | `720P` | |
| `default_voice` | `longxiaochun_v2` | |
| `tts_engine` | `cosyvoice` | 可改 `edge` |
| `cost_threshold_cny` | 5.00 | 超过弹窗强确认 |
| `timeout_sec` / `max_retries` | 60 / 2 | HTTP 单次超时与重试 |
| `auto_archive` / `retention_days` | false / 30 | 过期任务清理 |

**API Key 热加载**：保存后下一次请求即生效，不需重启插件。

## 6 · 工具调用（LLM / 主聊天）

```
@hh_cost_preview mode=t2v duration=10 resolution=1080P
@hh_t2v prompt="海上日出，慢镜头" duration=10 resolution=1080P
@hh_i2v prompt="风吹动主角发丝" first_frame_url=https://... duration=8
@hh_r2v prompt="角色1对角色2说：你好" reference_urls='["https://...role1.mp4","https://...role2.mp4"]'
@hh_video_edit prompt="把背景换成赛博朋克城市" source_video_url=https://...
@hh_photo_speak image_url=... text="你好" voice_id=longxiaochun_v2
@hh_video_relip source_video_url=... audio_url=...
@hh_video_reface image_url=... source_video_url=... mode_pro=false
@hh_pose_drive image_url=... source_video_url=... mode_pro=false
@hh_avatar_compose image_url=... image_urls='["https://...scene.png"]' prompt="..." text="..."
@hh_long_video_create story="一只小猫的奇幻冒险" total_duration=30 mode=parallel
@hh_status task_id=tk_xxx
@hh_list limit=10
```

每个工具返回符合 OrgRuntime 工作台协议的 JSON，可直接被组织 runtime 登记为附件。

## 7 · 与组织模板联动

主仓注册了组织模板 [`happyhorse-video-studio`](../../src/openakita/orgs/templates.py)（**百炼 AIGC 视频创作工作室**）：制片人 → 编剧 → `tongyi-image` 工作台（出分镜图）→ 快乐马视频工作台（消费 `from_asset_ids` → 出成片）。

- 5 分钟烟测：[`USER_TEST_CASES.md`](USER_TEST_CASES.md)
- 完整端到端三入口测试（指挥台 / 主聊天 / IM）：[`docs/happyhorse-video-test-plan.md`](../../docs/happyhorse-video-test-plan.md)
- 单元 / workbench 协议 / smoke 测试：[`tests/`](tests/)（`pytest plugins/happyhorse-video/tests/`）

## 8 · 与其它视频插件的关系

| 插件 | 后端 | 主打 | 与本插件关系 |
|---|---|---|---|
| [`seedance-video`](../seedance-video/) | 火山引擎 Seedance | 文生 / 图生 / 多模态 / 编辑 / 续写 / 长视频 | 双轨并存。火山版本走 Ark API；本插件走百炼。 |
| [`avatar-studio`](../avatar-studio/) | 阿里云 DashScope (单数字人) | 5 数字人模式 + 多 backend | 双轨并存。avatar-studio 多 backend（DashScope / RunningHub / ComfyUI）；本插件单后端但融合视频 + 数字人 + TTS。 |
| [`tongyi-image`](../tongyi-image/) | 阿里云 DashScope | 文生图 / 图生图 | **上游协作**。出分镜图，再被本插件 `from_asset_ids` 消费成视频。 |

## 9 · 已知限制

- **DashScope 异步任务并发上限 = 1 / API Key**。同一 Key 同一时刻只能跑一个生成任务；submits 被 `Semaphore(1)` 串行化。
- **task_id 24 h 后过期**。超期 `query_task` 返回 `not_found`；本地 sqlite 仍可查 metadata。
- **HappyHorse 不支持的旧参数**：`with_audio` / `size` / `quality` / `fps` / `audio` — client 显式拒绝，避免送 DashScope 后晚失败。
- **Wan 2.6 走旧版协议**：用 `size: "1280*720"`（星号格式）而不是 `resolution: "720P"`；client 内部按 model 派发。
- **i18n 仅 zh / en**。

## 10 · License & Attribution

OpenAkita source code is licensed under AGPL-3.0-only. The plugin includes `OpenAkita` as `author` in `plugin.json` and `LICENSE` notices follow the upstream project. See top-level [`LICENSE`](../../LICENSE) and [`TRADEMARK.md`](../../TRADEMARK.md).
