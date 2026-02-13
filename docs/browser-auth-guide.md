# 浏览器登录态保持指南

## 问题背景

默认情况下，OpenAkita 的浏览器工具会启动一个新的浏览器实例，没有用户的登录状态、Cookie 或密码管理器。本指南介绍如何让 AI Agent 连接到您**真实的 Chrome 浏览器**，保留所有登录状态。

## 方案概览

| 方案 | 登录态 | 密码管理器 | 安装难度 | 推荐场景 |
|------|--------|-----------|---------|---------|
| Chrome DevTools MCP | ✅ 保留 | ✅ 可用 | ⭐ 简单 | 需要登录态的自动化 |
| mcp-chrome 扩展 | ✅ 完全保留 | ✅ 完全可用 | ⭐⭐ 中等 | 最佳用户体验 |
| 内置 Playwright | ❌ 新实例 | ❌ 无 | 自动 | 无需登录的简单任务 |

---

## 方案一：Chrome DevTools MCP（推荐）

Google 官方提供的 Chrome 浏览器控制工具，通过 MCP 协议连接到用户正在运行的 Chrome。

### 前置条件

- **Node.js** v20.19 或更高版本
- **Chrome 浏览器**（推荐 Chrome 144+ 以使用 autoConnect）

### 设置步骤

#### 方式 A：autoConnect 模式（Chrome 144+，推荐）

1. 打开 Chrome 浏览器
2. 在地址栏输入 `chrome://inspect/#remote-debugging`
3. 按照提示**开启远程调试**
4. Chrome DevTools MCP 会自动连接到您的浏览器

> 每次 AI Agent 连接时，Chrome 会弹窗询问是否允许，点击"Allow"即可。

#### 方式 B：手动开启调试端口

如果 Chrome 版本低于 144，需要手动用调试端口启动 Chrome：

**Windows:**
```bash
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

> ⚠️ 使用此方式时，需要先关闭所有 Chrome 窗口再重新启动。

### 使用方式

OpenAkita 启动后，Chrome DevTools MCP 会自动作为外部 MCP 服务器加载。可以通过以下方式调用：

```
# 通过 MCP 工具调用
call_mcp_tool("chrome-devtools", "navigate_page", {"url": "https://example.com"})
call_mcp_tool("chrome-devtools", "click", {"selector": "#login-btn"})
call_mcp_tool("chrome-devtools", "fill", {"selector": "#email", "value": "user@example.com"})
call_mcp_tool("chrome-devtools", "take_screenshot", {})
```

### 自定义配置

如果需要修改默认配置（如使用不同的 Chrome 通道），编辑 `mcps/chrome-devtools/SERVER_METADATA.json`：

```json
{
    "command": "npx",
    "args": ["-y", "chrome-devtools-mcp@latest", "--autoConnect", "--channel=canary"]
}
```

可用参数：
- `--autoConnect` - 自动连接正在运行的 Chrome（Chrome 144+）
- `--browser-url=http://127.0.0.1:9222` - 手动指定调试端口
- `--channel=stable|canary|beta|dev` - Chrome 通道
- `--headless=true` - 无头模式
- `--no-usage-statistics` - 禁用使用统计

---

## 方案二：mcp-chrome 扩展

通过 Chrome 扩展直接控制用户浏览器，**无需开启调试端口**。

### 安装步骤

1. **安装 bridge 服务**
   ```bash
   npm install -g mcp-chrome-bridge
   ```

2. **安装 Chrome 扩展**
   - 从 [GitHub Releases](https://github.com/hangwin/mcp-chrome/releases) 下载最新扩展
   - 打开 Chrome，访问 `chrome://extensions/`
   - 开启"开发者模式"
   - 点击"加载已解压的扩展"，选择下载的扩展目录

3. **连接扩展**
   - 点击 Chrome 工具栏中的 mcp-chrome 图标
   - 点击"Connect"按钮

### 使用方式

扩展启动后会在 `http://127.0.0.1:12306/mcp` 暴露 MCP 接口。OpenAkita 会自动检测。

---

## 常见问题

### Q: 两种方案可以同时使用吗？
A: 可以。Chrome DevTools MCP 和 mcp-chrome 扩展互不冲突，OpenAkita 会根据可用性自动选择。

### Q: 密码管理器（1Password/Bitwarden）能正常工作吗？
A: 使用 Chrome DevTools MCP 的 autoConnect 模式或 mcp-chrome 扩展时，所有 Chrome 扩展（包括密码管理器）都正常工作。

### Q: 使用调试端口安全吗？
A: autoConnect 模式每次连接需要用户手动确认，安全性良好。手动调试端口模式仅在本机暴露，但建议操作完成后关闭。

### Q: 内置浏览器和外部 Chrome 有什么区别？
A: 内置浏览器（Playwright）启动全新实例，速度快但没有登录状态。外部 Chrome 连接到用户真实浏览器，保留所有状态但需要额外配置。
