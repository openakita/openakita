# 多 Agent 入门

## 什么是多 Agent

传统 AI 助手是"一个人干所有事"——一个模型处理你的所有请求。多 Agent 则像一个**专业团队**：每个 Agent 擅长不同领域，由编排器（Orchestrator）自动分工协作。

| 模式 | 比喻 | 特点 |
|------|------|------|
| 单 Agent | 一个全能助手 | 简单直接，适合日常问答和轻量任务 |
| 多 Agent | 一个专业团队 | 专人专事，复杂任务自动拆分委托 |

举个例子：你说"帮我调研竞品并生成一份分析 PPT"。在多 Agent 模式下，编排器会把"调研"分配给浏览器 Agent，把"写 PPT"分配给文档 Agent，最后汇总交付。

## 开启多 Agent 模式

多 Agent 默认关闭。开启方式：

1. 点击侧边栏底部的齿轮图标，进入 [Agent 配置](/web/#/config/agent)
2. 找到 **「多 Agent 协作」** 开关，打开即可
3. 状态会持久化到 `data/runtime_state.json`，重启后保持

::: tip 提示
开启后，侧边栏会出现「协作动态」和「组织编排」等新入口。如果只是简单对话，单 Agent 模式已经够用。
:::

## 协作动态面板

[打开协作动态](/web/#/dashboard)

协作动态是多 Agent 的"作战指挥室"，可以看到：

- **Agent 活动流** — 哪个 Agent 正在执行什么任务
- **委托日志** — 编排器的路由决策记录
- **任务状态** — 进行中 / 已完成 / 失败的任务一览
- **性能指标** — 各 Agent 的响应时间和 Token 消耗

## 预设 Agent

开启多 Agent 后，系统自带以下预设 Agent：

| Agent | 职责 | 擅长 |
|-------|------|------|
| **default** | 通用助手（主 Agent） | 日常对话、任务规划、综合分析 |
| **office-doc** | 文档专家 | Word/PPT/Excel 处理、排版、格式转换 |
| **code-assistant** | 编程助手 | 代码编写、调试、重构、代码审查 |
| **browser-agent** | 浏览器代理 | 网页浏览、信息采集、在线操作 |
| **data-analyst** | 数据分析师 | 数据清洗、统计分析、可视化 |

你也可以在 [Agent 管理](/web/#/agent-manager) 中自定义更多专属 Agent。

## 委托机制

编排器（Orchestrator）是多 Agent 的核心调度器：

1. **意图识别** — 分析用户消息的意图和所需能力
2. **路由决策** — 根据 Agent 画像匹配最合适的执行者
3. **任务委托** — 将任务分发给目标 Agent
4. **结果聚合** — 收集子 Agent 的输出，整合回复

**关键限制：**

- **最大委托深度：5 层** — Agent A → B → C → D → E 最多 5 级嵌套，防止无限递归
- **自动回退** — 子 Agent 失败时，编排器会尝试重新路由或由主 Agent 兜底
- **共享上下文** — 子 Agent 共享同一会话的 PromptAssembler，保证上下文连贯

```
用户消息 → Orchestrator（编排器）
              ├─ 匹配 Agent A → 执行 → 返回结果
              ├─ 匹配 Agent B → 执行 → 返回结果
              └─ 聚合结果 → 回复用户
```

## 相关页面

- [组织编排](/multi-agent/org-editor) — 可视化编排 Agent 团队结构
- [Agent 管理](/multi-agent/agent-manager) — 创建、编辑、导入导出 Agent
- [Agent Store / Skill Store](/multi-agent/store) — 从社区获取 Agent 和技能
- [配置向导 · Agent 配置](/web/#/config/agent) — 开启多 Agent 及相关设置
- [协作动态面板](/web/#/dashboard) — 查看 Agent 实时协作状态
