---
name: browser-task
description: 智能浏览器任务 - 描述任务，自动完成（推荐优先使用）
system: true
handler: browser
tool-name: browser_task
priority: high
---

# browser_task - 智能浏览器任务

**推荐优先使用** - 这是浏览器操作的首选工具。

基于 [browser-use](https://github.com/browser-use/browser-use) 开源项目实现。

## 用法

```python
browser_task(
    task="要完成的任务描述",
    max_steps=15  # 可选，默认 15
)
```

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| task | string | 是 | 任务描述，用自然语言描述你想完成的操作 |
| max_steps | integer | 否 | 最大执行步骤数，默认 15 |

## 何时使用（优先）

- 任何涉及多步骤的浏览器操作
- 网页搜索、表单填写、信息提取
- 不确定具体操作步骤时
- 复杂的网页交互流程

## 示例

### 搜索任务
```python
browser_task(task="打开百度搜索福建福州天气")
```

### 表单填写
```python
browser_task(task="打开 example.com 的注册页面，填写用户名 test123")
```

### 信息提取
```python
browser_task(task="打开 GitHub 首页，获取今日热门项目的名称")
```

### 截图任务
```python
browser_task(task="打开百度搜索福建福州，截图保存")
```

## 何时使用细粒度工具

仅在以下情况使用 `browser_navigate`、`browser_click` 等细粒度工具：

- `browser_task` 执行失败需要手动介入
- 仅需单步操作（如只截图 `browser_screenshot`）
- 需要精确控制特定元素

## 返回值

```json
{
    "success": true,
    "result": {
        "task": "打开百度搜索福建福州",
        "steps_taken": 5,
        "final_result": "搜索完成，已显示福建福州相关结果",
        "message": "任务完成: 打开百度搜索福建福州"
    }
}
```

## 注意事项

1. 任务描述要清晰具体，避免歧义
2. 复杂任务可能需要增加 max_steps
3. 首次使用会自动启动浏览器（可见模式）
4. **自动继承系统 LLM 配置**，无需额外配置 API Key

## 技术细节

- 通过 CDP (Chrome DevTools Protocol) 复用 OpenAkita 已启动的浏览器
- 自动继承 OpenAkita 系统配置的 LLM（来自 llm_endpoints.json）
- 基于 [browser-use](https://github.com/browser-use/browser-use) 开源项目

## 高级：操作用户已打开的 Chrome

如果想让 OpenAkita 操作你已打开的 Chrome 页面，需要以调试模式启动 Chrome：

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

启动后，OpenAkita 会自动检测并连接，可以操作你已打开的标签页。

## 相关技能

- `browser_screenshot` - 单独截图
- `browser_navigate` - 单独导航
- `send_to_chat` - 发送结果给用户
