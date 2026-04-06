# 微信个人号 IM 适配器 — 功能清单 / 协议约束 / 已知限制

> 本文档记录微信个人号适配器（`wechat.py`）的功能、协议细节和与其他模块的交互逻辑。
> 基于 iLink Bot API 协议，与 OpenClaw WeChat 插件使用相同的接入方式。
> 协议参考版本: @tencent-weixin/openclaw-weixin v2.1.6
> 目的：后续修改或修 bug 时不遗漏既有逻辑约束。

---

## 一、核心功能清单

### 1. 消息接收

| 功能 | 关键代码位置 | 说明 |
|------|------------|------|
| HTTP 长轮询 | `_poll_loop()` | 调用 `ilink/bot/getupdates` 端点，动态超时 3~30 秒 |
| 消息解析 | `_process_message()` | 从 update 中提取文本、媒体和元数据 |
| 文本提取 | `_extract_text_body()` | 提取消息正文文本 |
| 媒体检测 | `_find_media_item()` | 识别图片、语音、文件、视频附件，同时检查 `encrypt_query_param` 和 `full_url` |
| 媒体下载 | `_download_media_item()` | CDN 下载 + AES-128-ECB 解密，支持 `full_url` 直下载 |
| 消息去重 | `_dedup_check()` | 基于 msg_id 的 LRU 去重（TTL 10min, 最大 500 条） |
| 同步游标 | `_save_sync_buf()` / `_load_sync_buf()` | 持久化 get_updates_buf 到 data/ 目录 |
| Context Token 持久化 | `_save_context_tokens()` / `_load_context_tokens()` | 磁盘持久化，重启恢复 |
| 语音转码 | `_try_silk_to_wav()` | 可选 SILK→WAV 转码（需 pilk 依赖） |
| group_id 预留 | `_process_message()` | 当 `group_id` 存在时设为群聊模式 |
| 斜杠命令 | `_handle_slash_command()` | 支持 `/echo` 和 `/toggle-debug` 诊断命令 |

### 2. 消息发送

| 功能 | 方法 | 说明 |
|------|------|------|
| 文本消息 | `_send_text()` | POST `ilink/bot/sendmessage`，附带 context_token |
| 媒体消息 | `_send_media_by_mime()` | AES 加密 → CDN 上传 → 发送 CDN Key |
| 消息状态 | `_send_text()` | 支持 NEW → GENERATING → FINISH 三段式 |
| Markdown 转换 | `StreamingMarkdownFilter` | 逐字符状态机，支持流式/分段输出 |
| 打字指示器 | `send_typing()` / `clear_typing()` | 通过 typing_ticket 显示「正在输入」 |
| 发送失败通知 | `_send_error_notice()` | 火 forget 模式向用户发送可见错误提示 |
| 每用户限流 | `_rate_limit_wait()` | 最小间隔 2.5s + 指数退避重试（最多 4 次） |

### 3. 媒体处理

| 功能 | 方法 | 说明 |
|------|------|------|
| CDN 下载 | `_cdn_download()` | 从 CDN 下载并 AES-128-ECB 解密，支持 `full_url` 直下载 |
| CDN 上传 | `_cdn_upload()` | AES-128-ECB 加密后上传到 CDN，支持 `upload_full_url` |
| AES 密钥解析 | `_parse_aes_key()` | 支持 16-byte raw 和 32-char hex 两种格式 |
| 下载接口 | `download_media()` | ChannelAdapter 标准接口实现 |
| 上传接口 | `upload_media()` | ChannelAdapter 标准接口实现 |

### 4. 连接管理

| 功能 | 方法 | 说明 |
|------|------|------|
| 启动 | `start()` | 初始化 httpx 客户端，启动轮询任务 |
| 停止 | `stop()` | 取消轮询任务，关闭 httpx 客户端 |
| Session 过期处理 | `_is_session_paused()` / `_pause_session()` | errcode=-14 时暂停 1 小时 (`SESSION_PAUSE_DURATION_S = 3600`) |
| 指数退避 | `_poll_loop()` | API 错误时 2~30 秒指数退避 |
| 动态超时 | `_get_updates()` | 轮询超时随服务端 `longpolling_timeout_ms` 动态调整 |

---

## 二、协议细节

### API 端点

| 端点 | 方法 | 用途 |
|------|------|------|
| `ilink/bot/getupdates` | POST | 长轮询获取新消息 |
| `ilink/bot/sendmessage` | POST | 发送消息（文本/媒体） |
| `ilink/bot/getconfig` | POST | 获取配置（typing_ticket 等） |
| `ilink/bot/sendtyping` | POST | 发送/取消 typing 指示器 |
| `ilink/bot/getuploadurl` | POST | 获取 CDN 上传 URL |
| `ilink/bot/get_bot_qrcode` | GET | 获取登录二维码 |
| `ilink/bot/get_qrcode_status` | GET | 轮询扫码状态 |
| CDN: `novac2c.cdn.weixin.qq.com/c2c/download` | GET | 媒体文件下载 |
| CDN: `novac2c.cdn.weixin.qq.com/c2c/upload` | POST | 媒体文件上传 |

### 请求头

| 请求头 | 值 | 说明 |
|--------|-----|------|
| `Authorization` | `Bearer <token>` | 扫码登录获取的 token |
| `AuthorizationType` | `ilink_bot_token` | 固定值 |
| `X-WECHAT-UIN` | `base64(random_uint32)` | 随机生成 |
| `iLink-App-Id` | `"bot"` | 应用标识（可通过 `WECHAT_ILINK_APP_ID` 环境变量覆盖） |
| `iLink-App-ClientVersion` | `"131334"` (v2.1.6) | uint32 编码的版本号 (major<<16\|minor<<8\|patch) |
| `SKRouteTag` | 可选 | 从 credentials 中的 `route_tag` 字段读取，用于服务端路由 |

### 版本号编码

`channel_version` 放在每个请求的 `base_info` 中，当前值为 `OPENCLAW_COMPAT_VERSION`（默认 `"2.1.6"`）。

`iLink-App-ClientVersion` 通过 uint32 编码：`(major << 16) | (minor << 8) | patch`

- 2.1.6 → `0x00020106` → `131334`

可通过环境变量 `WECHAT_OPENCLAW_COMPAT_VERSION` 紧急覆盖。

### CDN 字段

服务端可能返回两种 CDN URL：

- **`encrypt_query_param`**: 传统方式，客户端拼接 CDN base URL + 此参数
- **`full_url`** (CDNMedia) / **`upload_full_url`** (GetUploadUrlResp): 完整 URL，优先使用

适配器优先使用 `full_url`/`upload_full_url`，回退到 `encrypt_query_param` 拼接。

CDN 错误响应会读取 `x-error-message` 响应头获取详细错误信息。

### 消息格式

#### 长轮询请求

```json
{
  "get_updates_buf": "<base64 encoded sync cursor>",
  "base_info": { "channel_version": "2.1.6" }
}
```

#### 长轮询响应

```json
{
  "ret": 0,
  "msgs": [
    {
      "message_id": 12345,
      "from_user_id": "wxid_xxx",
      "context_token": "...",
      "group_id": "",
      "item_list": [
        { "type": 1, "text_item": { "text": "消息文本" } }
      ],
      "create_time_ms": 1712345678000
    }
  ],
  "get_updates_buf": "<new sync cursor>",
  "longpolling_timeout_ms": 35000
}
```

#### 发送消息请求

```json
{
  "msg": {
    "from_user_id": "",
    "to_user_id": "wxid_xxx",
    "client_id": "openakita-wechat-xxxx",
    "message_type": 2,
    "message_state": 2,
    "item_list": [{ "type": 1, "text_item": { "text": "回复文本" } }],
    "context_token": "..."
  },
  "base_info": { "channel_version": "2.1.6" }
}
```

### AES-128-ECB 加密

- **算法**: AES-128-ECB（无 IV）
- **填充**: PKCS7
- **密钥来源**: 每个 media item 的 `aeskey` (hex) 或 `media.aes_key` (base64)
- **用途**: CDN 上传/下载的媒体文件加解密

### 消息状态 (message_state)

| 值 | 常量 | 用途 |
|----|------|------|
| 0 | MSG_STATE_NEW | 新消息开始 |
| 1 | MSG_STATE_GENERATING | 流式生成中（可选） |
| 2 | MSG_STATE_FINISH | 消息完成 |

### Session 过期处理

当 API 返回 `ret=-14` 或 `errcode=-14` 时：
1. 记录暂停时间戳
2. 暂停所有 API 调用 1 小时（`SESSION_PAUSE_DURATION_S = 3600`）
3. 1 小时后自动恢复轮询
4. 需要用户重新扫码登录获取新 Token

### 扫码登录流程

```
Frontend → API: POST /api/wechat/onboard/start
API → iLink: GET /ilink/bot/get_bot_qrcode?bot_type=3
  Headers: iLink-App-Id, iLink-App-ClientVersion
← 返回: { qrcode, qrcode_img_content }

Frontend: 显示二维码，用户扫码

Frontend → API: POST /api/wechat/onboard/poll { qrcode }
API → iLink: GET /ilink/bot/get_qrcode_status?qrcode=xxx
  Headers: iLink-App-Id, iLink-App-ClientVersion
← 返回: { status: "wait" | "scaned" | "scaned_but_redirect" | "confirmed" | "expired", ... }

scaned_but_redirect → 从 redirect_host 切换轮询目标，继续轮询
expired → 自动刷新二维码（最多 3 次），继续轮询
confirmed → 返回 bot_token / ilink_bot_id / baseurl
```

---

## 三、配置项

| 环境变量 | 必填 | 说明 |
|---------|------|------|
| `WECHAT_ENABLED` | ✅ | 启用微信通道 |
| `WECHAT_TOKEN` | ✅ | Bearer Token（扫码登录获取） |
| `WECHAT_BASE_URL` | ❌ | API 基础 URL，默认 `https://ilinkai.weixin.qq.com` |
| `WECHAT_CDN_BASE_URL` | ❌ | CDN 基础 URL，默认 `https://novac2c.cdn.weixin.qq.com/c2c` |
| `WECHAT_OPENCLAW_COMPAT_VERSION` | ❌ | 协议兼容版本号，默认 `2.1.6`，影响 `channel_version` 和 `iLink-App-ClientVersion` |
| `WECHAT_ILINK_APP_ID` | ❌ | iLink 应用 ID，默认 `bot` |
| `WECHAT_FOOTER_ELAPSED` | ❌ | 是否在回复尾部显示耗时，默认 `true` |

---

## 四、与其他模块的交互

### MessageGateway

- 适配器通过 `self._emit_message(unified_msg)` 将消息投递到 Gateway
- Gateway 会自动下载图片和语音附件（通过 `download_media()`）
- 文本消息长度限制 4000 字符（`gateway.py` 中 `_CHANNEL_MAX_LENGTH["wechat"]`）
- 分片发送间隔 2.5 秒（`_SPLIT_SEND_INTERVAL["wechat"]`）
- 进度消息节流 12 秒（`_CHANNEL_PROGRESS_THROTTLE["wechat"]`）
- Typing keepalive 由 Gateway 以 4 秒间隔调用 `send_typing()`

### 注册与依赖

- `registry.py`: `_create_wechat()` 工厂函数，支持 `route_tag` 可选字段
- `deps.py`: `httpx` + `pycryptodome` (必需)，`pilk` (可选，SILK→WAV 转码)
- `agents.py`: `"wechat"` 在 `VALID_BOT_TYPES` 中

### 日志脱敏

- `_redact_token()`: token 仅显示前 6 字符 + 长度
- `_redact_id()`: user_id 仅显示首尾各 4 字符
- `_redact_url()`: CDN URL 去除查询参数

### 诊断命令

| 命令 | 功能 |
|------|------|
| `/echo <text>` | 回显文本，附带平台延迟 |
| `/toggle-debug` | 开关 Debug 模式，开启后消息尾部附加统计信息 |

---

## 五、已知限制

1. **Token 会过期**: 微信登录态有时效限制，过期后需重新扫码
2. **单设备登录**: 同一微信号只能在一处使用 iLink Bot API
3. **Markdown 不支持**: 所有 Markdown 格式通过 `StreamingMarkdownFilter` 转为纯文本
4. **群聊**: iLink Bot API 主要用于单聊场景，`group_id` 字段已预留但尚未完整支持
5. **消息长度**: 单条消息最大 4000 字符
6. **非官方 API**: iLink Bot API 非腾讯官方公开 API，稳定性取决于腾讯策略
7. **频率限制**: 适配器内置 2.5s 最小间隔 + 指数退避重试
8. **语音格式**: 原始语音为 SILK 格式，WAV 转码需安装 `pilk` 可选依赖
