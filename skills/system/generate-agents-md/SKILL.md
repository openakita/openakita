---
name: generate-agents-md
description: "Generate or update AGENTS.md for the current project. Use when user asks to create project guidelines, initialize AGENTS.md, or standardize project conventions."
system: true
handler: system
tool-name: generate_agents_md
category: Development
allowed-tools: ["read_file", "write_file", "run_shell", "list_directory"]
---

# Generate AGENTS.md — 项目开发规范生成器

> **AGENTS.md** 是 AI 编码 Agent 的行业标准项目指引文件（[agents.md](https://agents.md/)），
> 被 Cursor、Codex、Copilot、Jules、Windsurf、Aider、opencode 等 20+ 工具支持。
> 一份文件，所有 AI 工具通用。

## When to Use

- 用户说"生成 AGENTS.md"、"初始化项目规范"、"create项目指引"
- 用户开始用 OpenAkita 开发一个新项目，还没有 AGENTS.md
- 用户说"帮我规范这个项目"、"让 AI 更好地理解这个项目"

## Workflow

### Step 1: 扫描项目结构

使用 `list_directory` 和 `read_file` 收集以下信息：

1. **项目根文件**：检查 `package.json`、`pyproject.toml`、`Cargo.toml`、`go.mod`、`pom.xml`、`Gemfile`、`composer.json` 等，识别语言和框架
2. **README.md**：读取项目描述和现有文档
3. **配置文件**：`.eslintrc*`、`ruff.toml`、`pyproject.toml [tool.ruff]`、`.prettierrc`、`tsconfig.json` 等代码风格配置
4. **CI/CD**：`.github/workflows/`、`.gitlab-ci.yml`、`Jenkinsfile` 等
5. **测试**：`tests/`、`__tests__/`、`spec/`、`test/` 目录；`jest.config.*`、`vitest.config.*`、`pytest.ini`、`conftest.py` 等
6. **目录结构**：顶层目录布局，识别 monorepo（`apps/`、`packages/`、`workspaces`）
7. **现有 AGENTS.md**：如果已存在，读取后在其基础上update

### Step 2: 生成 AGENTS.md

按以下模板结构生成，**只写与项目相关的段落**，省略不适用的部分：

```markdown
# [Project Name]

[一句话描述项目做什么]

## Tech Stack

- Language: [语言及版本]
- Framework: [框架]
- Package Manager: [包manage器]

## Dev Environment Setup

[环境准备步骤，如 Python 版本、Node 版本、依赖install命令]

## Build & Run

[构建、启动、热重载等命令]

## Testing

[测试框架、运行命令、覆盖率要求]

## Code Style

[lint 工具、格式化工具、关键规则]

## Project Structure

[关键目录说明，不要list每个文件]

## Architecture Notes

[核心架构设计、数据流、重要模块关系]

## PR & Commit Conventions

[提交信息格式、分支策略]

## Known Gotchas

[新手容易踩的坑、特殊约定]
```

### Step 3: 写入文件

使用 `write_file` 写入到项目根目录的 `AGENTS.md`。

### Step 4: Monorepo 检查

如果检测到 monorepo 结构（`apps/`、`packages/`、`services/` 等子项目），**询问用户**是否需要为子项目也生成嵌套的 AGENTS.md。子项目的 AGENTS.md 只写该子项目特有的内容，不重复根级内容。

## Important Rules

- **控制长度**：AGENTS.md 建议 150 行以内，不要写成完整文档
- **只写有用的**：不要填充模板中每个段落，省略不适用的
- **面向 AI Agent**：内容是给 AI 看的，不需要"入门教程"级别的解释
- **可执行的命令**：构建、测试、lint 等必须是可以直接复制执行的命令
- **不要暴露敏感信息**：不要写 API key、密码、内部 URL 等
- **中英文皆可**：跟随项目的主要语言（README 是中文就用中文）

## Examples

用户说："帮我生成 AGENTS.md"

→ 执行 Step 1-4，扫描项目后生成文件。

用户说："update一下 AGENTS.md"

→ 先读取现有 AGENTS.md，结合项目当前状态update过时的内容。
