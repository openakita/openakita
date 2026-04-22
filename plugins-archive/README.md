# plugins-archive/ — 已下沉的非一等公民插件

本目录存放**曾经的一等公民、现已降级**的插件源码，配套抽出的共享 contrib helpers 在 `_shared/`。

## 现状

`openakita-plugin-sdk` 已主动收缩 API surface（0.6.0 → 0.7.0），回归到 0.2 时代"最小插件壳子"的原始定位。
这些插件按当时 SDK 0.6.0 的 contrib API 写成，迁出时已把它们对 SDK 的依赖切到本目录的 `_shared/`，因此**仍可作为参考实现完整阅读和单测**，但：

- **不再被 host 自动加载**。host 的 `PluginManager` 只扫 `data/plugins/`，本目录不在其中。
- **不再列入主线 CI**。
- **不接受新功能 / bug 修复**——issue 默认 `wontfix: archived`。
- **不承诺与 main 分支的 SDK 同步升级**。

## 如何启用某个 archive 插件

```bash
# 把整个插件目录 + _shared/ 的相关模块复制（或软链）到 data/plugins/
cp -r plugins-archive/highlight-cutter data/plugins/
cp -r plugins-archive/_shared data/plugins/highlight-cutter/_shared
```

之后插件按原有 import 路径 `from _shared.task_manager import BaseTaskManager` 即可工作。
host `PluginManager` 在下次 `load_all()` 时会自动发现并加载它。

## 目录清单

19 个插件：

`avatar-speaker` · `bgm-mixer` · `bgm-suggester` · `dub-it` · `ecommerce-image` · `highlight-cutter` · `image-edit` · `local-sd-flux` · `poster-maker` · `ppt-to-video` · `shorts-batch` · `smart-poster-grid` · `storyboard` · `subtitle-maker` · `transcribe-archive` · `tts-studio` · `video-bg-remove` · `video-color-grade` · `video-translator`

11 个共享模块（`_shared/`）：

`task_manager` · `vendor_client` · `errors` · `upload_preview` · `storage_stats` · `ui_events` · `render_pipeline` · `llm_json_parser` · `ffmpeg` · `verification` · `tts/` (子包) · `asr/` (子包)

## 一等公民

只剩 `plugins/tongyi-image/` 和 `plugins/seedance-video/`——它们的 contrib 依赖已 inline 进各自插件目录（`tongyi_inline/` / `seedance_inline/`），与 SDK / archive 完全解耦。

详见仓库根 `CHANGELOG.md` 的 **0.7.0 — SDK 回归原始定位** 条目。
