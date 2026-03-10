# Release Playbook — OpenAkita 发布操作手册

> **面向 Agent 的交互式操作手册。**
> Agent 阅读此文档后，应先向用户提问选择哪种操作模式，再收集该模式所需的参数，确认后自动执行全部步骤。

---

## 前置知识

### 仓库

| 仓库 | 用途 |
|------|------|
| `openakita/openakita` | 主仓库（后端 + Desktop + 移动端） |
| `openakita/openakita-web` | 官网（下载页） |

### 分支约定

| 分支 | 角色 | 说明 |
|------|------|------|
| `main` | 开发分支 | 日常开发，打的 tag 自动标记为 **dev（开发版）** |
| `v{X}.{Y}.x` | 版本分支 | 如 `v1.24.x`、`v1.25.x`，渠道由配置文件决定 |

### 渠道配置

`.github/release-channels.json` 控制 tag minor 版本 → 渠道映射：

```json
{
  "release": "1.24",
  "pre-release": "1.25"
}
```

推断规则：tag `v1.24.7` → minor `1.24` → 匹配 `release` → **稳定版**；
tag `v1.25.10` → minor `1.25` → 匹配 `pre-release` → **抢先版**；
其余 → **开发版**。

### 版本号管理

`scripts/version.py` 是单一版本源管理工具，`VERSION` 文件为唯一版本来源。

```bash
python scripts/version.py set 1.25.10   # 写入 VERSION 并同步到所有文件
python scripts/version.py check          # 校验所有文件版本是否一致
```

同步范围：`VERSION` → `pyproject.toml` → `package.json` → `tauri.conf.json` → `Cargo.toml` → `Cargo.lock` → `_bundled_version.txt` → `build.gradle`

### 工作流

| 工作流 | 作用 |
|--------|------|
| `release.yml` | 构建 Desktop 安装包（Win/macOS/Linux），上传到 GitHub Release（Draft） |
| `mobile.yml` | 构建移动端（Android APK + iOS IPA），推 tag 时自动触发或手动触发 |
| `publish-release.yml` | 发布 Release + 生成 manifest + 上传安装包和 manifest 到阿里云 OSS |
| `backfill-oss.yml` | 批量回填历史版本 manifest 到 OSS |

### 环境注意事项

- **Shell**：Windows PowerShell，`&&` 不可用，命令必须分步执行。
- **Python**：系统 `python` 为 2.7，需使用 `r:\OpenAkita\.venv\Scripts\python.exe`。
- **文件同步**：跨分支复制文件用 `git checkout {branch} -- {file}`，**禁止**用 `git show > file`（PowerShell 会写 UTF-16）。

---

## Agent 交互流程

**Agent 应按以下步骤与用户交互：**

1. 向用户展示可用模式列表（A / B / C / D）
2. 用户选择模式后，按模式定义收集所需参数
3. 向用户确认参数和即将执行的操作
4. 用户确认后，按步骤顺序执行，每步报告结果
5. 全部完成后进行验证并汇报

---

## 模式 A：切换渠道配置

### 场景

调整哪个版本线是稳定版、哪个是抢先版。例如将 `v1.25.x` 升级为稳定版，`v1.26.x` 作为新的抢先版。

### Agent 需要向用户收集的参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `RELEASE_LINE` | 新的稳定版版本线（major.minor） | `1.25` |
| `PRERELEASE_LINE` | 新的抢先版版本线（major.minor） | `1.26` |

> **Agent 提问示例：**
> "请提供：
> 1. 稳定版版本线（如 `1.24`、`1.25`）
> 2. 抢先版版本线（如 `1.25`、`1.26`）"

### 执行步骤

```
STEP 1 — 检查分支是否存在
  git branch -r --list "origin/v{RELEASE_LINE}.x"
  git branch -r --list "origin/v{PRERELEASE_LINE}.x"
  → 如果抢先版分支不存在，提示用户是否需要从 main 创建

STEP 2 — 修改配置文件
  编辑 .github/release-channels.json 为：
  {
    "release": "{RELEASE_LINE}",
    "pre-release": "{PRERELEASE_LINE}"
  }

STEP 3 — 提交并推送到 main
  git checkout main
  git pull origin main
  git add .github/release-channels.json
  git commit -m "chore: set release={RELEASE_LINE}, pre-release={PRERELEASE_LINE}"
  git push origin main

STEP 4 — 同步到版本分支
  对 v{RELEASE_LINE}.x 和 v{PRERELEASE_LINE}.x 分支各执行：
    git checkout v{X}.x
    git pull origin v{X}.x
    git checkout main -- .github/release-channels.json
    git add .github/release-channels.json
    git commit -m "chore: sync release-channels.json"
    git push origin v{X}.x

STEP 5 — 回填 manifest 使新渠道生效
  gh workflow run backfill-oss.yml --repo openakita/openakita --ref main -f upload_binaries=false -f dry_run=false

STEP 6 — 验证
  curl OSS 上的 release.json / pre-release.json，确认 version 和 channel 正确
```

---

## 模式 B：发布新版本

### 场景

在指定分支上发布一个新版本。自动修改版本号 → 提交 → 打 tag → 推送 → 触发构建 → 触发发布。

### Agent 需要向用户收集的参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `VERSION` | 要发布的版本号（带或不带 v 前缀均可） | `v1.26.1` 或 `1.26.1` |
| `BRANCH` | 版本所在分支 | `v1.25.x`（可从版本号推断，如 `1.25.x`） |
| `INCLUDE_MOBILE` | 是否需要移动端构建 | `是` / `否`（默认否） |

> **Agent 提问示例：**
> "请提供：
> 1. 版本号（如 `v1.26.1`）
> 2. 分支（如 `v1.26.x`，留空则自动推断）
> 3. 是否包含移动端构建？（默认否）"

### 分支推断规则

版本号 `v1.26.1` → 分支 `v1.26.x`（取 major.minor + `.x`）。
如果用户未指定分支，Agent 应按此规则推断并向用户确认。

### 执行步骤

```
# 规范化参数
TAG = "v" + VERSION（去掉 v 前缀后再加，确保格式统一）
BARE_VERSION = VERSION 去掉 v 前缀（如 1.26.1）

STEP 1 — 切换到版本分支并拉取最新
  git checkout {BRANCH}
  git pull origin {BRANCH}

STEP 2 — 使用 version.py 修改版本号
  r:\OpenAkita\.venv\Scripts\python.exe scripts/version.py set {BARE_VERSION}
  → 输出会显示所有被修改的文件

STEP 3 — 提交版本号变更
  git add -A
  git commit -m "chore: bump version to {BARE_VERSION}"

STEP 4 — 打 tag 并推送
  git tag {TAG}
  git push origin {BRANCH}
  git push origin {TAG}

STEP 5 — 触发 Desktop 构建
  gh workflow run release.yml --repo openakita/openakita --ref {BRANCH} -f tag={TAG}

STEP 5b —（如果 INCLUDE_MOBILE=是）触发移动端构建
  gh workflow run mobile.yml --repo openakita/openakita --ref {BRANCH} -f tag={TAG}

STEP 6 — 等待构建完成（轮询检查，约 15-30 分钟）
  反复执行 gh run list --repo openakita/openakita --workflow=release.yml --limit 1
  直到状态为 ✓ completed
  如果 INCLUDE_MOBILE=是，也要等 mobile.yml 完成

STEP 7 — 触发发布（自动推断渠道）
  gh workflow run publish-release.yml --repo openakita/openakita --ref {BRANCH} -f tag={TAG}
  → 等待完成

STEP 8 — 验证
  gh release view {TAG} --repo openakita/openakita
  检查 OSS manifest 是否更新（根据渠道检查对应 JSON）
```

### 渠道自动推断参考

| TAG | minor | 配置匹配 | 渠道 | OSS 文件 |
|-----|-------|---------|------|---------|
| v1.24.7 | 1.24 | `"release": "1.24"` | 稳定版 | release.json + latest.json |
| v1.25.10 | 1.25 | `"pre-release": "1.25"` | 抢先版 | pre-release.json |
| v1.26.1 | 1.26 | 无匹配 | 开发版 | dev.json |

---

## 模式 C：覆盖指定版本（重新打包已有 tag）

### 场景

某个 tag 对应的版本需要重新打包（修了 bug 后想覆盖原 tag），在指定分支上强制将已有 tag 移动到最新提交，然后重新构建发布。**不会创建新 tag。**

### Agent 需要向用户收集的参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `TAG` | 要覆盖的 tag（必须已存在） | `v1.25.9` |
| `BRANCH` | 分支（tag 将指向该分支最新提交） | `v1.25.x` |
| `INCLUDE_MOBILE` | 是否需要移动端构建 | `是` / `否`（默认否） |

> **Agent 提问示例：**
> "请提供：
> 1. 要覆盖的 tag（如 `v1.25.9`，必须是已存在的 tag）
> 2. 分支（如 `v1.25.x`）
> 3. 是否包含移动端构建？（默认否）"

### 执行步骤

```
STEP 1 — 验证 tag 已存在
  git ls-remote --tags origin {TAG}
  → 如果不存在，报错并终止："tag {TAG} 不存在，请使用模式 B 发布新版本"

STEP 2 — 切换到分支并拉取最新
  git checkout {BRANCH}
  git pull origin {BRANCH}

STEP 3 — 确保版本号一致
  r:\OpenAkita\.venv\Scripts\python.exe scripts/version.py check --expected {TAG}
  → 如果不一致，先执行 version.py set 并提交：
    r:\OpenAkita\.venv\Scripts\python.exe scripts/version.py set {BARE_VERSION}
    git add -A
    git commit -m "chore: align version to {BARE_VERSION}"
    git push origin {BRANCH}

STEP 4 — 强制移动 tag 到分支最新提交
  git tag -f {TAG}
  git push origin {TAG} --force

STEP 5 — 删除 GitHub 上的旧 Release（如果存在），让构建工作流重新创建 Draft
  gh release delete {TAG} --repo openakita/openakita --yes --cleanup-tag=false
  （如果 release 不存在则忽略错误）

STEP 6 — 触发 Desktop 构建
  gh workflow run release.yml --repo openakita/openakita --ref {BRANCH} -f tag={TAG}

STEP 6b —（如果 INCLUDE_MOBILE=是）触发移动端构建
  gh workflow run mobile.yml --repo openakita/openakita --ref {BRANCH} -f tag={TAG}

STEP 7 — 等待构建完成（轮询，约 15-30 分钟）

STEP 8 — 触发发布（强制覆盖 manifest）
  gh workflow run publish-release.yml --repo openakita/openakita --ref {BRANCH} -f tag={TAG} -f force_update=true

STEP 9 — 验证
  gh release view {TAG} --repo openakita/openakita
  检查 OSS manifest 版本和日期是否已更新
```

---

## 模式 D：回填历史 manifest 到 OSS

### 场景

需要重建 OSS 上的全部 manifest 数据。如渠道配置变更后、manifest 格式升级后、或 OSS 数据丢失时使用。

### Agent 需要向用户收集的参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `UPLOAD_BINARIES` | 是否同时上传安装包到 OSS | `是` / `否`（默认否） |
| `MIN_VERSION` | 最低版本号过滤（可选） | `1.20.0` 或留空 |

> **Agent 提问示例：**
> "请确认：
> 1. 是否同时上传安装包？（默认否，仅上传 manifest）
> 2. 是否需要过滤最低版本号？（如 `1.20.0`，留空则处理全部版本）"

### 执行步骤

```
STEP 1 — 触发回填工作流
  gh workflow run backfill-oss.yml \
    --repo openakita/openakita \
    --ref main \
    -f upload_binaries={true|false} \
    -f min_version={MIN_VERSION 或空} \
    -f dry_run=false

STEP 2 — 监控执行
  仅 manifest：约 1 分钟完成
  含安装包：可能需要 2-3 小时（67 个版本 × 多平台安装包）

  gh run list --repo openakita/openakita --workflow=backfill-oss.yml --limit 1
  → 反复轮询直到完成

STEP 3 — 验证
  检查 OSS 上的 release.json / pre-release.json / dev.json / versions.json
  确认各渠道最新版本号正确
```

---

## 附录：验证命令

Agent 在任何模式执行完毕后，都应运行以下验证：

```powershell
# 检查 OSS manifest
$channels = @("release", "pre-release", "dev", "latest")
foreach ($ch in $channels) {
  try {
    $r = Invoke-WebRequest -Uri "https://dl-openakita.fzstack.com/api/$ch.json" -UseBasicParsing -ErrorAction Stop
    $j = $r.Content | ConvertFrom-Json
    Write-Host "$ch.json : v$($j.version) channel=$($j.channel) date=$($j.pub_date.Substring(0,10))"
  } catch { Write-Host "$ch.json : NOT FOUND" }
}

# 检查 versions.json
$r = Invoke-WebRequest -Uri "https://dl-openakita.fzstack.com/api/versions.json" -UseBasicParsing
$j = $r.Content | ConvertFrom-Json
Write-Host "versions.json: release=$($j.release.Count) pre_release=$($j.pre_release.Count) dev=$($j.dev.Count)"

# 检查 GitHub 最新 Release
gh release list --repo openakita/openakita --limit 5

# 检查渠道配置
Get-Content .github/release-channels.json
```
