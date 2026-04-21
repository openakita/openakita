# IM 通道集成指南

OpenAkita 支持多个即时通讯平台，每个平台通过独立的适配器 (Adapter) 接入统一的消息网关 (MessageGateway)。

## 平台概览

| 平台 | 状态 | 接入方式 | 需要公网 IP | 安装命令 |
|------|------|---------|------------|---------|
| Telegram | ✅ 稳定 | Long Polling | ❌ 不需要 | 默认包含 |
| 飞书 | ✅ 稳定 | WebSocket 长连接 | ❌ 不需要 | `pip install openakita[feishu]` |
| 钉钉 | ✅ 稳定 | Stream 模式 (WebSocket) | ❌ 不需要 | `pip install openakita[dingtalk]` |
| 企业微信 | ✅ 稳定 | HTTP 回调（智能机器人） | ⚠️ 需要公网 IP | `pip install openakita[wework]` |
| 微信个人号 | ✅ 实验性 | HTTP 长轮询 (iLink Bot API) | ❌ 不需要 | `pip install openakita[wechat]` |
| QQ 官方机器人 | ✅ 稳定 | QQ 开放平台 API (WebSocket) | ❌ 不需要 | `pip install openakita[qqbot]` |
| OneBot | ✅ 稳定 | OneBot v11 (WebSocket) | ❌ 不需要 | `pip install openakita[onebot]` |

## 媒体类型支持矩阵

### 接收消息 (平台 → OpenAkita)

| 类型 | Telegram | 飞书 | 钉钉 | 企业微信 | 微信 | QQ 官方机器人 | OneBot |
|------|----------|------|------|---------|------|-------------|--------|
| 文字 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 图片 | ✅ | ✅ | ✅ | ⚠️ 仅单聊 | ✅ | ✅ | ✅ |
| 语音 | ✅ | ✅ | ✅ | ❌ 不支持 | ✅ | ✅ | ✅ |
| 文件 | ✅ | ✅ | ✅ | ❌ 不支持 | ✅ | ✅ | ✅ |
| 视频 | ✅ | ✅ | ✅ | ❌ 不支持 | ✅ | ✅ | ✅ |

### 发送消息 (OpenAkita → 平台)

| 方法 | Telegram | 飞书 | 钉钉 | 企业微信 | 微信 | QQ 官方机器人 | OneBot |
|------|----------|------|------|---------|------|-------------|--------|
| send_text | ✅ | ✅ | ✅ | ✅ (stream 被动回复) | ✅ | ✅ | ✅ |
| send_image | ✅ | ✅ | ✅ | ✅ (stream msg_item, base64+md5) | ✅ (CDN+AES) | ✅ (本地 base64 直传) | ✅ |
| send_file | ✅ | ✅ | ✅ (降级为链接) | ❌ 降级为文本 | ✅ (CDN+AES) | ⚠️ 仅单聊 (file_type=4) | ✅ (upload_file API) |
| send_voice | ✅ | ✅ | ✅ (降级为文件) | ❌ 不支持 | ✅ (CDN+AES) | ✅ (silk/wav/mp3/flac 直传) | ✅ (record) |

> **企业微信限制说明**：智能机器人通过 stream 流式被动回复发送文本和图片（JPG/PNG，≤10MB，单条最多 10 张）。**不支持语音、文件和视频的收发**。接收端仅支持文字、图文混排和图片（图片仅单聊）。response_url 作为 stream 不可用时的降级方案（仅文本）。

> **注意**: 图片和语音由 MessageGateway 自动下载并预处理。文件和视频不会自动下载，需要通过 `deliver_artifacts` 工具主动处理。

---

## Telegram

### 前置条件

- 一个 Telegram 账号
- 网络能访问 Telegram API（大陆环境需要代理）

### 平台侧配置

1. **创建机器人**: 在 Telegram 中搜索 [@BotFather](https://t.me/BotFather)，发送 `/newbot`
2. **获取 Token**: 按提示设置名称后，BotFather 会返回一个 Bot Token（格式如 `123456:ABC-DEF...`）
3. **（可选）设置命令**: 发送 `/setcommands` 配置机器人命令菜单

### OpenAkita 配置

```bash
# .env
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=你的Bot Token

# 代理（大陆环境需要）
TELEGRAM_PROXY=http://127.0.0.1:7890
# 或 socks5://127.0.0.1:1080
```

### 部署模式

- **Long Polling（默认）**: 无需公网 IP，适配器主动轮询 Telegram 服务器获取消息
- **Webhook**: 需要公网 HTTPS URL，配置 `TELEGRAM_WEBHOOK_URL`

### 验证方法

1. 启动 OpenAkita 后，在 Telegram 中找到你的机器人
2. 发送 `/start`，应该收到配对码提示
3. 发送配对码完成配对（如果启用了 `TELEGRAM_REQUIRE_PAIRING`）
4. 发送任意消息，观察日志输出和机器人回复

### 特有功能

- 配对安全机制（防止未授权访问）
- 全媒体类型支持最完整
- Markdown 格式消息
- 内联键盘

---

## 飞书 (Lark)

### 前置条件

- 企业飞书账号
- 在 [飞书开发者后台](https://open.feishu.cn/) 创建企业自建应用

### 平台侧配置

1. **创建应用**: 进入 [开发者后台](https://open.feishu.cn/app) → 创建企业自建应用
2. **获取凭证**: 在「凭证与基础信息」页面获取 App ID 和 App Secret
3. **配置权限**: 在「权限管理」中添加以下权限：
   - `im:message` — 获取与发送消息
   - `im:message.create_v1` — 以应用身份发消息
   - `im:resource` — 获取消息中的资源文件
   - `im:file` — 上传/下载文件
4. **配置事件订阅**:
   - 进入「事件与回调」页面
   - **选择「使用长连接接收事件」**（关键步骤！）
   - 添加事件：`im.message.receive_v1`（接收消息）
5. **发布应用**: 在「版本管理与发布」中创建版本并发布

### OpenAkita 配置

```bash
# 安装飞书依赖
pip install openakita[feishu]

# .env
FEISHU_ENABLED=true
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
```

### 部署模式

- **WebSocket 长连接（默认/推荐）**: 无需公网 IP，SDK 自动管理连接和重连
- 每个应用最多支持 50 个长连接
- 多实例部署时，消息会随机分发到其中一个连接

### 验证方法

1. 启动 OpenAkita，日志应显示 `Feishu adapter: WebSocket started in background`
2. 在飞书中搜索并打开机器人对话
3. 发送消息，观察日志和机器人回复

### 常见问题

- **消息收不到**: 检查是否在飞书后台启用了"长连接模式"而非 Webhook
- **权限不足**: 确认所有必要权限已申请且应用已发布
- **Token 过期**: SDK 会自动管理 Token 刷新，无需手动处理

---

## 钉钉

### 前置条件

- 企业钉钉账号
- 在 [钉钉开发者后台](https://open-dev.dingtalk.com/) 创建应用

### 平台侧配置

1. **创建应用**: 进入 [开发者后台](https://open-dev.dingtalk.com/) → 应用开发 → 企业内部开发 → 创建应用
2. **获取凭证**: 在「基础信息」→「应用凭证」中获取 AppKey 和 AppSecret
3. **配置机器人**:
   - 进入「应用功能」→「机器人」
   - 开启机器人功能
   - **消息接收模式选择「Stream 模式」**（关键步骤！）
4. **配置权限**: 根据需要添加消息相关权限
5. **发布应用**: 发布应用版本

### OpenAkita 配置

```bash
# 安装钉钉依赖
pip install openakita[dingtalk]

# .env
DINGTALK_ENABLED=true
DINGTALK_CLIENT_ID=xxx
DINGTALK_CLIENT_SECRET=xxx
```

### 部署模式

- **Stream 模式（WebSocket）**: 无需公网 IP，通过 WebSocket 长连接接收消息
- dingtalk-stream SDK 自动管理连接、重连和心跳

### 验证方法

1. 启动 OpenAkita，日志应显示 `DingTalk Stream client starting...`
2. 在钉钉中搜索并打开机器人对话（或在群中 @机器人）
3. 发送消息，观察日志和机器人回复

### 消息回复方式

- **Session Webhook**: 收到消息时会携带 `sessionWebhook`，用于回复当前会话（推荐）
- **机器人单聊 API**: 使用 `robot/oToMessages/batchSend` 主动发送（需要用户 ID）

### 常见问题

- **收不到消息**: 确认在钉钉后台已选择 Stream 模式（不是 HTTP 模式）
- **Stream 连接失败**: 检查 AppKey 和 AppSecret 是否正确
- **图片/文件发送**: 钉钉机器人消息对富媒体支持有限，部分会降级为链接

### 增强能力评估（暂不开发）

`dingtalk-stream` 0.22+ 引入的 **AI 卡片**（`CardReplier`）能在同一卡片内做"思考中 → 流式吐字 → 完成"三态展示，体验显著优于当前 sessionWebhook 文本回复。但本项目当前**未启用**该路径，原因：

- 属于"新功能开发"而非"SDK 升级红利"，需重写 `send_text` 流式路径、维护卡片 ID、实现增量更新协议（约 1~2 天工作量）。
- 缺少明确的钉钉用户场景反馈，无法判断收益是否值得。

**触发开发的条件**（满足任一即可立项）：

1. 实际钉钉部署用户提出"希望流式输出"的诉求；
2. OpenAkita 增加"主动卡片推送"类业务（如审批结果、告警通知卡片）。

在此之前，钉钉适配器保持现有的"sessionWebhook 文本被动回复 + 长输出自动分段"实现，避免引入未验证收益的代码复杂度。

> 关于 Stream 心跳：`dingtalk-stream` 0.24 自带连接级心跳。OpenAkita 在其之上额外做了**线程级 watchdog**（[`dingtalk.py`](../src/openakita/channels/adapters/dingtalk.py) 的 `_stream_watchdog`），监控线程是否存活并指数退避重启——这是更外一层的兜底，**不是重复造轮子**，保留。

---

## 企业微信

通过**智能机器人**接入企业微信，配置简单，不需要备案域名。

### 前置条件

- 企业微信管理员账号
- **公网可访问的 URL**（IP 即可，不需要备案域名）
- **先在 `.env` 中配好企业微信参数，再去后台创建机器人**（创建时会验证回调连通性）

### 平台侧配置

> ⚠️ **重要**：创建机器人时企业微信会立即向回调 URL 发送 GET 验证请求，所以必须先在本地配好并启动 OpenAkita，确保回调端口可从公网访问，然后再创建机器人。

1. **获取企业 ID (Corp ID)**: 进入 [企业微信管理后台](https://work.weixin.qq.com/) →「我的企业」→「企业信息」页面底部
2. **在 `.env` 中填好配置**（见下方 OpenAkita 配置），启动 OpenAkita
3. **创建智能机器人**: 应用管理 → 智能机器人 → 创建
4. **配置回调地址**:
   - 在机器人配置页面 → 「接收消息服务器配置」
   - **URL**: 填写回调地址，如 `http://your-ip:9880/callback`
   - **Token**: 自动生成或自定义，记下来填入 `.env`
   - **EncodingAESKey**: 自动生成或自定义，记下来填入 `.env`
   - 点击保存（企业微信会发送 GET 验证请求到 URL，需确保已连通）
5. **设置可见范围**（必须！）:
   - 在机器人配置页面 → 「可见范围」
   - 添加需要使用机器人的部门或员工
   - **只有在可见范围内的企业成员才能使用机器人**
   - 企业外部人员（客户、供应商等）无法使用

### 权限与使用范围

| 场景 | 是否可用 | 说明 |
|------|---------|------|
| 可见范围内的员工单聊 | ✅ | 直接在机器人对话中发消息 |
| 可见范围内的员工群聊 @机器人 | ✅ | 群聊中 @机器人触发回复 |
| **不在可见范围的员工** | ❌ | 看不到机器人，无法使用 |
| **企业外部人员** | ❌ | 外部联系人、客户群中的非企业成员无法触发机器人 |

> **群聊使用**：将机器人拉入群聊后，**只有在可见范围内的企业成员** @机器人才会触发回复。每个 @机器人的用户会创建独立的会话 session，互不干扰。

### OpenAkita 配置

```bash
# 安装企业微信依赖
pip install openakita[wework]

# .env
WEWORK_ENABLED=true
WEWORK_CORP_ID=ww_xxx

# 回调加解密（必填！在机器人配置页获取）
WEWORK_TOKEN=xxx
WEWORK_ENCODING_AES_KEY=xxx
WEWORK_CALLBACK_PORT=9880
```

### 消息回复机制

智能机器人使用 **stream 流式被动回复**发送消息：

1. 用户发消息 → 企业微信推送加密 JSON 到回调 URL
2. OpenAkita 被动回复初始 stream（`finish=false`，创建流式会话）
3. 企业微信定时发送 `msgtype=stream` 的刷新请求
4. Agent 处理完成 → 内容写入 stream session → 下次刷新时返回 `finish=true` + 完整内容

**stream 回复能力**:
- **文本**: 通过 `stream.content` 字段发送 markdown 文本
- **图片**: 通过 `stream.msg_item` 字段发送（base64+md5），支持 JPG/PNG，≤10MB，单条最多 10 张
- **文件/语音**: 不支持

**response_url 降级**:
- 当 stream session 超时（5.5 分钟）或不可用时，自动降级到 response_url 发送纯文本
- response_url 有效期 1 小时，仅可调用一次，仅支持 text/markdown

**群聊 session 隔离**:
- 群聊中每个用户的 @消息会创建独立的 stream session
- 用户 A 和用户 B 同时 @机器人，各自的回复互不干扰
- 回复路由通过 `chat_id + user_id` 精确匹配，不会串到其他用户

### 支持的消息类型

**接收消息** (用户 → 机器人):

| 类型 | 群聊 | 单聊 | 说明 |
|------|------|------|------|
| 文本 | ✅ (@机器人) | ✅ | |
| 图文混排 | ✅ (@机器人) | ✅ | |
| 图片 | ❌ | ✅ | |
| 语音 | ❌ | ❌ | 不支持接收 |
| 文件 | ❌ | ❌ | 不支持接收 |
| 引用消息 | ✅ (@机器人) | ✅ | |

> **注意**: 图片的下载 URL 经 AES 加密且仅 5 分钟有效，适配器会自动处理解密。

**发送消息** (机器人 → 用户):

| 类型 | 支持 | 说明 |
|------|------|------|
| 文本/Markdown | ✅ | stream 被动回复（文本+图片） |
| 图片 | ✅ | stream msg_item（base64+md5，JPG/PNG，≤10MB） |
| 文件 | ❌ | 不支持，降级为文本描述 |
| 语音 | ❌ | 不支持 |

### 验证方法

1. 在 `.env` 中配好参数，启动 OpenAkita，日志应显示 `WeWorkBot adapter started`
2. 确保回调 URL 可从公网访问（回调端口已监听）
3. 去企业微信管理后台创建智能机器人，配置回调地址
4. **设置可见范围**，添加需要使用的员工/部门
5. 在企业微信中找到智能机器人并发消息，或拉入群聊后 @机器人
6. 观察日志中的消息解密和处理记录

### 公网访问

企业微信 **不支持** WebSocket/长连接模式，只能通过 HTTP 回调接收消息。

- **公网服务器**: 直接在服务器上运行，回调 URL 指向服务器 IP/域名
- **路由器端口转发**: 如有公网 IP，在路由器中将外部端口转发到本机 IP 的 9880 端口
- **内网穿透（无公网 IP）**: 使用以下工具将本地端口映射到公网：

#### ngrok

```bash
# 安装 ngrok: https://ngrok.com/download
ngrok http 9880
# 获取公网 URL（如 https://abc123.ngrok-free.app）
# 将 https://abc123.ngrok-free.app/callback 填入企业微信后台
```

#### frp

```bash
# 在有公网 IP 的服务器上部署 frps
# 在本地配置 frpc:
[wework]
type = http
local_port = 9880
custom_domains = your-domain.com
```

#### cpolar

```bash
# 安装 cpolar: https://www.cpolar.com/
cpolar http 9880
```

### 常见问题

- **创建机器人时提示"网络失败"**: 回调 URL 不通，确保 OpenAkita 已启动、端口已监听、公网可访问
- **URL 验证失败**: 确认 Token 和 EncodingAESKey 与企业微信后台一致
- **签名校验失败**: 检查 Corp ID 是否正确
- **端口被占用**: 修改 `WEWORK_CALLBACK_PORT` 为其他端口
- **内网穿透不稳定**: 建议使用付费版 ngrok 或自建 frp
- **收不到消息**: 确认机器人回调地址配置正确，且服务器端口可访问
- **群聊中其他人 @不回复**: 检查该员工是否在机器人的「可见范围」内
- **回复乱码**: 确认 stream 回复的 JSON 使用了 `ensure_ascii=False`（适配器已处理）
- **图片发不出来**: 确认图片为 JPG/PNG 格式且 ≤10MB，stream session 未超时

---

## 微信个人号 (iLink Bot API)

通过 iLink Bot API 接入微信个人号，使用 HTTP 长轮询接收消息，无需公网 IP。与 OpenClaw 使用的微信接入方式相同。

> **协议跟随**：腾讯官方 OpenClaw 微信通道当前 CLI 已发布 `@tencent-weixin/openclaw-weixin-cli@2.1.x`、插件本体 `@tencent-weixin/openclaw-weixin@2.1.x`（2026-04），但 iLink HTTP 协议本体（`getupdates` / `sendmessage` / `getuploadurl` / AES-128-ECB / CDN）**未变**，OpenAkita 适配器无需改动。OpenClaw 用户侧的反推/参考安装命令仍是：
>
> ```bash
> npx -y @tencent-weixin/openclaw-weixin-cli@latest install
> ```
>
> 该命令只用于在 OpenClaw 宿主中安装微信通道插件，与 OpenAkita 的接入流程无直接关系——OpenAkita 直接调用 iLink 原生 HTTP API，不经过 OpenClaw。

### 前置条件

- 一个微信个人号
- 网络能访问 `ilinkai.weixin.qq.com`

### 获取 Token

通过 **扫码登录** 获取 Bearer Token：

1. **桌面端**: 在 Setup Center 中选择微信通道 → 点击「扫码登录」→ 用微信扫描二维码 → 手机确认 → Token 自动填入
2. **API 方式**: 调用 `/api/wechat/onboard/start` 获取二维码 → 调用 `/api/wechat/onboard/poll` 轮询状态 → 确认后返回 Token
3. **CLI 方式**: 运行 `python -m openakita.setup_center.bridge wechat-onboard-start` 获取二维码

### OpenAkita 配置

```bash
# 安装微信依赖
pip install openakita[wechat]

# .env
WECHAT_ENABLED=true
WECHAT_TOKEN=你的Bearer Token（扫码登录获取）
```

### 接入方式

- **HTTP 长轮询**: 通过 `ilink/bot/getupdates` 端点轮询消息，无需公网 IP
- 动态超时调整（3~30 秒），指数退避重试
- 自动处理 Session 过期（errcode=-14）暂停轮询

### 媒体处理

微信媒体文件通过 CDN 传输，使用 **AES-128-ECB** 加密：

- **下载**: 从 CDN 下载加密文件 → AES 解密 → 得到原始文件
- **上传**: 原始文件 → AES 加密 → 上传到 CDN → 获取 CDN Key
- CDN 基础 URL: `https://novac2c.cdn.weixin.qq.com/c2c`
- 加密密钥从 Token 中提取（base64 解码后取后 16 字节）

### 支持的消息类型

**接收消息** (用户 → 机器人):

| 类型 | 支持 | 说明 |
|------|------|------|
| 文本 | ✅ | |
| 图片 | ✅ | CDN 下载 + AES 解密 |
| 语音 | ✅ | SILK 格式，含 ASR 转文字 |
| 文件 | ✅ | CDN 下载 + AES 解密 |
| 视频 | ✅ | CDN 下载 + AES 解密 |

**发送消息** (机器人 → 用户):

| 类型 | 支持 | 说明 |
|------|------|------|
| 文本 | ✅ | Markdown 自动转纯文本 |
| 图片 | ✅ | AES 加密 + CDN 上传 |
| 文件 | ✅ | AES 加密 + CDN 上传 |
| 语音 | ✅ | AES 加密 + CDN 上传 |

### 特有功能

- **消息状态**: 支持 NEW → GENERATING → FINISH 三段式消息状态
- **打字指示器**: 通过 typing_ticket 显示「正在输入」
- **上下文延续**: 通过 context_token 维持对话上下文
- **消息去重**: 基于 msg_id 避免重复处理
- **同步游标持久化**: get_updates_buf 保存在 data/ 目录

### 验证方法

1. 通过扫码登录获取 Token，配置 `.env`
2. 启动 OpenAkita，日志应显示 `WeChat adapter: poll loop started`
3. 用另一个微信账号向机器人号发送消息
4. 观察日志和机器人回复

### 常见问题

- **Token 过期**: 重新扫码登录获取新 Token，errcode=-14 会自动暂停轮询
- **媒体解密失败**: 确认 Token 正确，AES 密钥从 Token 中提取
- **长轮询超时**: 正常行为，适配器会自动重试
- **Markdown 不显示**: 微信不支持 Markdown，适配器会自动转为纯文本

---

## QQ 官方机器人

### 前置条件

- 一个 [QQ 开放平台](https://q.qq.com) 账号
- 已创建机器人应用，获取 AppID 和 AppSecret

### OpenAkita 配置

```bash
# 安装 QQ 官方机器人依赖
pip install openakita[qqbot]

# .env
QQBOT_ENABLED=true
QQBOT_APP_ID=your-app-id
QQBOT_APP_SECRET=your-app-secret
QQBOT_SANDBOX=false          # 开发调试时设为 true
```

### 连接方式

- **WebSocket 长连接**: 自建 WebSocket 连接到 QQ Gateway，自动管理心跳和重连
- 支持自动断线重连（指数退避策略，初始 5 秒，最大 120 秒）
- 支持频道、群聊和单聊消息

### 富媒体说明

QQ 官方 API 在群聊/单聊场景下发送图片、语音、视频、文件采用两步操作：
1. 通过富媒体 API 上传文件获取 `file_info`，**支持两种方式（任选其一）**：
   - `file_data`：base64 编码的文件二进制（**推荐，本地文件直传，无需公网**）
   - `url`：公网可访问的媒体 URL
2. 发送消息时附带 `file_info`

OpenAkita 的 QQ 适配器**默认走 base64 本地直传**，仅在显式提供 `url` 时走 URL 上传，无需公网 IP/域名。

| 媒体类型 | file_type | 频道 | 群聊 | 单聊 (C2C) | 备注 |
|---------|-----------|------|------|-----------|------|
| 图片 | 1 | ✅ 直接 URL | ✅ base64 直传 | ✅ base64 直传 | png/jpg |
| 视频 | 2 | ❌ | ✅ base64 直传 | ✅ base64 直传 | mp4 |
| 语音 | 3 | ❌ | ✅ base64 直传 | ✅ base64 直传 | silk/wav/mp3/flac |
| 文件 | 4 | ❌ | ❌ 暂未开放 | ✅ base64 直传 | 任意文件 |

> **语音格式注意**：QQ 现已支持 `silk/wav/mp3/flac` 直接上传，OpenAkita `send_voice` 会原格式直传上述四种；其他格式（opus/ogg/amr/m4a/...）会自动调用 `ffmpeg` 转为 mp3 后上传。**不再需要 `pilk` 编码**——pilk 仅用于接收用户语音消息后做 SILK→WAV 解码（配合 Whisper 转写），需要时通过 `pip install openakita[voice]` 单独安装。

### 验证方法

1. 启动 OpenAkita，日志应显示 `QQ Official Bot ready (user: 你的机器人名称)`
2. 在 QQ 频道或群聊中 @机器人 发送消息
3. 观察日志和机器人回复

### 常见问题

- **鉴权失败**: 确认 AppID 和 AppSecret 正确，检查事件订阅是否已开启
- **群聊收不到消息**: 确认机器人已上线审核通过或在沙箱环境中配置了测试群
- **图片/语音/视频发不出去**: 检查文件大小是否超过腾讯单条消息限额；语音建议 ≤30s
- **群聊文件 (`file_type=4`) 发送失败**: 群聊场景 QQ 官方暂未开放，仅单聊可用
- **语音转码失败**: 非 silk/wav/mp3/flac 格式需要系统安装 `ffmpeg`，否则请先转码再发
- **断线重连**: 适配器支持自动重连，连接成功后自动重置退避计时

---

## OneBot（通用协议）

### 前置条件

- 一个 QQ 账号（或其他 OneBot 兼容平台账号）
- 部署 OneBot v11 实现（如 NapCat、Lagrange.OneBot）

### 部署 OneBot 服务器

OpenAkita 通过 OneBot v11 协议与中间层通信。你需要先部署一个 OneBot 实现：

#### NapCat（推荐）

```bash
# 参考: https://github.com/NapNeko/NapCatQQ
# 下载并配置 NapCat，启用正向 WebSocket
# 配置文件中设置 WebSocket 地址为 ws://127.0.0.1:8080
```

#### Lagrange.OneBot

```bash
# 参考: https://github.com/LagrangeDev/Lagrange.Core
# 下载并配置，启用正向 WebSocket
```

### OpenAkita 配置

```bash
# 安装 OneBot 依赖
pip install openakita[onebot]

# .env
ONEBOT_ENABLED=true
ONEBOT_WS_URL=ws://127.0.0.1:8080
ONEBOT_ACCESS_TOKEN=               # 可选，用于连接鉴权
```

### 部署模式

- **WebSocket 正向连接**: OpenAkita 连接到本地 OneBot 服务器，无需公网 IP
- 支持自动断线重连（指数退避策略，初始 1 秒，最大 60 秒）

### 验证方法

1. 先启动 OneBot 服务器（如 NapCat），确认 WebSocket 监听正常
2. 启动 OpenAkita，日志应显示 `OneBot adapter connected to ws://127.0.0.1:8080`
3. 在 QQ 中给机器人发消息（私聊或群聊 @机器人）
4. 观察日志和回复

### 文件发送说明

OneBot v11 的文件发送不支持 CQ 码，必须使用专用 API：
- 群文件: `upload_group_file`
- 私聊文件: `upload_private_file`

适配器已自动处理，通过 `deliver_artifacts` 工具发送文件时无需特别操作。

### 常见问题

- **连接失败**: 确认 OneBot 服务器已启动且 WebSocket 地址正确
- **鉴权失败**: 检查 `ONEBOT_ACCESS_TOKEN` 是否与服务端配置一致
- **断线重连**: 适配器支持自动重连，初始延迟 1 秒，最大延迟 60 秒
- **群/私聊判断**: 适配器会根据消息来源自动判断群聊或私聊

---

## 统一安装

安装所有 IM 通道依赖：

```bash
pip install openakita[all]
```

或按需安装：

```bash
pip install openakita[feishu]      # 飞书
pip install openakita[dingtalk]    # 钉钉
pip install openakita[wework]      # 企业微信
pip install openakita[wechat]      # 微信个人号
pip install openakita[qqbot]       # QQ 官方机器人
pip install openakita[onebot]      # OneBot（通用协议）

# 组合安装
pip install openakita[feishu,dingtalk,wechat,qqbot]
```

---

## 架构说明

```
平台消息 → Adapter (解析) → UnifiedMessage → Gateway (预处理) → Agent
                                                    ↓
Agent 回复 ← Adapter (发送) ← OutgoingMessage ← Gateway (路由)
```

- **ChannelAdapter**: 基类定义在 `src/openakita/channels/base.py`，各平台实现在 `src/openakita/channels/adapters/`
- **MessageGateway**: 统一消息路由、会话管理、媒体预处理，定义在 `src/openakita/channels/gateway.py`
- **deliver_artifacts**: Agent 工具，用于主动发送文件/图片/语音，定义在 `src/openakita/tools/handlers/im_channel.py`
