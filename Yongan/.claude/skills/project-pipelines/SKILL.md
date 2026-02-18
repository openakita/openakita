---
description: "OpenAkita 构建与发布流程索引：CI 校验、Python 包发布、桌面端打包与 Release 发布"
---

# project-pipelines

- CI 验证 -> 配置文件: `.github/workflows/ci.yml` -> 关键步骤: lint、mypy、pytest、setup-center build、Rust check、构建产物上传
- Python 发布 -> 配置文件: `.github/workflows/release.yml` -> 关键步骤: 版本检查、`python -m build`、发布到 PyPI、上传 release 资产
- Desktop 发布 -> 配置文件: `.github/workflows/release.yml` -> 关键步骤: PyInstaller 打包 backend、Tauri 多平台构建、安装包收集上传
- Release 草稿与发布 -> 配置文件: `.github/workflows/release.yml` -> 关键步骤: 创建 draft release、按分支识别 stable/dev、最终发布
- Updater 清单更新 -> 配置文件: `.github/workflows/release.yml` + `scripts/generate_latest_json.py` -> 关键步骤: 生成并推送 `latest*.json`
- Dry-run 发布 -> 配置文件: `.github/workflows/release-dryrun.yml` -> 关键步骤: 预演发布流程与构建验证
