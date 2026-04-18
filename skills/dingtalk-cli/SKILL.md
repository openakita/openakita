---
name: openakita/skills@dingtalk-cli
description: "DingTalk Workspace CLI (dws) - officially open-sourced cross-platform CLI tool from DingTalk. Provides 86 commands across 12 products: Contact, Chat, Bot, Calendar, Todo, Approval, Attendance, Ding, Report, AITable, Workbench, DevDoc. Built in Go with zero-trust security architecture. Use when user wants to operate DingTalk resources."
license: Apache-2.0
metadata:
  author: DingTalk-Real-AI
  version: "1.0.8"
---

# DingTalk CLI Workspace CLI (dws)

钉钉官方开源的跨平台 CLI 工具，Go 语言开发。为人类用户和 AI Agent 场景同时设计，统一钉钉全套产品能力。

> 官方 GitHub: https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli
> 1500+ Stars | Apache-2.0 许可

## Installation

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/DingTalk-Real-AI/dingtalk-workspace-cli/main/scripts/install.sh | sh
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/DingTalk-Real-AI/dingtalk-workspace-cli/main/scripts/install.ps1 | iex
```

### 其他方式

```bash
# npm（需 Node.js）
npm install -g dingtalk-workspace-cli

# 预编译二进制：从 GitHub Releases 下载
# https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli/releases
```

## 认证

```bash
dws auth login            # 浏览器自动打开授权
dws auth login --device   # 无头环境（Docker、SSH、CI）
```

选择组织并授权即可。凭证使用 **PBKDF2 + AES-256-GCM** 加密存储。

### 自定义应用模式（CI/CD、ISV 集成）

```bash
dws auth login --client-id <your-app-key> --client-secret <your-app-secret>
```

## 产品覆盖（86 条命令，12 个产品）

| 产品 | 命令 | 子命令 | 说明 |
|------|------|--------|------|
| 通讯录 contact | 6 | user dept | 按姓名/手机号搜索、批量查询、部门、当前用户 |
| 聊天 chat | 10 | message group search | 群管理、成员管理、机器人消息、webhook |
| 机器人 bot | 6 | bot group message search | 机器人创建/搜索、群/单聊消息、webhook、消息撤回 |
| 日历 calendar | 13 | event room participant busy | 日程 CRUD、会议室预订、忙闲查询、参会人管理 |
| 待办 todo | 6 | task | 创建、列表、更新、完成、详情、删除 |
| 审批 approval | 9 | approval | 同意/拒绝/撤回、待办任务、发起实例、流程列表 |
| 考勤 attendance | 4 | record shift summary rules | 打卡记录、排班、考勤汇总、组规则 |
| 钉 ding | 2 | message | 发送/撤回 DING 消息 |
| 报表 report | 7 | create list detail template stats sent | 创建报表、收发列表、模板、统计 |
| AI 表格 aitable | 20 | base table record field attachment template | 完整增删改查 |
| 工作台 workbench | 2 | app | 批量查询应用详情 |
| 开发文档 devdoc | 1 | article | 搜索平台文档和错误码 |

## AI Agent 特性

### 智能输入纠正

内置管道引擎自动修正 AI 模型常见的参数错误：

| Agent 输出 | dws 自动修正为 |
|------------|---------------|
| --userId | --user-id |
| --limit100 | --limit 100 |
| --tabel-id | --table-id |
| --USER-ID | --user-id |

### Schema 自省

Agent 无需预置知识，可动态发现能力：

```bash
# 发现所有可用产品
dws schema --jq '.products[] | {id, tool_count: (.tools | length)}'

# 查看目标工具的参数 Schema
dws schema aitable.query_records --jq '.tool.parameters'
```

### jq 过滤与字段选择

```bash
# 精确过滤输出，减少 token 消耗
dws aitable record query --base-id BASE_ID --table-id TABLE_ID --jq '.invocation.params'
```

### 管道与文件输入

```bash
# 从文件读取消息体
dws chat message send-by-bot --robot-code BOT_CODE --group GROUP_ID \
  --title "周报" --text @report.md

# 从 stdin 读取
cat report.md | dws chat message send-by-bot --robot-code BOT_CODE --group GROUP_ID \
  --title "周报"
```

## Agent Skills 系统

仓库自带完整 Agent Skill 系统（skills/ 目录）：

```bash
# 安装 skills 到当前项目
curl -fsSL https://raw.githubusercontent.com/DingTalk-Real-AI/dingtalk-workspace-cli/main/scripts/install-skills.sh | sh
```

### 包含内容

| 组件 | 路径 | 说明 |
|------|------|------|
| Master Skill | SKILL.md | 意图路由、决策树、安全规则、错误处理 |
| 产品参考 | references/products/*.md | 各产品命令参考 |
| 意图指南 | references/intent-guide.md | 易混淆场景消歧 |
| 全局参考 | references/global-reference.md | 认证、输出格式、全局 flags |
| 错误码 | references/error-codes.md | 错误码 + 调试流程 |
| 恢复指南 | references/recovery-guide.md | RECOVERY_EVENT_ID 处理 |
| 预置脚本 | scripts/*.py | 13 个批量操作 Python 脚本 |

### 预置 Python 脚本

| 脚本 | 功能 |
|------|------|
| calendar_schedule_meeting.py | 创建日程 + 添加参会人 + 查找并预订会议室 |
| calendar_free_slot_finder.py | 多人共同空闲时段查找，推荐最佳会议时间 |
| calendar_today_agenda.py | 查看今日/明日/本周日程 |
| import_records.py | 从 CSV/JSON 批量导入 AI 表格记录 |
| bulk_add_fields.py | 批量添加 AI 表格字段 |
| todo_batch_create.py | 从 JSON 批量创建待办（含优先级、截止日期） |
| todo_daily_summary.py | 汇总今日/本周未完成待办 |
| todo_overdue_check.py | 扫描逾期待办 |
| contact_dept_members.py | 按部门名称搜索并列出全部成员 |
| attendance_my_record.py | 查看我的考勤记录 |
| attendance_team_shift.py | 查询团队排班与考勤统计 |
| report_inbox_today.py | 查看今日收到的报表详情 |

## 安全设计

零信任架构：凭证不落盘、Token 不离开可信域、权限不超越授权、操作不逃过审计。

| 机制 | 详情 |
|------|------|
| 加密令牌存储 | PBKDF2 + AES-256-GCM，基于设备物理 MAC 地址加密 |
| 输入安全 | 路径遍历保护、CRLF 注入拦截、Unicode 视觉欺骗过滤 |
| 域名白名单 | DWS_TRUSTED_DOMAINS 默认 *.dingtalk.com，bearer token 不发往非白名单域 |
| HTTPS 强制 | 所有请求要求 TLS |
| Dry-run 预览 | --dry-run 展示调用参数但不执行，防止误操作 |
| 零凭证持久化 | Client ID / Secret 仅在内存中使用 |

## 升级

```bash
dws upgrade                    # 交互式升级到最新版
dws upgrade --check            # 检查新版本
dws upgrade --rollback         # 回滚到上一版本
```

## Quick Start

```bash
dws contact user search --keyword "engineering"     # 搜索联系人
dws calendar event list                              # 列出日历事件
dws todo task create --title "季度报告" --executors "<userId>"  # 创建待办
dws todo task list --dry-run                          # 预览（不执行）
```

## Pre-built Scripts

### scripts/dws_setup.py
钉钉 CLI 安装和认证脚本。

```bash
python3 scripts/dws_setup.py install
python3 scripts/dws_setup.py auth
python3 scripts/dws_setup.py status
```

### scripts/dws_quick.py
钉钉常用操作快捷脚本。

```bash
python3 scripts/dws_quick.py send --conversation-id xxx --content "消息内容"
python3 scripts/dws_quick.py contacts
python3 scripts/dws_quick.py calendar
python3 scripts/dws_quick.py todo --title "待办事项"
```
