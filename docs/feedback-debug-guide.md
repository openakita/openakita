# 用户反馈 Issue 诊断指南

本文档指导如何从 GitHub Issue 中获取用户反馈的诊断包、分析并修复问题、以及完成后续的状态管理和数据清理。

---

## 前置配置

### 1. 安装依赖

```bash
pip install oss2
```

### 2. 安装 GitHub CLI

```bash
# Windows
winget install GitHub.cli

# macOS
brew install gh

# 首次使用需要登录
gh auth login
```

### 3. 配置 OSS 凭证

创建文件 `~/.openakita/feedback.env`：

```ini
OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
OSS_BUCKET=openakita-feedback
OSS_ACCESS_KEY_ID=__填入你的AccessKeyId__
OSS_ACCESS_KEY_SECRET=__填入你的AccessKeySecret__
```

> 凭证来自阿里云 RAM 用户，需拥有 `openakita-feedback` Bucket 的读写权限。

---

## Issue 状态标签体系

每个反馈 Issue 通过 `status:*` 标签追踪生命周期：

| 标签 | 颜色 | 含义 |
|------|------|------|
| `status:open` | 灰色 | 新建，待处理（自动创建时附带） |
| `status:need-info` | 紫色 | 需要用户提供更多信息 |
| `status:resolved` | 绿色 | 已修复并发版 |
| `status:wontfix` | 白色 | 不会修复（非 Bug / 预期行为 / 无法复现） |

**规则**：一个 Issue 同一时间只保留一个 `status:*` 标签，切换状态时需移除旧标签。

---

## 完整诊断流程

### 第一步：获取 Issue 信息

```bash
gh issue view <issue_number> -R openakita/openakita
```

从 Issue body 中提取关键信息：

| 字段 | 示例 | 说明 |
|------|------|------|
| Report ID | `0e9483116018` | 12 位十六进制，用于定位 OSS 文件 |
| Type | `bug` / `feature` | 反馈类型 |
| System Info | `OS: Windows 10 AMD64 \| Python: 3.11.9 \| OpenAkita: 1.26.1` | 用户环境 |
| Description | 用户原文 | 问题描述 |

### 第二步：下载诊断包

```bash
python scripts/feedback.py download <report_id> --output ./feedback-downloads
```

下载完成后得到：

- `<report_id>.zip` — 诊断 ZIP 包
- `<report_id>_metadata.json` — 报告元数据

### 第三步：解压

```bash
cd feedback-downloads
unzip <report_id>.zip -d <report_id>

# Windows PowerShell:
Expand-Archive <report_id>.zip -DestinationPath <report_id>
```

### 第四步：分析内容

ZIP 内部结构：

```
<report_id>/
├── metadata.json          # 报告元数据（report_id, type, title, description, system_info）
├── images/                # 用户截图
├── logs/
│   ├── openakita.log      # 主日志（最后 1MB）
│   └── error.log          # 错误日志（如果有）
└── llm_debug/             # 最近 50 条 LLM 调用调试记录（JSON）
```

**分析优先级：**

1. **`metadata.json`** — 了解用户描述、系统环境、OpenAkita 版本
2. **`logs/openakita.log`** — 搜索 `ERROR`、`CRITICAL`、`Traceback`，关注时间线
3. **`logs/error.log`** — 集中查看错误堆栈（如果存在）
4. **`llm_debug/`** — 按时间戳排序，检查 API Key 有效性、模型名、状态码、超时
5. **`images/`** — 用户截图可能直接展示 UI 错误

### 第五步：修复问题

根据分析结果在代码中定位并修复。

---

## 修复后操作

Bug 修复并合并/发版后，依次执行以下操作。

### 1. 更新 Issue 状态标签

移除旧标签，添加新标签：

```bash
# 已修复
gh issue edit <issue_number> -R openakita/openakita --remove-label "status:open" --add-label "status:resolved"

# 不会修复
gh issue edit <issue_number> -R openakita/openakita --remove-label "status:open" --add-label "status:wontfix"

# 需要更多信息
gh issue edit <issue_number> -R openakita/openakita --remove-label "status:open" --add-label "status:need-info"
```

### 2. 添加处理结论评论

```bash
gh issue comment <issue_number> -R openakita/openakita --body "修复说明..."
```

评论模板：

```markdown
## 处理结论

**状态**：已修复
**修复版本**：v1.25.12
**原因**：简要说明根因
**修复内容**：简要说明做了什么改动

---
_此问题已在 v1.25.12 中修复，请升级后验证。_
```

### 3. 关闭 Issue

```bash
gh issue close <issue_number> -R openakita/openakita --reason completed

# 如果是 wontfix
gh issue close <issue_number> -R openakita/openakita --reason "not planned"
```

### 4. 清理 OSS 诊断包（可选）

Issue 关闭后，对应的 OSS 诊断包可以删除以节省存储：

```bash
# 删除单个报告
python scripts/feedback.py delete <report_id>

# 需要确认，或加 --yes 跳过
python scripts/feedback.py delete <report_id> --yes
```

### 5. 清理本地下载文件（可选）

```bash
# 删除本地解压的诊断文件
rm -rf feedback-downloads/<report_id>*
```

---

## 定期维护

### 批量清理过期 OSS 数据

建议每月执行一次，删除超过 90 天的旧诊断包：

```bash
# 查看将要删除的内容（需确认）
python scripts/feedback.py cleanup --days 90

# 跳过确认直接删除
python scripts/feedback.py cleanup --days 90 --yes
```

### 查看待处理 Issue

```bash
# 列出所有 status:open 的反馈 Issue
gh issue list -R openakita/openakita --label "status:open" --label "source:feedback"

# 列出需要更多信息的 Issue
gh issue list -R openakita/openakita --label "status:need-info"
```

### OSS 报告统计

```bash
python scripts/feedback.py list --days 7
python scripts/feedback.py stats --days 30
```

---

## OSS 存储结构

```
openakita-feedback/                    # Bucket
└── feedback/
    └── {YYYY-MM-DD}/                  # UTC 日期
        └── {report_id}/
            ├── report.zip             # 诊断包
            └── metadata.json          # 元数据（含 GitHub Issue URL）
```

- 日期为 **UTC 时区**
- report_id 为 `uuid4().hex[:12]`（12 位十六进制字符）

---

## 给 AI 助手的操作说明

如果你是 AI 助手（如 Cursor Agent），请严格按以下步骤操作。

### 收到 Issue 链接时

1. `gh issue view <number> -R openakita/openakita --json title,body,labels` 获取内容
2. 从 body 中用正则提取 Report ID（`Report ID:` 后的 12 位 hex）
3. `python scripts/feedback.py download <report_id> --output ./feedback-downloads` 下载
4. 解压后用 Read 工具逐一分析 `metadata.json`、`logs/`、`llm_debug/`、`images/`
5. 在代码中定位问题并修复

### 用户说"结单"或"关闭 Issue"时

依次执行：

1. 更新标签：`gh issue edit <number> -R openakita/openakita --remove-label "status:open" --add-label "status:resolved"`
2. 添加结论评论：`gh issue comment <number> -R openakita/openakita --body "..."`
3. 关闭 Issue：`gh issue close <number> -R openakita/openakita --reason completed`
4. 如用户要求清理 OSS：`python scripts/feedback.py delete <report_id> --yes`
5. 清理本地文件：删除 `feedback-downloads/<report_id>*`

### 用户说"定期清理"时

1. `python scripts/feedback.py cleanup --days 90` 清理过期 OSS 数据
2. `gh issue list -R openakita/openakita --label "status:open" --label "source:feedback"` 查看未处理 Issue
