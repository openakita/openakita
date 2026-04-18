---
name: browser-task
description: Smart browser task agent - describe what you want done in natural language and it completes automatically. PREFERRED tool for multi-step browser operations like searching, form filling, and data extraction.
system: true
handler: browser
tool-name: browser_task
category: Browser
priority: high
---

# browser_task - 智能浏览器任务

**Recommendations优先Use** - 这YesBrowser operations的首选工具。

Based on [browser-use](https://github.com/browser-use/browser-use) 开源项目实现。

## 用法

```python
browser_task(
    task="要完成的任务描述",
    max_steps=15  # Optional,Default 15
)
```

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| task | string | Yes | 任务描述，用自然语言描述你想完成的操作 |
| max_steps | integer | No | MaximumExecute步骤数，Default 15 |

## When to Use（优先）

- 任何涉及多步骤的Browser operations
- 网页Search、表单填写、信息Extract
- 不确定具体操作步骤时
- 复杂的网页交互流程

## Examples

### Search任务
```python
browser_task(task="Open百度Search福建福州天气")
```

### 表单填写
```python
browser_task(task="Open example.com 的注册页面，填写用户名 test123")
```

### 信息Extract
```python
browser_task(task="Open GitHub 首页，Get今日热门项目的名称")
```

### 截图任务
```python
browser_task(task="Open百度Search福建福州，截图Save")
```

## 浏览器 & 网站操作工具选用指引

系统Provides多条路径操作网站和浏览器，按场景选择最可靠的方案：

| 场景 | Recommendations工具 | Description |
|------|---------|------|
| 目标网站有 opencli adapter | `opencli_run`（最可靠） | 确定性命令 + JSON 输出，复用 Chrome 登录态 |
| 需要登录但无 adapter | `browser_task` → Manual组合 | 先尝试 browser_task，失败则用 click/type Manual操作 |
| 仅需Read网页内容 | `web_fetch` | 最快最省资源，无需浏览器 |
| 仅需Search | `web_search` | DuckDuckGo 直接Search |
| 复杂多步浏览器交互 | `browser_task` | 适合登录、填表、筛选等 |
| 单步Browser operations | `browser_navigate`/`browser_click` 等 | 精确控制单个操作 |
| 操作用户已登录的 Chrome | `call_mcp_tool("chrome-devtools", ...)` | 需用户 Chrome 开启调试端口 |

决策顺序：`opencli_run`（有 adapter 时）→ `web_fetch`/`web_search`（只读时）→ `browser_task` → Manual browser_click/type 组合 → chrome-devtools MCP。

## When to Use细粒度工具

仅在以下情况Use `browser_navigate`、`browser_click` 等细粒度工具：

- `browser_task` Execute失败需要Manual介入
- 仅需单步操作（如只截图 `browser_screenshot`）
- 需要精确控制特定元素

## Return Values

```json
{
    "success": true,
    "result": {
        "task": "Open百度Search福建福州",
        "steps_taken": 5,
        "final_result": "Search完成，已Display福建福州相关结果",
        "message": "任务完成: Open百度Search福建福州"
    }
}
```

## Notes

1. 任务描述要清晰具体，避免歧义
2. 复杂任务可能需要增加 max_steps
3. 首次Use会AutomaticLaunch browser（可见模式）
4. **Automatic继承系统 LLM 配置**，无需额外配置 API Key

## 技术细节

- Via CDP (Chrome DevTools Protocol) 复用 OpenAkita 已Launch的浏览器
- Automatic继承 OpenAkita 系统配置的 LLM（来自 llm_endpoints.json）
- Based on [browser-use](https://github.com/browser-use/browser-use) 开源项目

## 高级：操作用户已Open的 Chrome

如果想让 OpenAkita 操作你已Open的 Chrome 页面，需要以调试模式Launch Chrome：

**Windows:**
```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

**macOS:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

**Linux:**
```bash
google-chrome --remote-debugging-port=9222
```

Launch后，OpenAkita 会Automatic检测并连接，可以操作你已Open的标签页。

## 相关技能

- `browser_screenshot` - 单独截图
- `browser_navigate` - 单独导航
- `deliver_artifacts` - Send结果给用户
