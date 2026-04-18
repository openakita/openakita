---
name: platform-guide
description: "OpenAkita Platform guide for searching and installing Agents from Agent Hub and Skills from Skill Store. Use when user asks to find, browse, or install Agents or Skills from the platform."
system: true
handler: system
tool-name: platform_guide
category: Platform
---

# OpenAkita Platform Guide — AI 操作手册

> **核心原则**：当用户需要某种能力但本地没有时，你应该 **主动搜索平台**。

## 什么时候该用平台？

- 用户说"帮我找一个 XX Agent / 技能 / Skill"
- 用户需要某种能力但本地没有对应的技能
- 用户说"有没有现成的"、"市场上有吗"、"安装一个"
- 用户提到 Agent Hub、Skill Store、OpenAkita 平台

**注意**：平台是 **可选扩展**，离线时所有本地功能不受影响。

---

## Agent 安装三步走

当用户想从平台获取 Agent 时：

**第 1 步 — 搜索**
```
search_hub_agents(query="客服", category="customer_service")
```
- 查看返回的 Agent 列表，注意每个 Agent 的 `id`

**第 2 步 — 预览（推荐）**
```
get_hub_agent_detail(agent_id="the-agent-id")
```
- 查看描述、技能列表、评分、下载量

**第 3 步 — 安装**
```
install_hub_agent(agent_id="the-agent-id")
```
- 系统自动：下载包 → 解压技能 → 版本去重 → 注册 Agent → 热重载
- 安装后 Agent **立刻可用**

### Installation时发生了什么？
1. 下载 `.akita-agent` 包
2. 打包的技能 → `skills/custom/`（版本去重：本地有更新版就跳过）
3. 外部依赖技能 → 从原始 GitHub 仓库拉取到 `skills/community/`
4. 每个技能写入 `.openakita-origin.json` 追踪来源
5. Agent 配置注册到本地 → 技能自动重载

---

## Skill 安装三步走

当用户想从平台获取 Skill 时：

**第 1 步 — 搜索**
```
search_store_skills(query="翻译", trust_level="official")
```
- 注意 `trustLevel`：official（官方）> certified（认证）> community（社区）

**第 2 步 — 预览（推荐）**
```
get_store_skill_detail(skill_id="the-skill-id")
```
- 查看描述、开源协议、GitHub Stars、评分

**第 3 步 — 安装**
```
install_store_skill(skill_id="the-skill-id")
```
- 从原始 GitHub 仓库直接克隆（非平台重新分发）
- 安装后技能 **立刻可用**

---

## 本地 Agent 导入导出（不需要平台）

```
list_exportable_agents()                                    # 查看可导出的
export_agent(profile_id="my-agent", version="1.0.0")        # 导出
inspect_agent_package(package_path="xxx.akita-agent")       # 预览包
import_agent(package_path="xxx.akita-agent")                # 导入
```

---

## 版本去重策略

| 场景 | 行为 |
|------|------|
| 本地有同名 skill 且版本 >= 新包中的版本 | **跳过**（保留本地更新版） |
| 新包中版本 > 本地版本 | **覆盖**（升级到新版） |
| 任一方无版本信息 | **覆盖**（安全起见） |

---

## 平台离线时的替代方案

| 需求 | 替代方式 |
|------|----------|
| 找 Agent | 用 `.akita-agent` 文件导入 |
| 找 Skill | `install_skill` 从 GitHub 直接装；Setup Center 的 skills.sh 市场 |
| 共享 Agent | `export_agent` 导出文件分享 |

---

## 全部平台工具速查

| 工具 | 用途 |
|------|------|
| `search_hub_agents` | 搜索平台上的 Agent |
| `get_hub_agent_detail` | 查看 Agent 详情 |
| `install_hub_agent` | 安装 Agent 到本地 |
| `search_store_skills` | 搜索平台上的 Skill |
| `get_store_skill_detail` | 查看 Skill 详情 |
| `install_store_skill` | 安装 Skill 到本地 |
| `submit_skill_repo` | 提交 GitHub 仓库为新 Skill |
| `export_agent` | 导出本地 Agent |
| `import_agent` | 导入 Agent 包 |
| `list_exportable_agents` | 列出可导出的 Agent |
| `inspect_agent_package` | 预览 Agent 包内容 |

---

## 许可证合规

- 平台上的 Skill 从 **原始开源仓库** 拉取，非平台重新分发
- Agent 包可能含 `LICENSE-3RD-PARTY.md` 列出外部依赖
- 安装前应告知用户相关许可证
- 侵权联系：zacon365@gmail.com
