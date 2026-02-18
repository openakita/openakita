---
description: "OpenAkita 模块索引：CLI、核心引擎、渠道、工具、桌面端与发布相关目录入口"
---

# project-modules

- CLI 入口 -> 职责: 命令行启动与路由 -> 入口: `src/openakita/main.py` -> 关键导出: `app`
- Core -> 职责: Agent 主循环、推理与编排核心 -> 入口: `src/openakita/core/` -> 关键导出: 核心执行组件
- LLM -> 职责: 多模型接入与调用抽象 -> 入口: `src/openakita/llm/` -> 关键导出: provider/client 适配层
- Memory -> 职责: 记忆读写与检索 -> 入口: `src/openakita/memory/` -> 关键导出: memory store / consolidation 组件
- Evolution -> 职责: 自检、迭代、修复流程 -> 入口: `src/openakita/evolution/` -> 关键导出: self-check/evolve 任务
- Channels -> 职责: IM 与交互渠道对接 -> 入口: `src/openakita/channels/` + `channels/` -> 关键导出: 各通道适配器
- Tools -> 职责: Shell/Web/File/MCP 等工具能力 -> 入口: `src/openakita/tools/` -> 关键导出: 工具注册与执行器
- Scheduler -> 职责: 定时任务与后台流程调度 -> 入口: `src/openakita/scheduler/` -> 关键导出: schedule runner
- Setup Center -> 职责: 桌面端前后端构建 -> 入口: `apps/setup-center/` -> 关键导出: Tauri + React 应用
- Build/Release -> 职责: 打包与发布 -> 入口: `build/` + `.github/workflows/` -> 关键导出: backend 打包脚本与 CI/CD
