# 微信个人号 IM 适配器 — 功能清单 / 协议约束 / 已知限制

> 本文档记录微信个人号适配器（`wechat.py`）的功能、协议细节和与其他模块的交互逻辑。
> 基于 iLink Bot API 协议，与 OpenClaw WeChat 插件使用相同的接入方式。
> 目的：后续修改或修 bug 时不遗漏既有逻辑约束。

---

## 一、核心功能清单

### 1. 消息接收

| 功能 | 关键代码位置 | 说明 |
|------|------------|------|
| HTTP 长轮询 | `_poll_loop()` | 调用 `ilink/bot/getupdates` 端点，动态超时 3~30 秒 |
| 消息解析 | `_process_message()` | 从 update 中提取文本、媒体和元数据 |
| 文本提取 | `_extract_text_body()` | 提取消息正文文本 |
| 媒体检测 | `_find_media_item()` | 识别图片、语音、文件、视频附件 |
| 媒体下载 | `_download_media_item()` | CDN 下载 + AES-128-ECB 解密 |
| 消息去重 | `_dedup_check()` | 基于 msg_id 的 LRU 去重 |
| 同步游标 | `_save_sync_buf()` / `_load_sync_buf()` | 持久化 get_updates_buf 到 data/ 目录 |

### 2. 消息发送

| 功能 | 方法 | 说明 |
|------|------|------|
| 文本消息 | `_send_text()` | POST `ilink/bot/sendmessage`，附带 context_token |
| 媒体消息 | `_send_media_by_mime()` | AES 加密 → CDN 上传 → 发送 CDN Key |
| 消息状态 | `_send_text()` | 支持 NEW → GENERATING → FINISH 三段式 |
| Markdown 转换 | `_markdown_to_plaintext()` | 微信不支持 Markdown，自动转纯文本 |
| 打字指示器 | `send_typing()` / `clear_typing()` | 通过 typing_ticket 显示「正在输入」 |

### 3. 媒体处理

| 功能 | 方法 | 说明 |
|------|------|------|
| CDN 下载 | `_cdn_download()` | 从 CDN 下载并 AES-128-ECB 解密 |
| CDN 上传 | `_cdn_upload()` | AES-128-ECB 加密后上传到 CDN |
| AES 密钥解析 | `_parse_aes_key()` | 从 Bearer Token base64 解码后取后 16 字节 |
| 下载接口 | `download_media()` | ChannelAdapter 标准接口实现 |
| 上传接口 | `upload_media()` | ChannelAdapter 标准接口实现 |

### 4. 连接管理

| 功能 | 方法 | 说明 |
|------|------|------|
| 启动 | `start()` | 初始化 httpx 客户端，启动轮询任务 |
| 停止 | `stop()` | 取消轮询任务，关闭 httpx 客户端 |
| Session 过期处理 | `_is_session_paused()` / `_pause_session()` | errcode=-14 时暂停 30 分钟 |
| 指数退避 | `_poll_loop()` | API 错误时 2~60 秒指数退避 |
| 动态超时 | `_get_updates()` | 轮询超时随无消息次数递增（3~30 秒） |

---

## 二、协议细节

### API 端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `ilink/bot/getupdates` | POST | 长轮询获取新消息 |
| `ilink/bot/sendmessage` | POST | 发送消息（文本/媒体） |
| `ilink/bot/getconfig` | GET | 获取配置（typing_ticket 等） |
| `ilink/bot/fetchqrcode` | GET | 获取登录二维码 |
| `ilink/bot/pollqrstatus` | GET | 轮询扫码状态 |
| CDN: `novac2c.cdn.weixin.qq.com/c2c` | GET/PUT | 媒体文件上传下载 |

### 认证方式

- **Bearer Token**: 通过扫码登录获取，放在 `Authorization: Bearer <token>` 头中
- **X-WECHAT-UIN**: 额外认证头（从配置中提取）
- Token 过期时 API 返回 `errcode=-14`（SESSION_EXPIRED_ERRCODE）

### 消息格式

#### 长轮询请求

```json
{
  "get_updates_buf": "<base64 encoded sync cursor>"
}
```

#### 长轮询响应

```json
{
  "errcode": 0,
  "updates": [
    {
      "msg_id": "xxx",
      "from_user": "wxid_xxx",
      "context_token": "...",
      "body": { "content": "消息文本" },
      "media_list": [{ "type": "image", "cdn_key": "...", "aes_key": "..." }]
    }
  ],
  "get_updates_buf": "<new sync cursor>"
}
```

#### 发送消息请求

```json
{
  "context_token": "...",
  "body": { "content": "回复文本" },
  "message_state": "FINISH"
}
```

### AES-128-ECB 加密

- **算法**: AES-128-ECB（无 IV）
- **填充**: PKCS7
- **密钥来源**: Bearer Token → base64 解码 → 取后 16 字节
- **用途**: CDN 上传/下载的媒体文件加解密

### 消息状态 (message_state)

| 状态 | 用途 |
|------|------|
| NEW | 新消息开始 |
| GENERATING | 流式生成中（可选） |
| FINISH | 消息完成 |

### Session 过期处理

当 API 返回 `errcode=-14` 时：
1. 记录暂停时间戳
2. 暂停轮询 30 分钟（`SESSION_PAUSE_SECS = 1800`）
3. 30 分钟后自动恢复轮询
4. 需要用户重新扫码登录获取新 Token

---

## 三、配置项

| 环境变量 | 必填 | 说明 |
|---------|------|------|
| `WECHAT_ENABLED` | ✅ | 启用微信通道 |
| `WECHAT_TOKEN` | ✅ | Bearer Token（扫码登录获取） |
| `WECHAT_BASE_URL` | ❌ | API 基础 URL，默认 `https://ilinkai.weixin.qq.com` |
| `WECHAT_CDN_BASE_URL` | ❌ | CDN 基础 URL，默认 `https://novac2c.cdn.weixin.qq.com/c2c` |

---

## 四、与其他模块的交互

### MessageGateway

- 适配器通过 `self.emit_message(unified_msg)` 将消息投递到 Gateway
- Gateway 会自动下载图片和语音附件（通过 `download_media()`）
- 文本消息长度限制 4000 字符（`gateway.py` 中 `_CHANNEL_MAX_LENGTH["wechat"]`）

### 注册与依赖

- `registry.py`: `_create_wechat()` 工厂函数
- `deps.py`: `httpx` + `pycryptodome`
- `agents.py`: `"wechat"` 在 `VALID_BOT_TYPES` 中

### 扫码登录流程

```
Frontend → API: POST /api/wechat/onboard/start
API → iLink: GET /ilink/bot/fetchqrcode
← 返回: { uuid, qrcode_url, expire_seconds }

Frontend: 显示二维码，用户扫码

Frontend → API: POST /api/wechat/onboard/poll { uuid }
API → iLink: GET /ilink/bot/pollqrstatus?uuid=xxx
← 返回: { status: "wait" | "scaned" | "confirmed" | "expired", token? }

confirmed → Token 自动填入 .env
```

---

## 五、已知限制

1. **Token 会过期**: 微信登录态有时效限制，过期后需重新扫码
2. **单设备登录**: 同一微信号只能在一处使用 iLink Bot API
3. **Markdown 不支持**: 所有 Markdown 格式会被转为纯文本
4. **群聊**: iLink Bot API 主要用于单聊场景
5. **消息长度**: 单条消息最大 4000 字符
6. **非官方 API**: iLink Bot API 非腾讯官方公开 API，稳定性取决于腾讯策略
7. **频率限制**: 过于频繁的请求可能被限流
