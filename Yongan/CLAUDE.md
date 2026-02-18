# OpenAkita (Yongan Workspace)

## 项目身份
- 本仓库是 OpenAkita：一个以 `src/openakita` 为核心的自进化 AI Agent 项目，包含 CLI、服务端与桌面端构建链路。

## 项目独有规范
- Python 核心包路径固定为 `src/openakita`，新增 Python 代码优先保持该目录分层风格。
- 对本地定制/隔离扩展，统一放在 `Yongan/` 下，不直接把 Yongan 新增内容写入上游目录。
- 仅在无法通过扩展实现时修改上游文件，并在 `Yongan/docs/` 补充变更原因。

## 常用开发命令
- 安装开发依赖：`pip install -e ".[dev]"`
- 本地启动 CLI：`openakita`
- 初始化配置：`openakita init`
- 运行测试：`pytest tests/ -v`
- 代码检查：`ruff check src/`
- 类型检查：`mypy src/`

## 操作约束
- 项目结构索引请优先查看：
  - `Yongan/.claude/skills/project-modules/SKILL.md`
  - `Yongan/.claude/skills/project-data/SKILL.md`
  - `Yongan/.claude/skills/project-pipelines/SKILL.md`
- 文档、方案、计划统一放 `Yongan/docs/`。
