# Chrome DevTools MCP 使用说明

## 概述

Chrome DevTools MCP 是 Google 官方提供的浏览器自动化工具，可以连接到用户正在运行的 Chrome 浏览器，**保留所有登录状态、Cookie 和密码管理器扩展**。

## 使用前提

### 方式一：autoConnect（推荐，Chrome 144+）

1. 打开 Chrome，访问 `chrome://inspect/#remote-debugging`
2. 开启远程调试功能
3. Chrome DevTools MCP 会自动连接到正在运行的 Chrome

### 方式二：手动开启调试端口

在终端启动 Chrome 时添加调试端口参数：

**Windows:**
```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

**macOS:**
```
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

**Linux:**
```
google-chrome --remote-debugging-port=9222
```

## 核心工具

- `navigate_page` - 导航到指定 URL
- `click` - 点击页面元素
- `fill` / `fill_form` - 填写表单
- `take_screenshot` - 截取页面截图
- `take_snapshot` - 获取页面结构快照
- `evaluate_script` - 执行 JavaScript
- `list_pages` - 列出所有标签页

## 与内置浏览器工具的关系

- Chrome DevTools MCP 适合需要**保留登录态**的场景
- 内置 `browser_task` 工具使用 browser-use Agent 进行智能自动化
- 两者可以共存，按需选择使用

## 注意事项

- 需要 Node.js v20.19+ 和 npm
- 首次使用会自动下载 chrome-devtools-mcp 包
- autoConnect 模式下，每次连接会弹窗征求用户许可
