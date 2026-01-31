# Core Agent Specification

## Overview

OpenAkita 的核心模块，负责协调所有其他模块，提供统一的对外接口。

## Jobs to Be Done

1. 接收用户输入并理解意图
2. 将任务分解为可执行的子任务
3. 协调技能、工具、存储等模块完成任务
4. 维护对话上下文和记忆
5. 执行 Ralph 循环直到任务完成

## Components

### Agent (`core/agent.py`)

主 Agent 类，协调所有模块。

```python
class Agent:
    brain: Brain           # LLM 交互
    ralph: RalphLoop       # Ralph 循环引擎
    memory: Memory         # 记忆系统
    skills: SkillRegistry  # 技能注册
    tools: ToolManager     # 工具管理
    
    async def chat(self, message: str) -> str
    async def execute_task(self, task: Task) -> TaskResult
    async def self_check(self) -> CheckResult
```

### Brain (`core/brain.py`)

与 Claude API 交互的模块。

```python
class Brain:
    client: Anthropic
    model: str
    
    async def think(self, prompt: str, context: Context) -> Response
    async def plan(self, task: Task) -> Plan
    async def generate_code(self, spec: str) -> str
```

### RalphLoop (`core/ralph.py`)

Ralph Wiggum 循环引擎，实现永不放弃的执行模式。

```python
class RalphLoop:
    max_iterations: int = 100
    
    async def run(self, task: Task) -> TaskResult
    def is_complete(self) -> bool
    async def save_progress(self)
    async def load_progress(self)
```

### Identity (`core/identity.py`)

加载和管理 identity/AGENT.md 和 identity/SOUL.md。

```python
class Identity:
    soul: str      # identity/SOUL.md 内容
    agent: str     # identity/AGENT.md 内容
    
    def get_system_prompt(self) -> str
    def get_behavior_rules(self) -> list[str]
```

## Acceptance Criteria

- [ ] Agent 可以接收用户消息并返回响应
- [ ] Agent 可以执行多步骤任务
- [ ] Agent 在任务失败时会自动重试
- [ ] Agent 会更新 identity/MEMORY.md 记录进度
- [ ] Agent 可以运行自检
