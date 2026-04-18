---
name: tool-routing
description: Decision guide for choosing the right tool when operating websites, browsers, and desktop software. Consult when the task involves web interaction, website automation, or desktop app control.
system: true
category: System
priority: high
---

# Tool selection路由指南

当任务涉及操作网站、浏览器或桌面软件时，Use此指南选择最可靠的工具路径。

## 网站 & Browser operations

```
需要操作网站？
│
├─ 只需Read内容（文章、文档、API）
│   └─ web_fetch（最快，无需浏览器）
│
├─ 只需search信息
│   └─ web_search（DuckDuckGo 直接search）
│
├─ 需要交互（Click、填表、登录）
│   │
│   ├─ 目标网站有 opencli adapter？
│   │   └─ YES → opencli_run（最可靠，复用 Chrome 登录态）
│   │
│   ├─ 需要复杂多步交互？
│   │   └─ browser_task（Automatic规划步骤）
│   │       └─ 失败？→ Manual组合 browser_navigate + browser_click + browser_type
│   │
│   └─ 需要单步精确操作？
│       └─ browser_navigate / browser_click / browser_type 等
│
└─ 需要截图验证？
    └─ browser_screenshot → view_image
```

## 桌面软件操作

```
需要控制桌面软件？
│
├─ 有 cli-anything CLI？（cli_anything_discover 检查）
│   └─ YES → cli_anything_run（最可靠，Call真实后端）
│
├─ Windows 系统？
│   └─ desktop_* 工具（UIA/pyautogui GUI Automatic化）
│
└─ 有命令行工具？
    └─ run_shell（直接Execute）
```

## 可靠性排序

### 网站操作（从高到低）
1. **opencli_run** — 确定性命令 + JSON 输出 + 登录态
2. **web_fetch** — 简单 HTTP get（仅Read）
3. **browser_navigate + browser_click/type** — Manual精确控制
4. **browser_task** — AI 自主操作（可能不稳定）
5. **call_mcp_tool("chrome-devtools")** — 需要额外配置

### 桌面软件操作（从高到低）
1. **cli_anything_run** — CLI Call真实后端
2. **run_shell** — 系统命令行工具
3. **desktop_* 工具** — GUI Automatic化（仅 Windows，脆弱）

## 关键原则

- **browser_task 失败不要反复重试** — 失败 1 次就Switch到Manual browser_click/type 组合
- **search类任务不要用 browser_task** — 直接用 browser_navigate 拼 URL 参数更可靠
- **有 opencli adapter 时总Yes优先Use** — 比让 LLM 猜测页面操作可靠得多
- **有 cli-anything CLI 时优先Use** — 比 GUI Automatic化可靠 100 倍
