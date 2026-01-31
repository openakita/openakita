# Building Mode Prompt
<!--
参考来源: Ralph Playbook
https://claytonfarr.github.io/ralph-playbook/

此文件定义 Building 模式的指令
用于: 从计划选择任务 → 实现 → 测试 → 提交
-->

0a. 学习 `specs/*` 中的需求规格文档。

0b. 学习 `identity/MEMORY.md` 中的 Implementation Plan。

0c. 学习 `src/openakita/` 了解现有代码和模式。

1. 你的任务是按照规格实现功能。遵循 `identity/MEMORY.md` 中的 Implementation Plan，选择最重要的项目来处理。在做出更改之前，先搜索代码库（不要假设未实现）。

2. 实现功能或解决问题后，运行该代码单元的测试。如果功能缺失，你的工作是按照规格添加它。深入思考。

3. 当你发现问题时，立即更新 `identity/MEMORY.md` 的 Implementation Plan。解决后，更新并移除该项目。

4. 当测试通过时:
   - 更新 `identity/MEMORY.md` 标记任务完成
   - 记录学到的经验
   - 如有新发现，更新 `identity/USER.md`

## 重要准则

99999. 编写文档时，捕获"为什么"——测试和实现的重要性。

999999. 单一来源真相，不要迁移/适配器。如果与你工作无关的测试失败，作为增量的一部分解决它们。

9999999. 保持 `identity/MEMORY.md` 简洁。定期清理已完成的项目。

99999999. 如果需要调试问题，可以添加额外的日志。

999999999. 保持 `identity/MEMORY.md` 更新——未来的工作依赖它来避免重复努力。特别是在完成你的轮次后更新。

9999999999. 当你学到关于如何运行应用的新知识时，更新 `identity/AGENT.md`，但保持简洁。

99999999999. 对于你注意到的任何bug，解决它们或在 `identity/MEMORY.md` 中记录，即使与当前工作无关。

999999999999. 完整实现功能。占位符和存根浪费时间和精力。

9999999999999. **重要**: 保持 `identity/AGENT.md` 只包含操作性内容——状态更新和进度笔记属于 `identity/MEMORY.md`。
