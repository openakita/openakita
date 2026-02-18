---
description: "OpenAkita 数据与配置索引：身份档案、配置、记忆与运行态数据目录"
---

# project-data

- 身份设定 -> 位置: `identity/` -> schema: `SOUL.md`/`AGENT.md`/`USER.md`/personas 文本模板
- 全局文档与规范 -> 位置: `docs/` + `.claude/rules/` -> schema: Markdown 规范与说明文档
- 记忆与会话存储 -> 位置: `src/openakita/memory/` + `src/openakita/storage/` + `src/openakita/sessions/` -> schema: 存储抽象与会话状态管理
- 运行日志 -> 位置: `logs/` + `src/openakita/logging/` -> schema: 文本/结构化日志与追踪信息
- 通道配置 -> 位置: `channels/` + `src/openakita/channels/` -> schema: 各 IM 渠道配置与适配参数
- 技能与提示词 -> 位置: `skills/` + `prompts/` + `src/openakita/skills/` + `src/openakita/prompt/` -> schema: 技能包与提示模板
- Yongan 隔离数据 -> 位置: `Yongan/docs/` + `Yongan/src/` + `Yongan/patches/` -> schema: 本地定制文档/代码/补丁
