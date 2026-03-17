# 消息通道（IM）

## 什么是消息通道

消息通道让你通过**日常使用的聊天软件**与 OpenAkita 对话——一个 OpenAkita 实例可以同时连接多个平台。

无论你在 Telegram、飞书还是钉钉发消息，背后都是同一个 Agent 在思考和执行。就像给你的 AI 助手开通了多个"手机号"，哪个方便用哪个。

## 支持平台一览

[打开消息通道配置](/web/#/im)

| 平台 | 文本 | 图片 | 文件 | 语音 | 群聊 | 流式输出 |
|------|:----:|:----:|:----:|:----:|:----:|:--------:|
| Telegram | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 飞书 Feishu | ✅ | ✅ | ✅ | — | ✅ | ✅（流式卡片） |
| 钉钉 DingTalk | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| 企业微信 WeCom | ✅ | ✅ | ✅ | — | ✅ | — |
| QQ 官方 | ✅ | ✅ | — | — | ✅ | — |
| OneBot (QQ) | ✅ | ✅ | ✅ | — | ✅ | — |

## Telegram

最推荐的入门通道，配置最简单。

1. 在 Telegram 中搜索 **@BotFather**，发送 `/newbot`
2. 按提示设置名称，获取 **Bot Token**
3. 在 OpenAkita 中填入 Token：[配置 Telegram](/web/#/config/im)
4. （可选）如在中国大陆使用，配置代理地址

**配对码安全机制**：首次连接时，OpenAkita 会生成一个一次性配对码。在 Telegram 对话中发送该配对码，完成身份绑定后才能正常使用，防止未授权访问。

## 飞书 Feishu

提供两种配置方式，推荐使用快速方式。

### 快速配置（推荐） {#feishu-quick}

1. 在 [IM 配置页](/web/#/config/im) 选择飞书
2. 点击 **「扫码创建机器人」** 按钮
3. 用飞书 App 扫描二维码
4. App ID 和 App Secret 自动回填，无需登录开发者后台

### 手动配置

1. 登录 [飞书开放平台](https://open.feishu.cn) → 创建企业自建应用
2. 开启「机器人」能力，获取 **App ID** 和 **App Secret**
3. 配置事件订阅回调地址
4. 在 OpenAkita 中填入凭证

### 飞书特色功能

- **流式卡片**：回复以飞书消息卡片形式呈现，支持实时更新内容
- **群聊响应模式**：可在 `mention_only`（仅 @时回复）、`smart`（智能判断）、`always`（总是回复）之间切换

## 钉钉 DingTalk

1. 登录 [钉钉开放平台](https://open-dev.dingtalk.com) → 创建应用
2. 启用「机器人」→ 选择 **Stream 协议**（推荐，无需公网 IP）
3. 获取 **Client ID** 和 **Client Secret**
4. 在 OpenAkita 中填入凭证：[配置钉钉](/web/#/config/im)

::: tip 为什么推荐 Stream 协议
Stream 协议通过长连接通信，不需要配置公网回调地址，部署在内网也能正常工作。
:::

## 企业微信 WeCom

企业微信支持两种通信模式：

| 模式 | 适用场景 |
|------|---------|
| **HTTP 回调** | 有公网 IP 或域名，需配置回调 URL 和 Token/AESKey |
| **WebSocket** | 无公网 IP，通过长连接实现，配置更简单 |

配置步骤：
1. 登录 [企业微信管理后台](https://work.weixin.qq.com) → 应用管理 → 创建应用
2. 获取 **CorpID**、**AgentID**、**Secret**
3. 根据网络环境选择通信模式
4. 在 OpenAkita 中完成配置：[配置企业微信](/web/#/config/im)

## QQ 官方

1. 登录 [QQ 开放平台](https://q.qq.com) → 创建机器人
2. 获取 **App ID** 和 **Token**
3. 在 OpenAkita 中填入凭证

## OneBot（NapCat / Lagrange）

适用于已有 OneBot 协议实现的用户：

1. 安装 [NapCat](https://github.com/NapNeko/NapCatQQ) 或 [Lagrange](https://github.com/LagrangeDev/Lagrange.Core)
2. 配置 WebSocket 正向连接地址
3. 在 OpenAkita 中填入 WebSocket URL：`ws://127.0.0.1:端口`

## 群聊响应模式

当 Agent 被拉入群聊时，可以设置响应策略：

| 模式 | 行为 |
|------|------|
| `always` | 群内所有消息都回复 |
| `mention_only` | 仅在被 @ 时回复 |
| `smart` | 智能判断是否与 Agent 相关再决定是否回复 |

在各通道配置页中设置，或通过 [高级设置](/advanced/advanced) 统一管理。

## 相关页面

- [聊天对话](/features/chat) — 聊天界面功能详解
- [LLM 端点配置](/features/llm-config) — 配置 Agent 使用的 AI 模型
- [配置向导详解](/advanced/wizard) — 完整的初始化配置流程
- [网络基础科普](/network/basics) — 理解回调地址、端口、代理等网络概念
- [多端访问指南](/network/multi-access) — 在手机、平板等多设备上使用
