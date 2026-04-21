# WhatsApp 通道插件 / WhatsApp Channel Plugin

将 WhatsApp 接入 OpenAkita 作为 IM 通道。支持两种模式：

## 模式 A: Cloud API（企业版）

使用 Meta 官方 WhatsApp Cloud API，通过 HTTP REST + Webhook 收发消息。

**适用场景**：企业账号、正式商用  
**优点**：稳定、合规、无需 QR 扫码  
**配置**：在 Meta Developer Portal 创建应用，获取 Phone Number ID 和 Access Token

### 必填配置

| 参数 | 说明 |
|------|------|
| `mode` | 设为 `cloud_api` |
| `phone_number_id` | WhatsApp Business 手机号 ID |
| `access_token` | Graph API 永久访问令牌 |
| `verify_token` | Webhook 验证令牌（自定义字符串） |

## 模式 B: WhatsApp Web（个人版） — Baileys 7.x

通过 [Baileys 7.x](https://github.com/WhiskeySockets/Baileys)（Node.js）连接 WhatsApp Web 协议，支持 QR 扫码链接个人账号。

**适用场景**：个人账号、测试环境
**优点**：免费、支持 QR 扫码
**要求**：**Node.js ≥ 20**（baileys 7 要求，且为 ESM-only）

> ⚠️ **2026 年起必须使用 baileys 7.x**：WhatsApp 已切换到 LID（Local Identifier）系统，旧版 baileys 6.x 因协议不兼容会被官方限流甚至封号。本插件 2.0.0 已迁移到 baileys 7。如果你之前停留在 1.x（baileys 6），请：
>
> ```bash
> cd <plugin>/bridge
> rm -rf node_modules package-lock.json
> npm install
> ```
>
> 同时建议清理一次 `BRIDGE_DATA_DIR` 下的旧 auth state（旧 session 会自动迁移，但全新登录更稳）。

### 配置

| 参数 | 说明 |
|------|------|
| `mode` | 设为 `web` |
| `node_path` | Node.js 可执行文件路径（默认 `node`，需 ≥ 20） |
| `bridge_port` | Baileys bridge HTTP 端口（默认 9882） |

### 首次使用

1. 设置 `mode` 为 `web`
2. 启动 bot 后，在 IM 配置界面点击"扫码连接"
3. 用手机 WhatsApp 扫描 QR 码完成配对

### LID 提示

baileys 7 起，群聊场景下用户的 `from` 字段可能是 LID（如 `12345@lid`）而非真实手机号。本桥接会优先使用 WA 提供的 PN 别名（`participantPn` / `remoteJidAlt`），无法获取时直接透传 LID。**不要尝试反查手机号**——参见 [官方迁移指南](https://whiskey.so/migrate-latest)。

## 功能

- 文本消息收发
- 图片、文件、语音、视频消息
- 群聊 / 私聊
- @提及检测
- 流式输出（累积后发送）
- 输入状态指示器

## 权限

- `channel.register` — 注册 WhatsApp 通道适配器
- `routes.register` — 注册 onboard API 端点（QR 扫码流程）
- `config.read/write` — 读写插件配置

---

# WhatsApp Channel Plugin

Connect WhatsApp to OpenAkita as an IM channel. Two modes are supported:

## Mode A: Cloud API (Business)

Uses Meta's official WhatsApp Cloud API via HTTP REST + Webhook.

**Use case**: Business accounts, production  
**Setup**: Create an app in Meta Developer Portal, get Phone Number ID and Access Token

## Mode B: WhatsApp Web (Personal) — Baileys 7.x

Uses [Baileys 7.x](https://github.com/WhiskeySockets/Baileys) (Node.js) to connect via WhatsApp Web protocol with QR code pairing.

**Use case**: Personal accounts, testing
**Requirements**: **Node.js >= 20** (baileys 7 is ESM-only and requires Node 20)

> ⚠️ Baileys 7 is mandatory in 2026: WhatsApp finalized the LID (Local Identifier) rollout, and 6.x is no longer protocol-compatible — staying on 6.x risks rate-limiting or bans. This plugin 2.0.0 ships with baileys 7. If upgrading from 1.x, run `npm install` in `bridge/` to refresh dependencies. See the [official migration guide](https://whiskey.so/migrate-latest).

## Features

- Text, image, file, voice, and video messages
- Group chat / DM support
- @mention detection
- Streaming output (accumulated batch send)
- Typing indicators
- QR code pairing (Web mode)
