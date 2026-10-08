# 百智云工具包 MCP Server

通过 Streamable HTTP 连接百智云托管的 Agent Toolkit。此预设复用 OpenAkita 的 MCP 客户端，实际可用工具、输入参数与权限以连接后的 `tools/list` 为准；不要猜测工具名或参数。

## 配置与使用

1. 在 [百智云工具包控制台](https://agent-toolkit.app.baizhi.cloud/) 获取自己的 API Key。
2. 打开 OpenAkita 的 MCP 管理面板，找到 `baizhi-agent-toolkit`（百智云工具包），展开配置，在 **Baizhi Cloud API Key** 中填写 Key 并保存。只填写 Key，预设会添加 `Bearer ` 前缀。
3. 按需点击连接，查看实际发现的工具，再选择适合任务的工具调用。此预设不设置启动时自动连接。

也可在工作区 `.env` 中设置 `BAIZHI_API_KEY`，重新加载 MCP 配置或重启后连接。不要把真实 Key 写入本文件、共享配置、聊天提示词、Issue 或日志。

## 数据、权限与费用

- 这是需要账号的外部托管服务；工具输入通过 HTTPS 发送到百智云。仅发送获准交给该服务的数据。
- 账号可能提供搜索、网页读取等不同工具，也可能包含有副作用的工具；先检查实际工具列表与权限，不把整个服务视为只读。
- 调用可能消耗账号额度或产生费用，按自己的账号和额度决定是否调用。
- [公开集成仓库](https://github.com/chaitin/baizhi-agent-toolkit)提供接入配置与文档，不包含托管后端源码。本预设不表示 OpenAkita 对百智云的认证或背书。
