# mcp-chrome 扩展使用说明

## 概述

mcp-chrome 是一个 Chrome 扩展，通过 Streamable HTTP 协议将用户的真实浏览器暴露为 MCP 服务器。它天然继承所有登录状态、Cookie 和浏览器扩展（包括密码管理器如 1Password、Bitwarden 等）。

## 安装步骤

### 1. 安装 Chrome 扩展

- 从 [mcp-chrome Releases](https://github.com/hangwin/mcp-chrome/releases) 下载最新版扩展
- 打开 Chrome，访问 `chrome://extensions/`
- 开启右上角的"开发者模式"
- 点击"加载已解压的扩展程序"
- 选择下载解压后的扩展目录

### 2. 连接扩展

- 安装完成后，点击 Chrome 工具栏中的 mcp-chrome 图标
- 点击 "Connect" 按钮
- 连接成功后图标变为绿色

## 工作原理

扩展启动后，在 `http://127.0.0.1:12306/mcp` 暴露 MCP 接口（Streamable HTTP 协议）。OpenAkita 通过此端点连接并调用浏览器操作工具。

## 核心工具

- `chrome_navigate` - 导航到指定 URL
- `chrome_click` - 点击页面元素
- `chrome_type` - 在输入框中输入文本
- `chrome_screenshot` - 截取页面截图
- `chrome_get_content` - 获取页面内容
- `chrome_search` - 语义搜索页面元素

## 使用方式

```
call_mcp_tool("chrome-browser", "chrome_navigate", {"url": "https://example.com"})
call_mcp_tool("chrome-browser", "chrome_click", {"selector": "#login-btn"})
call_mcp_tool("chrome-browser", "chrome_type", {"selector": "#email", "text": "user@example.com"})
```

## 注意事项

- 扩展需要在 Chrome 中保持激活状态
- 默认端口 12306，如有冲突可在扩展设置中修改
- 扩展支持的 Chrome 版本没有限制
