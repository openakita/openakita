# 企业微信 WebSocket 长连接适配器 — 功能清单 / 协议约束 / 已知限制

> 本文档记录企业微信 WebSocket 长连接适配器（`wework_ws.py`）的功能、协议细节和与其他模块的交互逻辑。
> 目的：后续修改或修 bug 时不遗漏既有逻辑约束。

---

## 一、核心功能清单

### 1. 消息接收

| 功能 | 关键代码位置 | 说明 |
|------|------------|------|
| WebSocket 长连接 | `_connection_loop()` | 主动连接 `wss://openws.work.weixin.qq.com` |
| 认证 | `_send_auth()` | 连接后立即发认证帧 (`aibot_subscribe`) |
| 心跳保活 | `_heartbeat_loop()` | 30s 间隔，连续 2 次无回复判定连接死亡 |
| 指数退避重连 | `_connection_loop()` | 1s → 2s → 4s → ... → 30s (cap)，默认无限重连 |
| 消息类型解析 | `_parse_content()` | 支持 text/image/mixed/voice/file/video |
| 事件类型解析 | `_handle_event_callback()` | enter_chat/template_card_event/feedback_event/disconnected_event |
| 消息去重 | `_seen_msg_ids` (OrderedDict) | 按 `msgid` 去重，10 分钟 TTL + 数量上限 500 双重淘汰 |

### 2. 消息发送

| 功能 | 方法 | 说明 |
|------|------|------|
| 流式文本回复 | `_send_stream_reply()` | `msgtype: "stream"`，透传 `req_id`，自动分片 (20480B) |
| 图片/文件/语音/视频回复 | `send_image/file/voice/video()` | WS 上传获取 media_id → 流式结束后追加发送 |
| 主动推送 (Markdown) | `_send_active_message()` | `cmd: "aibot_send_msg"`，自己生成 `req_id`，支持 `chat_type` |
| 主动推送 (媒体) | `_send_active_media_message()` | 支持 image/file/voice/video（需先 upload 获取 media_id） |
| response_url 回退 | `_response_url_fallback()` | WS 回复失败时通过 HTTP POST 回退 |
| Webhook 图片发送 | `_WebhookSender.send_image()` | base64+md5 直发 (fallback) |
| Webhook 语音发送 | `_WebhookSender.send_voice()` | 非 AMR 先转 AMR → upload_media → 发送 (fallback) |
| Webhook 文件发送 | `_WebhookSender.send_file()` | upload_media → 发送 (fallback) |

### 3. 文件处理

| 功能 | 方法 | 说明 |
|------|------|------|
| 文件下载 | `download_media()` | httpx GET，从 Content-Disposition 解析文件名 |
| AES-256-CBC 解密 | `_decrypt_file()` | per-file aeskey (base64)，iv=key[:16]，PKCS#7 pad 32B block |
| WS 分片上传 | `_ws_upload_media()` | init → chunk*N → finish，获取 media_id（有效期 3 天） |
| upload_media | `upload_media()` | 封装 `_ws_upload_media()`，返回 MediaFile |

### 4. 启动流程

| 步骤 | 说明 |
|------|------|
| `start()` | 导入 `websockets`，创建 `_connection_task` |
| `_connection_loop()` | 循环：连接 → 认证 → 心跳+接收 → 断开 → 退避重连 |
| `_connect_and_run()` | 单次连接生命周期 |
| `_send_auth()` | 发送 `{cmd: "aibot_subscribe", body: {bot_id, secret}}` |
| 等待认证响应 | 10s 超时，`errcode=0` 才启动心跳 |
| `_heartbeat_loop()` | 定时 ping，维持连接 |
| `_receive_loop()` | 读帧 → `_route_frame()` 分发 |

---

## 二、WebSocket 协议细节

### 通用帧格式

```json
{
  "cmd": "string | undefined",
  "headers": { "req_id": "prefix_timestamp_random8hex", ... },
  "body": { ... },
  "errcode": 0,
  "errmsg": "ok"
}
```

### 所有 cmd 值

| 方向 | cmd | 用途 |
|------|-----|------|
| 客户端 → 服务端 | `aibot_subscribe` | 认证订阅 |
| 客户端 → 服务端 | `ping` | 心跳 |
| 客户端 → 服务端 | `aibot_respond_msg` | 回复消息 |
| 客户端 → 服务端 | `aibot_respond_welcome_msg` | 回复欢迎语 |
| 客户端 → 服务端 | `aibot_respond_update_msg` | 更新模板卡片 |
| 客户端 → 服务端 | `aibot_send_msg` | 主动推送消息 |
| 客户端 → 服务端 | `aibot_upload_media_init` | 上传初始化 |
| 客户端 → 服务端 | `aibot_upload_media_chunk` | 上传分片 |
| 客户端 → 服务端 | `aibot_upload_media_finish` | 上传完成 |
| 服务端 → 客户端 | `aibot_msg_callback` | 消息推送 |
| 服务端 → 客户端 | `aibot_event_callback` | 事件推送 |

### 帧路由优先级 (`_route_frame`)

1. `cmd = "aibot_msg_callback"` → 消息处理
2. `cmd = "aibot_event_callback"` → 事件处理
3. 无 cmd + req_id 在 `_pending_acks` → 回复/上传回执
4. 无 cmd + req_id 以 `aibot_subscribe` 开头 → 认证响应
5. 无 cmd + req_id 以 `ping` 开头 → 心跳响应
6. 其他 → 日志记录

### 支持的消息类型

| 消息类型 | msgtype | 说明 |
|---------|---------|------|
| 文本消息 | text | 用户发送的文本内容 |
| 图片消息 | image | 用户发送的图片，仅支持单聊 |
| 图文混排 | mixed | 用户发送的图文混排内容 |
| 语音消息 | voice | 用户发送的语音（转为文本），仅支持单聊 |
| 文件消息 | file | 用户发送的文件，仅支持单聊 |
| 视频消息 | video | 用户发送的视频，仅支持单聊 |

### 支持的事件类型

| 事件类型 | eventtype | 说明 |
|---------|-----------|------|
| 进入会话 | enter_chat | 用户当天首次进入机器人单聊会话 |
| 模板卡片点击 | template_card_event | 用户点击模板卡片按钮 |
| 用户反馈 | feedback_event | 用户对机器人回复进行反馈 |
| 连接断开 | disconnected_event | 新连接踢掉旧连接时推送给旧连接 |

### 回复规则

- **回复消息**：透传收到消息的 `req_id`
- **主动推送**：自己生成 `req_id`（前缀 `aibot_send_msg`）
- **串行队列**：同一 `req_id` 的回复串行发送，每条等回执后才发下一条
- **回执超时**：15 秒 (D2，从 5s 调整为 15s 对齐 OpenClaw)
- **流式内容上限**：20480 字节/片（UTF-8），自动分片
- **中间流消息上限**：85 帧 (D1，WeCom SDK 限制约 100 帧，保留余量)

### 频率限制 (D9，对齐 OpenClaw)

| 限制 | 值 |
|------|-----|
| 回复频率 | 30 条/24h 滑动窗口/会话（收到新入站消息时重置） |
| 主动发送频率 | 10 条/天/会话 |
| 上传频率 | 30 次/分钟，1000 次/小时 |
| 回复窗口 | 收到消息后 24 小时内 |
| 流式消息超时 | 首帧发送后 **6 分钟**内必须 finish=true |
| 欢迎语/卡片更新超时 | 收到事件后 **5 秒**内必须回复 |

### 上传临时素材协议

```
_ws_upload_media(path, mime_type) -> media_id

1. init:   {cmd: "aibot_upload_media_init", body: {type, filename, total_size, total_chunks, md5}}
           → 响应: {body: {upload_id}}
2. chunk:  {cmd: "aibot_upload_media_chunk", body: {upload_id, chunk_index, base64_data}}
           → 逐片上传，每片 ≤ 512KB（base64 编码前），最多 100 片
           → 分片可乱序上传，重复上传同一分片会被忽略（幂等）
3. finish: {cmd: "aibot_upload_media_finish", body: {upload_id}}
           → 响应: {body: {type, media_id, created_at}}
```

上传约束：
- 图片: ≤ 2MB (png/jpg/gif)
- 语音: ≤ 2MB (amr)
- 视频: ≤ 10MB (mp4)
- 文件: ≤ 20MB
- 上传会话有效期 30 分钟
- media_id 有效期 3 天

---

## 三、与 HTTP 回调适配器的对比

| 特性 | HTTP 回调 (`wework_bot.py`) | WebSocket (`wework_ws.py`) |
|------|---------------------------|---------------------------|
| 连接方式 | 被动 HTTP 服务器 | 主动 WebSocket 客户端 |
| 需要公网 | 是（回调地址） | 否（出站连接即可） |
| 认证方式 | corp_id + token + encoding_aes_key | bot_id + secret |
| 消息加密 | AES-256-CBC (全局 encoding_aes_key) | 无加密（WSS 传输层加密） |
| 文件解密 | 全局 encoding_aes_key | per-file aeskey |
| 流式回复 | 支持（HTTP 轮询刷新） | 原生支持（WebSocket stream） |
| 主动推送 | 通过 response_url | 通过 `aibot_send_msg` cmd |
| 媒体上传 | 不支持 | WS 分片上传 → media_id |
| 心跳/重连 | 不需要 | 30s 心跳，指数退避重连 |
| `supports_streaming` | False | True |

---

## 四、关键逻辑约束（修改时必须保持）

### 约束 1：req_id 透传规则

- **回复消息**时必须使用收到消息帧中的 `req_id`（不能重新生成）
- **主动推送**时必须使用自己生成的 `req_id`（前缀 `aibot_send_msg`）
- **上传操作**时必须使用自己生成的 `req_id`（前缀 `aibot_upload_media_*`）
- 服务端通过 `req_id` 关联消息与回复

### 约束 2：流式回复串行性

- 同一 `req_id` 的多个 stream 帧必须串行发送
- 每发一帧必须等待服务端回执（`errcode: 0`）后才能发下一帧
- 不能并行发送同一 `req_id` 的帧（会导致消息乱序或丢失）

### 约束 3：图片/媒体发送方式

- ~~`stream.msg_item`（base64 图片）~~ **已废弃**（2026/03 官方文档明确 "目前暂不支持 msg_item 字段"）
- 新方式：先通过 WS 分片上传获取 `media_id`，再通过 `aibot_respond_msg` 发送 `{msgtype: "image", image: {media_id}}` 消息
- 流式回复中的媒体：先 finish stream → 再追加 media 消息（使用同一 `req_id`）
- 主动推送媒体：`aibot_send_msg` + `{msgtype: "image/file/voice/video", ...}`

### 约束 4：心跳超时判定

- 发心跳**之前**检查 `missed_pong` 计数
- 收到心跳响应时重置为 0
- 连续 2 次未收到 → `ws.close()` → 触发重连
- 不能在发心跳**之后**才检查（否则错过 1 个周期）

### 约束 5：认证必须在连接后立即发送

- WebSocket `open` 后第一帧必须是认证帧
- 认证超时 10 秒
- 认证失败不启动心跳，直接断开进入重连

### 约束 6：is_mentioned 的平台特性

- WebSocket 模式下，企业微信**只推送**涉及机器人的消息
- 因此所有收到的消息 `is_mentioned = True`（平台已预过滤）
- 与 HTTP 回调模式不同（HTTP 收到所有群消息，需要自己判断 is_mentioned）

### 约束 7：response_url 的生命周期

- 每条消息附带 `response_url`，有效期约 5 分钟
- 缓存在 `_response_urls` dict 中（按 req_id 索引）
- WS 回复失败时作为 HTTP POST 回退
- 定期清理，保留最近 200 条

### 约束 8：连接唯一性

- 每个机器人同一时间只能保持一个有效长连接
- 新连接 subscribe 成功后，旧连接会收到 `disconnected_event` 并被服务端断开
- 需要在业务层避免同一机器人多连接

### 约束 9：流式消息 6 分钟超时

- 从首帧发送开始计时，6 分钟内必须设置 `finish=true`
- 超时后消息自动结束，不可再更新
- HTTP 模式的 `STREAM_TIMEOUT=330s` 约束不同，WS 模式为 360s

---

## 五、配置说明

### .env 配置

```ini
# 企业微信 WebSocket 长连接模式
WEWORK_WS_ENABLED=true
WEWORK_WS_BOT_ID=your_bot_id
WEWORK_WS_SECRET=your_bot_secret

# 可选：群机器人 Webhook URL（作为 fallback 通道，WS 上传失败时使用）
# 在企业微信群设置 → 群机器人 → 添加机器人 → 获取 Webhook 地址
WEWORK_WS_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

### im_bots JSON 配置（多 Bot 模式）

```json
{
  "type": "wework_ws",
  "bot_id": "your_bot_id",
  "secret": "your_bot_secret",
  "ws_url": "wss://openws.work.weixin.qq.com",
  "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
}
```

### 注意事项

- HTTP 回调模式和 WebSocket 模式可以**同时启用**（不同的 bot_id）
- WebSocket 模式**不需要公网 IP**，适合开发和内网部署
- `bot_id` 和 `secret` 在企业微信管理后台的智能机器人配置页面获取
- 模式切换影响：API 模式只能选择一种方式（长连接或回调地址），切换会使另一种失效

---

## 六、数据流概览

### 消息接收流程

```
企业微信 WebSocket 服务端
  → JSON 帧: {cmd: "aibot_msg_callback", headers: {req_id}, body: {msgid, ...}}
    → _receive_loop()              # async for msg in ws
      → _route_frame()             # 按 cmd 分发
        → _handle_msg_callback()   # 消息去重 + 解析
          → _parse_content()       # text/image/mixed/voice/file/video → MessageContent
          → UnifiedMessage.create()
          → _emit_message()        # 触发 gateway 回调
```

### 消息发送流程 (回复)

```
Agent 生成回复
  → gateway 构造 OutgoingMessage (metadata.req_id = 收到消息的 req_id)
    → adapter.send_message()
      → _send_stream_reply()
        → 分片 (每片 ≤ 20480 字节)
        → for each chunk:
            → _send_reply_with_ack(req_id, body, "aibot_respond_msg")
              → ws.send(frame)
              → await ack (5s timeout)
        → 最后一片: finish=true
        → 追加媒体消息 (images/files uploaded via _ws_upload_media)
```

### 消息发送流程 (主动推送)

```
Agent 主动发送
  → gateway 构造 OutgoingMessage (无 req_id)
    → adapter.send_message()
      → _send_active_message(chat_type=1|2)
        → _send_reply_with_ack(自生成 req_id, body, "aibot_send_msg")
```

### 媒体上传流程

```
send_image/file/voice/video()
  → _ws_upload_media(path, mime_type)
    → aibot_upload_media_init → upload_id
    → aibot_upload_media_chunk × N (每片 ≤ 512KB)
    → aibot_upload_media_finish → media_id
  → 排入 _pending_media_msgs (回复模式) 或直接发送 (主动推送)
```

### 连接生命周期

```
start()
  → _connection_loop() [asyncio.Task]
    → while running:
        → _connect_and_run()
          → websockets.connect()
          → _send_auth()
          → wait authenticated (10s)
          → asyncio.gather:
              → _heartbeat_loop() [每 30s ping]
              → _receive_loop()   [读帧]
        → on disconnect / disconnected_event:
          → 指数退避 (1s, 2s, 4s, ... 30s cap)
          → retry
```

---

## 七、已知限制（后续迭代处理）

| # | 严重度 | 问题 | 说明 |
|---|--------|------|------|
| 1 | 中 | 模板卡片回复未完整实现 | 已预留 cmd 常量，但 `send_message` 尚未支持构建模板卡片；需要 `OutgoingMessage` 扩展 |
| 2 | ~~中~~ 已完成 | ~~欢迎语回复未自动化~~ | `__init__` 新增 `welcome_message` 参数，`enter_chat` 事件自动回复 |
| 3 | 中 | 更新模板卡片未实现 | `aibot_respond_update_msg` cmd 已预留，等待业务需求 |
| 4 | ~~低~~ 已完成 | ~~流式回复中断无恢复~~ | 新增 `_pending_replies` 队列，WS 断连时暂存最终回复，重连后通过 response_url 或 active push 重试 |
| 5 | 低 | response_url 缓存无 TTL | 仅按数量清理（200 条），未按时间清理过期 URL |
| 6 | ~~低~~ 已完成 | ~~引用消息 (quote) 未解析~~ | `_parse_quote_content()` 解析 `body.quote`，支持 text/image/voice/file/mixed，前缀到消息内容 |
| 7 | ~~低~~ 已完成 | ~~语音消息只取转文字结果~~ (已优化) | 同上；另新增 ffmpeg 转换失败时自动降级为文件发送 |
| 8 | 低 | feedback.id 未传递 | 回复/推送消息的 markdown/template_card 支持 `feedback.id` 字段以追踪用户反馈，当前未使用 |

---

## 八、修改检查清单

修改企业微信 WebSocket 适配器相关代码时，请逐一确认：

- [ ] 回复消息是否透传了原始 `req_id`（而非重新生成）？
- [ ] 主动推送是否使用了自己生成的 `req_id`（前缀 `aibot_send_msg`）？
- [ ] 主动推送是否传递了 `chat_type` 字段（1=单聊，2=群聊）？
- [ ] 同一 `req_id` 的回复是否保持串行（经过 `_reply_locks`）？
- [ ] 流式回复的每一片是否等待了回执？
- [ ] 媒体消息是否通过 `_ws_upload_media` 上传后用 `media_id` 发送（而非 msg_item）？
- [ ] 新增的事件类型是否在 `_handle_event_callback` 中处理？
- [ ] `disconnected_event` 是否设置 `_displaced=True` 并停止重连（而非仅关闭连接）？
- [ ] 心跳超时判定是否在发送前检查？
- [ ] `_reject_all_pending` 是否在断开/重连时调用（包括清理 `_pending_media_msgs`、`_thinking_tasks`）？
- [ ] `is_mentioned` 是否保持为 True（平台已预过滤）？
- [ ] 语音输出格式是否与 `wework_bot.py` 保持一致（转写成功→直接文本，失败→统一提示）？
- [ ] `_WebhookSender` 是否在 `stop()` 时关闭 httpx 客户端？
- [ ] 媒体发送是否优先 WS upload → fallback webhook → fallback markdown hint？
- [ ] 长耗时流式回复是否有 keepalive timer 防止 6 分钟超时？
- [ ] keepalive 是否考虑了最近流帧发送时间（D6 智能跳过）？
- [ ] 消息处理是否有超时保护（`_handle_msg_callback_safe`）？
- [ ] 消息处理异常是否发送了 `finish=true` 关闭流（D4）？
- [ ] 同一对话的消息处理是否经过 `_peer_locks` 串行化？
- [ ] 超尺寸媒体是否通过 `_check_upload_size` 自动降级为 file 类型？
- [ ] 非 AMR 语音是否自动降级为 file 类型（D5）？
- [ ] 语音发送 ffmpeg 失败时是否降级为文件发送（而非抛异常）？
- [ ] 中间流消息（finish=false）是否在 85 帧上限内（D1）？
- [ ] think 标签是否通过 `_normalize_think_tags()` 归一化（D3）？
- [ ] 媒体发送失败后是否向用户发送了错误通知（D11）？

---

## 九、变更记录

### 2026-03-19 (六): 修复 flush_progress 绕过 F2 提取

**问题**: F1-F4 修复了 `emit_progress_event` 的中间节流 flush，但 `agent.py` 在返回 response_text 之前显式调用 `gateway.flush_progress(session)`（line 4141），该方法未检查 `_THINK_TAG_NATIVE`，仍将累积的进度缓冲区作为独立消息发送。当 gateway 的 F2 提取逻辑随后运行时，缓冲区已为空。

**修复**:

- `gateway.py` — `flush_progress()`: 新增 `_THINK_TAG_NATIVE` 检查，对支持原生 `<think>` 的适配器直接返回，保留 buffer 供 F2 在回复时提取

### 2026-03-18 (五): 修复进度事件泄露为独立消息

**问题**: 前一轮修复（P1-P3）仅在最终回复时从 progress buffer 提取 💭 行，但 `emit_progress_event` 的 2 秒节流 `_flush()` 在推理期间已将进度以独立消息发送，导致最终回复时缓冲区为空。此外，仅提取 💭 行而遗漏了 🔧/✅ 等工具进度行。移动端思考指示器因累积式内容和未闭合 `</think>` 标签无法折叠。

**修复**:

- **F1** `gateway.py` — `emit_progress_event()`: 对 `_THINK_TAG_NATIVE` 适配器跳过节流刷新任务，仅累积到 buffer；所有进度行在回复时统一提取
- **F2** `gateway.py` — 非流式/流式回复路径: 从 buffer 提取**全部**进度行（而非仅 💭），统一包裹 `<think>` 标签整合到回复文本
- **F3** `wework_ws.py` — `_thinking_counter_loop()` / `_maybe_send_thinking_indicator()`: 思考指示器改为单行内容 + 闭合 `</think>` 标签，修复移动端不折叠问题

### 2026-03-18 (四): 思考内容整合到回复流

**问题**: 当 `IM_CHAIN_PUSH=true` 时，模型的 thinking content 被 gateway 以独立 markdown 消息（💭）推送给用户，与 WeCom 原生 `<think>` 折叠块体验冲突。

**修复**:

- **P1** `gateway.py`: 在 `flush_progress` 前检查 adapter 是否声明 `_THINK_TAG_NATIVE`；若是，从 progress buffer 提取 💭 行并整合到 response_text 的 `<think>` 标签中，而非作为独立消息推送
- **P2** `wework_ws.py`: 新增类属性 `_THINK_TAG_NATIVE = True`，声明该 adapter 原生支持 think 标签渲染

### 2026-03-18 (三): 深度优化 & QR 修正 (D0-D11)

基于 `openclaw-plugin-wecom` 和 `@wecom/wecom-openclaw-cli` 源码深度对比，完成 14 项改动：

**QR 扫码修正 (D0a-D0c)：**

- **D0a** `wecom_onboard.py`: 域名从 `developer.work.weixin.qq.com` 修正为 `work.weixin.qq.com`；HTTP 方法从 POST 改为 GET；响应字段从 `qr_url`/`qr_id` 修正为 `auth_url`/`scode`，bot_info 从 `resp.data.bot_info.{botid, secret}` 解析
- **D0b** 全链路 `qr_id` → `scode` 适配：routes、bridge.py、main.rs、WecomQRModal.tsx
- **D0c** IMView.tsx 添加 `wework_ws` 类型的"扫码配置机器人"按钮

**适配器优化 (D1-D6, D9-D11)：**

1. **D1 中间流消息上限**: 新增 `MAX_INTERMEDIATE_STREAM_MSGS=85` 常量 + `_stream_msg_count` 计数器，在 thinking counter/keepalive/stream reply 中检查
2. **D2 回执超时调整**: `reply_ack_timeout` 从 5s 提高到 15s，对齐 OpenClaw `REPLY_SEND_TIMEOUT_MS=15000`
3. **D3 think 标签归一化**: 新增 `_normalize_think_tags()` 修正未闭合/多余 `<think>` 标签，在 stream reply 和 active message 中调用
4. **D4 异常兜底 finish=true**: `_handle_msg_callback_safe` 新增通用 `Exception` 捕获，确保任何处理错误都会发送 `finish=true` 关闭流
5. **D5 语音格式校验**: `_check_upload_size` 新增 `mime_type` 参数，非 AMR 语音自动降级为 file 类型
6. **D6 智能 keepalive**: `_stream_keepalive_loop` 跟踪 `_last_stream_sent`，最近发过流帧时跳过冗余 keepalive
7. **D7 quote.file 确认**: 验证 `_parse_quote_content` 已正确处理 `quote.msgtype == "file"`（无需修改）
8. **D9 频率限制重构**: `_RateLimitTracker` 重构为双模型：回复 30/24h 滑动窗口（入站重置）+ 主动发送 10/天/会话，对齐 OpenClaw
9. **D10 思考指示器首帧**: 首帧内容从 `"等待模型响应 1s"` 改为 `"<think>等待模型响应 1s"`，从第一帧即激活 WeCom 思考动画
10. **D11 媒体失败反馈**: 流回复后媒体发送失败时，通过 active message 向用户发送错误通知文本

### 2026-03-18 (二): 适配器健壮性优化 & 扫码配置

基于 `openclaw-plugin-wecom` 插件源码分析，完成 12 项适配器优化和扫码配置功能：

**适配器优化 (A1-A12)：**

1. **A1 流式 Keepalive**: 新增 `_stream_keepalive_loop()`，每 4 分钟发送 `finish=false` 帧防止 6 分钟超时
2. **A2 引用消息解析**: 新增 `_parse_quote_content()` 解析 `body.quote`，支持 text/image/voice/file/mixed 类型引用
3. **A3 媒体类型自动降级**: `_validate_upload_size()` 重构为 `_check_upload_size()`，超尺寸 image/voice/video 自动降级为 file 类型
4. **A4 Displaced 停止重连**: `disconnected_event` 设置 `_displaced=True` + `_running=False`，防止被踢后无限重连
5. **A5 消息处理超时保护**: `_handle_msg_callback_safe()` 包装 `asyncio.wait_for(timeout=300s)`，超时发送提示并关闭流
6. **A6 Per-Peer 消息序列化锁**: `_peer_locks` 按 `chat_id` 串行化，防止同一对话消息并发乱序
7. **A7 可配置欢迎消息**: `__init__` 新增 `welcome_message` 参数，`enter_chat` 事件触发 `CMD_RESPONSE_WELCOME`
8. **A8 动画思考指示器**: `_thinking_counter_loop()` 每秒更新 "等待模型响应 Ns"，收到回复时自动取消
9. **A9 失败回复队列**: `_pending_replies` 队列（TTL 5min，max 50），WS 断连时暂存，重连后 `_flush_pending_replies()` 重试
10. **A10 频率限制追踪**: `_RateLimitTracker` 按 chat_id 滑动窗口追踪 reply/min 和 reply/hour，80% 阈值告警
11. **A11 语音优雅降级**: `send_voice()` ffmpeg 转换失败时自动降级为文件发送，不再抛异常
12. **A12 消息去重 TTL 清理**: `_seen_msg_ids` 改为 `OrderedDict[str, float]`，增加 10 分钟 TTL 过期 + 数量上限双重淘汰

**扫码配置 (B1-B5)：**

- 新增 `wecom_onboard.py`: 封装 `/ai/qc/generate` 和 `/ai/qc/query_result` API
- 新增 `api/routes/wecom_onboard.py`: FastAPI 路由 `/api/wecom/onboard/start|poll`
- 新增 `WecomQRModal.tsx`: React 组件，复用 FeishuQRModal 架构
- 修改 `IMConfigView.tsx`: 企微 WS 模式增加 "扫码配置机器人" 按钮
- 修改 `main.rs`, `bridge.py`: 新增 `wecom-onboard-start/poll` Tauri 命令
- 新增 zh/en i18n 文案

### 2026-03-18: 协议对齐 & 功能补全

对照官方长连接协议文档（更新于 2026/03/13），完成以下变更：

1. **新增 WS 分片上传临时素材**：实现 `aibot_upload_media_init/chunk/finish` 三步上传协议，`upload_media()` 不再抛 `NotImplementedError`
2. **修复图片发送**：`msg_item` 字段已被官方废弃，改为 WS 上传获取 `media_id` + 独立 image 消息发送
3. **新增视频消息接收**：`_parse_content()` 支持 `video` 类型（含 aeskey 解密）
4. **新增 `disconnected_event` 处理**：收到时优雅关闭连接，触发重连
5. **主动推送增强**：`_send_active_message()` 支持 `chat_type` 参数；新增 `_send_active_media_message()` 支持 image/file/voice/video
6. **新增 `send_video()` 方法**：通过 WS 上传发送视频消息
7. **媒体发送优先级调整**：WS upload (首选) → Webhook (fallback) → Markdown hint (最终 fallback)
8. **send_file/send_voice 增强**：均可通过 WS 上传直接发送，不再依赖 Webhook

---

## 十、协议参考

- 官方长连接文档: https://developer.work.weixin.qq.com/document/path/101463
- 官方 API 模式文档: https://developer.work.weixin.qq.com/document/path/101468
- Node.js SDK: https://github.com/WecomTeam/aibot-node-sdk (MIT)
- Python SDK: https://dldir1.qq.com/wework/wwopen/bot/aibot-python-sdk-1.0.0.zip
- OpenClaw 官方插件: https://github.com/WecomTeam/wecom-openclaw-plugin (MIT)
- 本适配器参考官方协议文档独立实现
