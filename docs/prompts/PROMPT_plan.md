# Planning Mode Prompt
<!--
参考来源: Ralph Playbook
https://claytonfarr.github.io/ralph-playbook/

此文件定义 Planning 模式的指令
用于: 分析需求 → 生成/更新 identity/MEMORY.md 中的计划
-->

0a. 学习 `specs/*` 中的需求规格文档，理解应用需求。

0b. 学习 `identity/MEMORY.md`（如存在）了解当前计划和进度。

0c. 学习 `src/openakita/` 了解现有代码结构和模式。

1. 研究 `identity/MEMORY.md`（如存在，可能不正确）并比较 `specs/*` 与现有源代码。分析发现，确定优先级，创建/更新 `identity/MEMORY.md` 中的 Implementation Plan 部分，作为按优先级排序的待实现项目列表。

深入思考。考虑搜索 TODO、最小实现、占位符、跳过/不稳定的测试和不一致的模式。

**重要**: 
- 仅做计划，不要实现任何东西
- 不要假设功能缺失，先用代码搜索确认
- 将 `src/openakita/skills/` 视为技能库
- 优先使用已有的工具和模式

**最终目标**: 
我们要实现一个全能自进化AI Agent。考虑缺失的元素并相应规划。如果元素缺失，先搜索确认不存在，然后在 `specs/` 创建规格文档，在 `identity/MEMORY.md` 中记录实现计划。
