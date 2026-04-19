# 钉钉 IM 适配器 — 功能清单 / 协议约束 / 已知限制

> 本文档记录钉钉适配器（`dingtalk.py`）的功能、协议细节和与其他模块的交互逻辑。
> 参考对比：AstrBot (`dingtalk_adapter.py`)、koishi (`@koishijs/plugin-adapter-dingtalk`)。
> 目的：后续修改或修 bug 时不遗漏既有逻辑约束。

---

## 一、核心功能清单

### 1. 消息接收

| 功能 | 关键代码位置 | 说明 |
|------|------------|------|
| Stream 长连接 | `_start_stream()` | 基于 `dingtalk-stream` SDK 的 WebSocket 长连接，无需公网 IP |
| 消息回调 | `_ChatbotHandler.process()` | ACK 先行：立即返回 ACK，异步处理消息 |
| 消息解析 | `_handle_stream_message()` | 从 `callback.data` 原始字典手动解析基础字段 |
| 内容解析 | `_parse_message_content()` | 支持 text/picture/richText/audio/video/file |
| @检测 | `_handle_stream_message()` L287-297 | `isInAtList` 字段 + 遍历 `atUsers` 列表 |
| 线程投递 | `run_coroutine_threadsafe()` | Stream 线程 → 主事件循环 |

### 2. 消息发送

| 功能 | 方法 | 说明 |
|------|------|------|
| 智能路由 | `send_message()` | 优先 SessionWebhook → 回退 OpenAPI |
| Webhook 发送 | `_send_via_webhook()` | 仅支持 text/markdown 类型 |
| 群聊 OpenAPI | `_send_group_message()` | `POST /v1.0/robot/groupMessages/send` |
| 单聊 OpenAPI | `_send_via_api()` | `POST /v1.0/robot/oToMessages/batchSend` |
| Markdown 发送 | `send_markdown()` | 单聊专用便捷方法 |
| 卡片消息 | `send_action_card()` | 单聊专用便捷方法 |
| Typing 提示 | `send_typing()` | 优先 AI Card (382e4302 模板)，降级 StandardCard |
| Typing 清理 | `clear_typing()` | 更新残留卡片为"处理完成"，正常路径由 send_message 消费 |
| AI Card 创建 | `_create_ai_card()` | `POST /v1.0/card/instances` + `POST /v1.0/card/instances/deliver` |
| AI Card 流式更新 | `_stream_ai_card()` | `PUT /v1.0/card/streaming` (流式) / `PUT /v1.0/card/instances` (FINISHED) |
| StandardCard 发送 | `_send_interactive_card()` | `POST /v1.0/im/v1.0/robot/interactiveCards/send` (降级方案) |
| StandardCard 更新 | `_update_interactive_card()` | `PUT /v1.0/im/robots/interactiveCards` (降级方案) |
| 文本分块 | `_chunk_markdown_text()` | 4000 字符限制，markdown-aware 分块 |
| 图片发送 | `send_image()` | 上传 → OpenAPI → Webhook markdown 嵌入 → 文本降级 |
| 文件发送 | `send_file()` | 上传 → OpenAPI → 文本降级 |
| 语音发送 | `send_voice()` | 委托给 `send_file()`（Webhook 不支持语音） |

### 3. 文件处理

| 功能 | 方法 | 说明 |
|------|------|------|
| 文件下载 | `download_media()` | 先获取 downloadUrl 再 GET 下载 |
| 文件上传 | `upload_media()` | 旧版 `oapi.dingtalk.com/media/upload` |

### 4. 启动流程

| 步骤 | 说明 |
|------|------|
| `start()` | 导入 httpx/dingtalk_stream，创建 HTTP 客户端，刷新 token |
| `_start_stream()` | 后台线程创建新事件循环，初始化 `DingTalkStreamClient` |
| `loop.run_until_complete(client.start())` | 在受控事件循环中运行 SDK（非 `start_forever`） |
| 保存 `_main_loop` | 从 Stream 线程投递协程到主循环 |
| 连接状态机 | `DingTalkStreamState`: IDLE → CONNECTING → RUNNING → RECONNECTING → STOPPED |

---

## 二、协议与 API 细节

### Token 双体系

| Token 类型 | 获取接口 | 用途 | 过期时间 |
|-----------|---------|------|---------|
| 新版 (OAuth2) | `POST api.dingtalk.com/v1.0/oauth2/accessToken` | 发消息、下载文件、互动卡片 | 7200s (2h) |
| 旧版 (gettoken) | `GET oapi.dingtalk.com/gettoken` | media/upload | expires_in (7200s) |

两个 token 各自独立刷新，都留 60s 安全余量。

### 消息回调数据结构 (Stream)

```json
{
  "msgtype": "text|picture|richText|audio|video|file",
  "msgId": "msgXXX",
  "conversationId": "cidXXX",
  "conversationType": "1|2",
  "senderId": "$:LWCP_v1:$xxx (加密，不可直接使用)",
  "senderStaffId": "企业员工userId (用于单聊回复)",
  "senderNick": "发送者昵称",
  "isInAtList": true,
  "atUsers": [{"dingtalkId": "xxx"}],
  "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=xxx",
  "sessionWebhookExpiredTime": 1700000000000,
  "text": {"content": "消息文本"},
  "content": {"downloadCode": "xxx", "duration": 3000, "fileName": "xxx"}
}
```

### 发送消息 msgKey/msgParam 映射

| msgKey | msgParam 结构 | 说明 |
|--------|--------------|------|
| `sampleText` | `{"content": "..."}` | 纯文本 |
| `sampleMarkdown` | `{"title": "...", "text": "..."}` | Markdown（title ≤ 20字） |
| `sampleImageMsg` | `{"photoURL": "..."}` | 图片（URL 或 @mediaId） |
| `sampleFile` | `{"mediaId": "@...", "fileName": "...", "fileType": "..."}` | 文件 |
| `sampleAudio` | `{"mediaId": "@...", "duration": "3000"}` | 语音（duration 单位 ms） |
| `sampleVideo` | `{"mediaId": "@...", "duration": "3000", "videoType": "mp4"}` | 视频（duration 单位 ms） |
| `sampleActionCard` | `{"title", "text", "singleTitle", "singleURL"}` | 交互卡片 |

### SessionWebhook

- **过期时间**: 约 1 小时（`sessionWebhookExpiredTime` 字段，毫秒时间戳）
- **支持类型**: 仅 text / markdown / actionCard / feedCard
- **不支持**: image / file / voice 原生类型

---

## 三、与 Gateway 的交互

### 消息接收

```
钉钉平台 → WebSocket (dingtalk-stream SDK)
  → DingTalkStreamClient (后台线程)
    → _ChatbotHandler.process(callback)
      → _handle_stream_message(callback)
        → _parse_message_content(msg_type, raw_data)
        → UnifiedMessage.create(metadata={session_webhook, conversation_type, is_group})
        → run_coroutine_threadsafe(_emit_message(unified), _main_loop)
          → gateway._on_message(unified)
```

### 消息发送

```
Agent 生成回复
  → gateway._deliver_response()
    → OutgoingMessage.text(chat_id, text, metadata=original.metadata)
      (metadata 包含 session_webhook、is_group 等)
    → adapter.send_message(outgoing)
      → 路由: session_webhook 优先 → OpenAPI 回退
```

### metadata 传递链

接收消息时写入 `UnifiedMessage.metadata`:
- `session_webhook`: sessionWebhook URL（用于优先回复）
- `conversation_type`: "1" (单聊) / "2" (群聊)
- `is_group`: bool

Gateway 回复时 `outgoing_meta = dict(original.metadata)` 完整复制，
`send_message()` 中从 `message.metadata.get("session_webhook")` 取回。

---

## 四、与 AstrBot 参考实现对比

### 相同点

| 维度 | 说明 |
|------|------|
| SDK | 均使用 `dingtalk-stream` 官方 SDK |
| 接收方式 | 均为 `ChatbotHandler` + Stream 模式 |
| 消息类型 | 均支持 text/picture/richText/audio/file |
| 发送 API | 均使用 `/v1.0/robot/groupMessages/send` 和 `/v1.0/robot/oToMessages/batchSend` |
| 文件下载 | 均使用 `/v1.0/robot/messageFiles/download` |
| 媒体上传 | 均使用旧版 `oapi.dingtalk.com/media/upload` |

### 差异点

| 维度 | OpenAkita | AstrBot | 影响 |
|------|-----------|---------|------|
| **线程模型** | `start_forever()` 在新线程事件循环中运行 | `client.start()` 通过 `run_in_executor` 运行 | 等价，但 AstrBot 使用 `shutdown_event` 优雅退出 |
| **Token 获取** | 双 token（新旧 API 各一个） | 优先 SDK 内置 `get_access_token`，失败回退手动获取 | OpenAkita 更稳妥 |
| **ID 前缀处理** | 未处理 `$:LWCP_v1:$` 前缀 | `_id_to_sid()` 去除前缀 | **OpenAkita 缺失** |
| **私聊 userId** | `senderStaffId \|\| senderId`，缓存在 `_conversation_users` | `sender_staff_id` 持久化到 KV 存储 | OpenAkita 重启后丢失映射 |
| **content 来源** | 直接从 `raw_data["content"]` | 通过 `message.extensions["content"]` | **可能导致 audio/file 解析失败** |
| **视频发送** | 未实现 `sampleVideo` 发送 | 完整实现（含封面提取、格式转换） | **OpenAkita 缺失** |
| **语音格式** | 直接发送，无格式转换 | OGG(Opus) 优先，AMR 回退 | OpenAkita 可能发送不兼容格式 |
| **Webhook 回复** | 有完整 Webhook 优先回复 | 无 Webhook 回复，全走 OpenAPI | OpenAkita 更快 |
| **流式回复** | 未实现 | 缓冲后一次性发送 | 低优先级 |
| **At 处理** | 未从 text.content 中去除 @文本 | 在群聊消息处理时追加 At 组件 | 群聊回复中可能包含多余 @文本 |
| **robotCode** | 一律用 `app_key` | 一律用 `client_id` | 一致（通常相同），但部分场景可能不同 |

---

## 五、关键逻辑约束（修改时必须保持）

### 约束 1：线程投递模式

- Stream 回调在 **独立线程** 的事件循环中运行
- 收到消息后 **必须** 使用 `run_coroutine_threadsafe()` 投递到主循环
- **不能** 在 Stream 线程中直接 `await self._emit_message()`
- **不能** 使用 `asyncio.run()`（当前线程已有运行中的事件循环）

### 约束 2：Token 使用必须对应 API 域名

- `api.dingtalk.com/v1.0` 接口 → 新版 token（`x-acs-dingtalk-access-token` header）
- `oapi.dingtalk.com` 接口 → 旧版 token（`access_token` query param）
- **不可混用**：错误的 token 会返回 "无效的 access_token"

### 约束 3：SessionWebhook 有时效性

- 有效期约 1 小时（`sessionWebhookExpiredTime`）
- 过期后必须回退到 OpenAPI 发送
- 当前实现未检查过期时间，依赖发送失败后的降级逻辑

### 约束 4：robotCode 的身份语义

- `robotCode` 必须是钉钉后台的 **机器人编码**
- 当前实现使用 `config.app_key` 作为 `robotCode`
- **通常** app_key == robotCode，但 **不保证**（见健康检查文档记录）
- 若不一致，所有 OpenAPI 发送和文件下载都会返回 "robot 不存在"

### 约束 5：单聊 userId 必须是 senderStaffId

- `oToMessages/batchSend` 的 `userIds` 必须使用 **企业员工 userId**（即 `senderStaffId`）
- `senderId` 是加密 ID（`$:LWCP_v1:$...` 格式），**不能** 用于 API 调用
- 当前实现：`senderStaffId || senderId`，若 `senderStaffId` 为空则 fallback 到加密 ID，此时 **单聊回复必定失败**

### 约束 6：stop() 必须阻断 SDK 重连

- SDK `start()` 内部是 `while True` 循环，catch 所有异常（含 CancelledError）并重试
- 仅关闭 WebSocket 会触发 SDK 立即重连（`open_connection` → 新 ticket → 新连接）
- 必须 monkey-patch `client.open_connection = lambda: None` 阻断重连
- 同时将 `_main_loop = None` 防止僵尸线程投递到已关闭的事件循环
- `_handle_stream_message` 检查 `_running` + `_main_loop.is_closed()` 双重防护

### 约束 7：Webhook 仅支持 text/markdown

- SessionWebhook 不支持 image / file / voice 原生类型
- 图片需通过 **markdown 嵌入**：`![img](@media_id)` 或 `![img](url)`
- 文件/语音只能降级为文本提示

---

## 六、已发现问题与修复建议

### 问题 1（高）：senderId 前缀 `$:LWCP_v1:$` 未处理 — ✅ 已修复

**修复**: 新增 `_normalize_dingtalk_id()` 静态方法，对 `$:LWCP` 前缀的加密 ID 返回空串；
`_handle_stream_message` 优先 `senderStaffId`，次选 `_normalize_dingtalk_id(senderId)`，仅有加密 ID 时记录 warning；
`UnifiedMessage.user_id` 在没有可用 senderId 时回退为 `dd_anon_<conversation>` 而非污染缓存。


**现象**: `senderId` 可能以 `$:LWCP_v1:$` 开头，这是钉钉加密格式。直接存入 `_conversation_users` 后，用于 `oToMessages/batchSend` 的 `userIds` 时会导致 "staffId.notExisted"。

**当前代码** (`_handle_stream_message` L261):
```python
sender_id = raw_data.get("senderStaffId") or raw_data.get("senderId", "")
```
优先用 `senderStaffId` 是正确的，但 fallback 到 `senderId` 时该 ID 不可用于 API 调用。

**修复建议**:
1. 增加 `_normalize_dingtalk_id()` 方法，去除 `$:LWCP_v1:$` 前缀
2. 在写入 `_conversation_users` 和构建 `user_id` 时统一调用
3. 若 `senderStaffId` 为空，记录 warning 而非静默使用加密 ID

**引入风险**: 低。仅影响 ID 清理，不改变主流程。

### 问题 2（高）：audio/file 的 content 解析来源可能不一致 — ✅ 已修复

**修复**: 新增 `_extract_content_dict()` 静态方法，统一处理 `raw_data["content"]`（dict 或 JSON 字符串）
并在为空时回退到 `raw_data["extensions"]["content"]`；audio / video / file 三个分支全部改用该方法，逻辑去重。


**现象**: 钉钉 SDK 的 `ChatbotMessage` 可能将 audio/file 的 content 放在 `extensions["content"]` 而非顶层 `raw_data["content"]`。

**当前代码** (`_parse_message_content` L416):
```python
audio_content = raw_data.get("content", {})
```

**AstrBot 做法** (`convert_msg` L182):
```python
raw_content = cast(dict, message.extensions.get("content") or {})
```

**修复建议**:
```python
audio_content = raw_data.get("content", {})
if not audio_content or not isinstance(audio_content, dict):
    audio_content = raw_data.get("extensions", {}).get("content", {})
```
对 audio / video / file 三种类型都增加此 fallback。

**引入风险**: 低。增加 fallback 不影响已有解析路径。

### 问题 3（中）：SessionWebhook 缓存无过期清理 — ✅ 已修复

**修复**: `_session_webhooks` 由 `dict[str, str]` 改为 `OrderedDict[str, tuple[str, int]]`，
保存 `sessionWebhookExpiredTime`（缺省按 2h 估算）；新增 `_save_webhook()` / `_get_valid_webhook()` 辅助方法，
读取时惰性删除过期项并 LRU 触底淘汰，最大容量 500。三处使用点（写入 + 两处读取）已统一。


**现象**: `_session_webhooks` 会持续增长，且过期的 webhook（1 小时有效期）永远不会被清理。使用过期 webhook 发送会失败，虽有 OpenAPI 降级，但浪费一次请求。

**当前代码**: 无过期机制，无容量限制。

**修复建议**:
1. 存储 webhook 时同时记录 `sessionWebhookExpiredTime`
2. 发送前检查是否过期，过期则跳过直接走 OpenAPI
3. 或使用简单的 TTL dict / OrderedDict 限制容量（如最多 500 条）

**引入风险**: 低。仅影响缓存管理。

### 问题 4（中）：视频消息发送未实现 — ✅ 已修复

**已实现**: `_build_msg_key_param()` 已支持 `sampleVideo` 分支，上传视频获取 mediaId 后发送原生视频消息。当前为基础版（仅 mp4，无封面提取）。

### 问题 5（中）：语音发送无格式转换

**现象**: 钉钉 sampleAudio 要求 OGG 或 AMR 格式，当前直接上传原始文件。若输入为 wav / mp3 等格式，钉钉端可能无法播放。

**AstrBot 做法**: `_prepare_voice_for_dingtalk()` 优先转 OGG(Opus)，失败回退 AMR。

**修复建议**: 在 `_build_msg_key_param()` 的语音分支中，发送前检查格式并转换。

**引入风险**: 中。需要 ffmpeg 或类似依赖。可先增加格式检查和 warning 日志。

### 问题 6（中）：_conversation_users 缓存重启丢失

**现象**: `_conversation_users` 仅存在于内存。适配器重启后，所有会话映射丢失，无法回复之前的单聊会话。

**AstrBot 做法**: 将 `sender_staff_id` 持久化到 KV 存储 (`sp.put_async`)。

**修复建议**: 可选持久化方案，或在回复时动态获取（但钉钉 API 无此能力）。当前对"回复消息"场景影响不大（metadata 中有 session_webhook），对"主动推送"场景有影响。

**引入风险**: 低。增加持久化逻辑不影响现有功能。

### 问题 7（低）：robotCode 不可配置

**现象**: 硬编码使用 `config.app_key` 作为 robotCode。若实际 robotCode 与 app_key 不同（少数情况），所有发送和下载都会失败。

**修复建议**: 新增 `DINGTALK_ROBOT_CODE` 可选配置项，默认回退到 `app_key`。

**引入风险**: 极低。

### 问题 8（低）：群聊文本可能包含 @机器人 前缀文本

**现象**: 钉钉 Stream 模式下，群聊 `text.content` 可能包含 `@机器人名` 前缀（取决于 SDK 版本和钉钉端行为）。当前仅 `strip()` 处理，未去除 @ 前缀。

**修复建议**: 从 `raw_data.get("atUsers")` 提取机器人昵称/ID，从 text 中移除对应的 `@xxx ` 前缀。

**引入风险**: 低。需注意不要误删用户正常文本中的 @。

### 问题 9（低）：Markdown 检测逻辑过于宽松

**现象** (`_send_via_webhook` L694):
```python
any(c in text for c in ["**", "##", "- ", "```", "[", "]"])
```
方括号 `[` `]` 在普通文本中也常见（如 `[语音消息]`），导致本应作为纯文本的消息被误判为 markdown。

**修复建议**: 移除 `[` 和 `]` 的检测，或使用更严格的模式（如 `[xxx](url)` 链接模式）。

**引入风险**: 极低。

### 问题 10（低）：download_media 文件名可能冲突

**现象** (`download_media` L1108):
```python
local_path = self.media_dir / media.filename
```
若两条消息的 media.filename 相同（如 `dingtalk_image_abcdefgh.jpg`），后者会覆盖前者。

**修复建议**: 文件名中加入 msg_id 或 uuid 前缀。

**引入风险**: 极低。

### 问题 11（低）：send_message 中媒体 webhook 发送失败后的降级路径不完整

**现象** (`send_message` L567):
```python
fallback_text = message.content.text or "[媒体消息]"
fallback = OutgoingMessage.text(message.chat_id, fallback_text)
if session_webhook:
    return await self._send_via_webhook(fallback, session_webhook)
```
降级后仍尝试同一个可能已失败的 webhook，若 webhook 本身有问题（如过期），会再次失败并抛异常，此时无 OpenAPI 兜底。

**修复建议**: 降级路径应直接走 OpenAPI 而非重试 webhook。

**引入风险**: 极低。

### 问题 12（中）：缺少"正在处理"提示（send_typing 未实现）— ✅ 已修复

**现象**: ~~当前钉钉适配器未实现 `send_typing()` / `clear_typing()`（基类默认 no-op）。用户发送消息后在 Agent 处理期间（可能数秒至数十秒）完全无反馈，体验差。~~

**已实现方案 A（互动卡片）**，详见下方"十二、互动卡片 Typing 提示架构"。

**Gateway 侧已有机制**:
- `_on_message()` 在正常消息处理前创建 `typing_task = asyncio.create_task(self._keep_typing(message))`
- `_keep_typing()` 每 4 秒调用一次 `adapter.send_typing(chat_id)`
- 处理完成后 `typing_task.cancel()` + `adapter.clear_typing(chat_id)`
- 当前钉钉适配器的 `send_typing` 继承基类 no-op，所以完全无效果

**各平台现有实现对比**:

| 平台 | 方式 | send_typing | clear_typing |
|------|------|-------------|-------------|
| Telegram | 原生 `sendChatAction(TYPING)` | 每 4s 发一次（平台状态自动消失） | no-op |
| 飞书 | 发送"思考中..."互动卡片 | 幂等：首次发卡片，后续跳过 | PATCH 卡片为最终回复内容 / 删除卡片 |
| QQ 官方 | 发送"正在思考中..."文本 | 幂等：首次发文本，后续跳过 | 撤回该文本消息 |
| OneBot | NapCat 扩展 `set_input_status` | 每次调用发送一次 | no-op |
| 企微 WS | 无（流式回复本身即反馈） | no-op | no-op |
| 钉钉 | **未实现** | no-op | no-op |

**钉钉平台可用方案（3 选 1）**:

#### 方案 A：互动卡片（推荐，与飞书一致）

- **原理**: 发送 `StandardCard` 互动卡片，显示"思考中..."；处理完成后更新卡片为实际回复内容。
- **API**:
  - 发送: `POST /v1.0/im/v1.0/robot/interactiveCards/send`（需 `cardTemplateId: "StandardCard"`，`cardBizId: uuid`）
  - 更新: `PUT /v1.0/im/v1.0/interactiveCards`（通过 `cardBizId` 更新内容）
- **优点**: 无残留消息，回复原地替换，体验最好；可扩展为打字机流式模式
- **缺点**: 实现复杂度高；需要额外 API 权限（`im` 相关）；群聊用 `openConversationId`，单聊用 `singleChatReceiver`（`{"userId": staffId}`）
- **状态管理**:
  - `_thinking_cards: dict[str, str]`（`chat_id → cardBizId`）
  - `send_typing`: 幂等，`chat_id in _thinking_cards` 则跳过
  - `send_message`: 检查 `_thinking_cards.pop(chat_id)`，有则更新卡片内容
  - `clear_typing`: 若 cardBizId 仍在（异常路径），更新为空或忽略

#### 方案 B：Webhook 文本提示（简单，但有残留）

- **原理**: 通过 SessionWebhook 发送一条"💭 正在思考中..."的 markdown 消息。
- **优点**: 实现简单，复用现有 webhook 逻辑
- **缺点**: **无法撤回**（钉钉机器人无消息撤回 API），处理完成后该提示消息永远留在聊天中
- **状态管理**:
  - `_typing_sent: set[str]`（`chat_id` 集合）
  - `send_typing`: 幂等，已在集合中则跳过
  - `clear_typing`: 从集合中移除（但消息无法撤回）

#### 方案 C：不实现（维持现状）

- **理由**: 钉钉无原生 typing API，任何文本提示都无法撤回。如果 Agent 响应速度较快（< 5s），用户感知不明显。
- **适用场景**: 对体验要求不高，或 Agent 响应时间短的部署场景

**推荐实现**: **方案 A**（互动卡片），与飞书适配器的 thinking card 模式保持一致。

**关键实现约束**:

1. **幂等性**: Gateway 的 `_keep_typing` 每 4 秒调用一次，`send_typing` 必须保证同一 `chat_id` 只发一次卡片
2. **send_message 接管**: 在 `send_message()` 开头检查 `_thinking_cards`，有则更新卡片内容替代新发消息
3. **群聊 vs 单聊**: 群聊用 `openConversationId`，单聊用 `singleChatReceiver: {"userId": staffId}`
4. **失败降级**: 卡片发送/更新失败时静默降级（不影响正常消息流程）
5. **异常路径清理**: `clear_typing` 要处理卡片未被 `send_message` 消费的情况

**引入风险**: 中。需要新增 API 调用（interactiveCards），但失败不影响核心消息收发。需测试权限是否开通。

---

## 七、配置说明

### .env 配置

```ini
# 钉钉 Stream 模式
DINGTALK_ENABLED=true
DINGTALK_CLIENT_ID=your_client_id      # 即 AppKey
DINGTALK_CLIENT_SECRET=your_client_secret  # 即 AppSecret
```

### im_bots JSON 配置（多 Bot 模式）

```json
{
  "type": "dingtalk",
  "app_key": "your_client_id",
  "app_secret": "your_client_secret"
}
```

### 注意事项

- `client_id` 即钉钉后台的 **AppKey**（新称 Client ID）
- `client_secret` 即钉钉后台的 **AppSecret**（新称 Client Secret）
- Stream 模式 **不需要公网 IP**
- 需要在钉钉后台开通相关权限：
  - 企业内部机器人发送群聊消息
  - 企业内部机器人发送单聊消息
  - 机器人接收消息（Stream 模式）
- robotCode 通常等于 AppKey，若不同需联系钉钉管理员确认

---

## 八、数据流概览

### 消息接收流程

```
钉钉平台 WebSocket 推送
  → dingtalk_stream SDK (后台线程)
    → _ChatbotHandler.process(callback)
      → _handle_stream_message(callback)
        → raw_data = callback.data
        → 解析基础字段: msgtype, senderStaffId, conversationId, conversationType
        → 缓存: _session_webhooks[conversationId] = sessionWebhook
        → 缓存: _conversation_users[conversationId] = senderId
        → 缓存: _conversation_types[conversationId] = conversationType
        → _parse_message_content(msg_type, raw_data)
        → @检测: isInAtList / atUsers
        → UnifiedMessage.create(channel="dingtalk", metadata={...})
        → run_coroutine_threadsafe(_emit_message(unified), _main_loop)
          → gateway._on_message(unified)
```

### 消息发送流程 (回复)

```
Agent 生成回复
  → gateway._deliver_response()
    → OutgoingMessage(metadata=original.metadata)
    → adapter.send_message(outgoing)
      → 检查 _thinking_cards[chat_id]?
        → 有 (纯文本): 更新互动卡片为回复内容, return
        → 有 (媒体): 更新卡片为"处理完成", 继续正常流程
        → 无 / 更新失败: 继续正常流程
      → 从 metadata 或缓存获取 session_webhook
      → 有媒体?
        → 是: 上传 → markdown 嵌入 → webhook 发送
        → 否: webhook 发送 text/markdown
      → webhook 不可用/失败?
        → 是: OpenAPI (群聊 groupMessages/send, 单聊 oToMessages/batchSend)
        → _build_msg_key_param() 选择 msgKey/msgParam
```

### 连接生命周期

```
start()
  → _import_httpx(), _import_dingtalk_stream()
  → httpx.AsyncClient()
  → _refresh_token() (新版 OAuth2)
  → _main_loop = asyncio.get_running_loop()
  → _start_stream()
    → state: IDLE → CONNECTING
    → 后台 Thread: _run_stream_in_thread()
      → loop = asyncio.new_event_loop()
      → DingTalkStreamClient(Credential)
      → register_callback_handler(ChatbotMessage.TOPIC)
      → state: CONNECTING → RUNNING
      → loop.run_until_complete(client.start())
        → SDK 内部: 建 WS → 注册回调 → 接收消息
    → 看门狗: _stream_watchdog_loop() 每 15s 检查
      → 线程存活 + 运行 300s: 重置重连计数
      → 线程退出: state → RECONNECTING, 指数退避重启

stop()
  → _running = False, _main_loop = None
  → state: → STOPPED
  → 取消看门狗
  → monkey-patch client.open_connection = lambda: None  ← 阻断 SDK while True 重连
  → 关闭 WebSocket (ws.close() via run_coroutine_threadsafe)
  → 停止事件循环 (stream_loop.stop)
  → 等待线程退出 (join 5s)
  → http_client.aclose()
  → 输出 metrics (消息数, 重连数, 去重命中数)

注意: SDK start() 内部是 while True 循环，捕获所有异常含 CancelledError 并重连。
必须 patch open_connection 才能阻断重连，否则旧线程成为僵尸。
```

---

## 九、与其他 IM 适配器对比

| 特性 | 钉钉 (`dingtalk.py`) | 飞书 (`feishu.py`) | 企微WS (`wework_ws.py`) | Telegram |
|------|---------------------|-------------------|------------------------|----------|
| 连接方式 | Stream WebSocket (SDK) | WebSocket (SDK) | 原生 WebSocket | Webhook/Polling |
| 公网需求 | 否 | 否 (WS模式) | 否 | 视模式 |
| 认证方式 | AppKey + AppSecret | AppId + AppSecret | bot_id + secret | Bot Token |
| 线程模型 | 独立 Stream 线程 | 独立 WS 线程 | 主事件循环 | 主事件循环 |
| Token 数量 | 2（新旧 API） | 1 | 0 (协议内认证) | 1 |
| 消息加密 | 无 (WSS 传输层) | 无 (WSS 传输层) | 文件 AES-256-CBC | 无 |
| Typing 提示 | AI Card (382e4302) + StandardCard 降级 | 思考中卡片 (PATCH) | 无需（流式即反馈） | sendChatAction |
| 流式回复 | 未实现 | 未实现 | 原生支持 | 无 |
| Webhook 回退 | SessionWebhook → OpenAPI | 无 | response_url → WS | 无 |
| 心跳/重连 | SDK 内置 | SDK 内置 | 自实现 30s心跳 | SDK 管理 |

---

## 十、修改检查清单

修改钉钉适配器相关代码时，请逐一确认：

- [ ] 是否保持了新旧 Token 对应正确的 API 域名？
- [ ] 单聊回复的 userId 是否使用 senderStaffId（而非加密 senderId）？
- [ ] robotCode 是否使用 config.app_key？（若需变更，确认配置来源）
- [ ] 消息投递是否通过 `run_coroutine_threadsafe` 跨线程？
- [ ] 发送失败时是否有降级路径（webhook → OpenAPI → 文本）？
- [ ] media/upload 是否使用旧版 token？
- [ ] 新增消息类型是否在 `_parse_message_content` 中处理？
- [ ] 新增发送类型是否在 `_build_msg_key_param` 中处理？
- [ ] `stop()` 是否 patch 了 `open_connection` 并清除 `_main_loop`？
- [ ] 缓存字典是否有容量保护？
- [ ] `send_typing` 是否幂等（同一 chat_id 只发一次）？
- [ ] `send_message` 是否检查并消费 `_thinking_cards`？
- [ ] `clear_typing` 是否处理了异常路径的卡片清理？

---

## 十二、互动卡片 Typing 提示架构

### 实现概述

双层卡片策略：优先使用 **AI Card** (382e4302 官方 AI 模板)，失败时降级为 **StandardCard**。
状态通过 `_CardState(card_id, is_ai_card)` 统一管理。

### AI Card（主方案） — 官方 AI 流式模板

| 操作 | 方法 | URL | 说明 |
|------|------|-----|------|
| 创建实例 | POST | `/v1.0/card/instances` | `cardTemplateId="382e4302-551d-4880-bf29-a30acfab2e71.schema"` |
| 投递卡片 | POST | `/v1.0/card/instances/deliver` | `openSpaceId="dtv1.card//IM_GROUP.{cid}"` 或 `"dtv1.card//IM_ROBOT.{uid}"` |
| 流式更新 | PUT | `/v1.0/card/streaming` | `key="msgContent"`, `isFull=true`, 每次带新 `guid` |
| 完成/更新状态 | PUT | `/v1.0/card/instances` | `flowStatus: "FINISHED"` |

状态流转：`PROCESSING` → `INPUTING`（流式输出时） → `FINISHED`

AI Card 优势：
- 原生流式输出动画（INPUTING 状态自带光标效果）
- 官方 streaming API，无需手动节流
- 状态管理规范

### StandardCard（降级方案）

| 操作 | 方法 | URL | 说明 |
|------|------|-----|------|
| 发送卡片 | POST | `/v1.0/im/v1.0/robot/interactiveCards/send` | `cardTemplateId="StandardCard"` |
| 更新卡片 | PUT | `/v1.0/im/robots/interactiveCards` | 通过 `cardBizId` 全量替换 `cardData` |

当 AI Card API 不可用（权限未开通、模板不存在等）时自动降级为 StandardCard。
首次 AI Card 失败后 `_ai_card_available = False`，后续直接使用 StandardCard。

### cardData 格式 (StandardCard)

`cardData` 参数类型为 **JSON 字符串**（不是对象），结构：

```json
{
  "config": {"autoLayout": true, "enableForward": false},
  "header": {"title": {"type": "text", "text": ""}},
  "contents": [
    {"type": "markdown", "text": "💭 **正在思考中...**", "id": "content_main"}
  ]
}
```

### 群聊 vs 单聊路由

- **AI Card**: 群聊 `openSpaceId="dtv1.card//IM_GROUP.{cid}"`，单聊 `openSpaceId="dtv1.card//IM_ROBOT.{uid}"`
- **StandardCard**: 群聊 `openConversationId=chat_id`，单聊 `singleChatReceiver=json({"userId": staffId})`
- 两者**二选一**，通过 `_conversation_types` 缓存判断

### 状态管理

```
_thinking_cards: dict[str, _CardState]  # session_key -> _CardState(card_id, is_ai_card)
_ai_card_available: bool = True  # AI Card 可用性标记
```

### 关键时序

```
Gateway: _on_message()
  → typing_task = create_task(_keep_typing)
    → send_typing(chat_id)
      → sk not in _thinking_cards → _create_card(chat_id)
        → AI Card: create_instance + deliver → _CardState(ai=True)
        → AI Card 失败: fallback → _create_standard_card → _CardState(ai=False)
      → sk in _thinking_cards → 跳过 (幂等)
  → Agent 处理...
  → thinking_end → emit_progress_event("💭 preview")
    → gateway._try_patch_progress_to_card → adapter._patch_card_content(card_state, text)
      → AI Card: PUT streaming / StandardCard: PUT update → 思考内容写入卡片
  → stream_token(chat_id, token)  [流式模式]
    → AI Card: PUT /v1.0/card/streaming (无需节流)
    → StandardCard: PUT update (800ms 节流)
  → finalize_stream(chat_id, final_text)
    → AI Card: PUT /v1.0/card/instances (FINISHED)
    → StandardCard: PUT update (最终内容)
  → _send_response() → send_message(outgoing)
    → pop _thinking_cards[sk]
    → 纯文本: _finish_card(card_state, text), return
    → 含媒体: _finish_card(card_state, "处理完成"), 继续正常发送
    → 更新失败: fallthrough 到 webhook/OpenAPI
  → finally:
    → typing_task.cancel()
    → clear_typing(chat_id) → no-op (正常) / _finish_card (异常)
```

### 异常路径保障

| 场景 | 行为 |
|------|------|
| Agent 抛异常 | `_send_error` → `send_text` → `send_message` 消费卡片 |
| Agent + _send_error 双重失败 | `clear_typing` → `_finish_card` 更新卡片为"处理完成" |
| AI Card 创建失败 | 降级 StandardCard；StandardCard 也失败则跳过 |
| AI Card 首次失败 | `_ai_card_available = False`，后续直接 StandardCard |
| 卡片更新失败 | `send_message` fallthrough 到正常发送 |
| 中断后 typing 重建 | 主响应消费旧卡, typing 重建新卡, 中断响应消费新卡 |
| 单聊无 staffId | `send_typing` 静默跳过, 退化为无提示 |

### 约束与限制

1. **AI Card 权限**: 需要 AI Card 模板权限，未开通时自动降级
2. **API 调用量**: AI Card 消耗 3 次 (create + deliver + finish)，StandardCard 消耗 2 次
3. **普通版不支持 Stream 回调**: 按钮交互需改用高级版
4. **`outTrackId`/`cardBizId` 唯一性**: 每次生成新 UUID, 确保幂等
5. **加密 senderId 检测**: `$:LWCP` 前缀的 ID 不可用于卡片投递

---

## 十三、协议参考

- Stream 模式概述: https://opensource.dingtalk.com/developerpedia/docs/explore/tutorials/stream/overview
- 机器人接收消息: https://open-dingtalk.github.io/developerpedia/docs/learn/bot/appbot/receive/
- 机器人发送消息类型: https://open.dingtalk.com/document/development/robot-message-type
- 批量发送单聊消息: https://open.dingtalk.com/document/development/chatbots-send-one-on-one-chat-messages-in-batches
- 群聊消息发送: https://open.dingtalk.com/document/group/the-robot-sends-a-group-message
- 下载文件: https://open.dingtalk.com/document/development/download-the-file-content-of-the-robot-receiving-message
- dingtalk-stream SDK: https://pypi.org/project/dingtalk-stream/
- 互动卡片发送: https://open.dingtalk.com/document/group/robots-send-interactive-cards
- 互动卡片更新: https://dingtalk.apifox.cn/doc-3595435
- 互动卡片搭建平台: https://card.dingtalk.com/card-builder
- 打字机模式教程: https://opensource.dingtalk.com/developerpedia/docs/explore/tutorials/stream/bot/go/send-streaming-card
- AstrBot 钉钉适配器: https://github.com/Soulter/AstrBot (`astrbot/core/platform/sources/dingtalk/`)
- AI Card 流式卡片 API: https://open.dingtalk.com/document/orgapp/create-an-instance-of-an-interactive-card
- AI Card 流式更新 API: https://open.dingtalk.com/document/orgapp/streaming-update-of-interactive-cards
- openclaw-china 参考实现: DingTalk IM extension (TypeScript/Stream mode)

---

## 十四、openclaw-china 对比改进记录

> 基于与 openclaw-china-main 项目的对比分析，以下改进已实施。

### 已实施改进

| 改进项 | 描述 | 影响 |
|--------|------|------|
| **ACK 先行** | `process()` 立即返回 ACK，消息处理异步执行 | 避免大文件处理时 SDK 超时重发 |
| **AI Card 流式卡片** | 升级为 382e4302 官方 AI 模板，支持 `/v1.0/card/streaming` | 原生流式输出动画，体验大幅提升 |
| **StandardCard 降级** | AI Card 不可用时自动降级为 StandardCard | 向后兼容，零配置 |
| **去重增强** | key 加 bot_id 前缀，60s TTL 过期清理，上限 5000 | 多实例安全，内存可控 |
| **连接状态机** | `DingTalkStreamState` 枚举 + 状态转换日志 | 问题排查更直观 |
| **运行指标** | `_StreamMetrics`: 消息数、重连数、去重命中数等 | stop() 时输出汇总 |
| **stop() 改进** | monkey-patch `open_connection` 阻断 SDK 重连 + 清除 `_main_loop` | 彻底解决热重载后 Stream 僵尸线程问题 |
| **事件循环对齐** | `loop.run_until_complete(client.start())` 替代 `start_forever()` | `_stream_loop` 指向实际运行循环 |
| **消息投递防御** | `_handle_stream_message` 检查 `_running` + `_main_loop.is_closed()` | 避免 "Event loop is closed" 错误 |
| **思考进度卡片** | 新增 `_patch_card_content`，思考内容写入卡片而非独立消息 | 解决思考预览显示在最终回复之后的时序问题 |
| **视频消息发送** | `_build_msg_key_param` 新增 `sampleVideo` | 支持原生视频消息 |
| **文本分块** | `_chunk_markdown_text()` 4000 字符 markdown-aware 分块 | 避免超长文本发送失败 |

### 未实施（保留现状）

| 项目 | 原因 |
|------|------|
| 自行管理 WebSocket 重连 | SDK 内置重连足够，自行管理增加复杂度 |
| 连接/注册超时检测 | 当前看门狗机制已覆盖 |
| 指数退避重连 (自定义) | 看门狗已有指数退避 |
| 长任务文本降级通知 | 已有 thinking card，暂不需要 |
| SessionWebhook 废弃 | OpenAkita 的 Webhook 优先策略是合理的性能优化 |

---

## 十五、思考流式增强（与飞书对齐）

> 为钉钉 IM 互动卡片增加思考过程、工具调用链、Footer 耗时/状态展示，与飞书适配器对齐。

### 新增方法

| 方法 | 说明 |
|------|------|
| `stream_thinking(chat_id, thinking_text, *, thread_id, is_group, duration_ms)` | 实时将思考内容写入卡片，设置 `_typing_status = "深度思考"` |
| `stream_chain_text(chat_id, text, *, thread_id, is_group)` | 追加工具调用链描述，设置 `_typing_status = "调用工具"` |
| `_compose_thinking_display(sk)` | 根据 thinking + chain + reply 构建复合卡片内容 |
| `_build_footer_note(sk, *, final)` | 构建卡片底部状态文本（耗时 + 状态） |

### 新增状态字段

```python
self._streaming_thinking: dict[str, str] = {}       # 思考内容（替换式）
self._streaming_thinking_ms: dict[str, int] = {}    # 思考耗时（毫秒）
self._streaming_chain: dict[str, list[str]] = {}    # 工具调用链
self._typing_status: dict[str, str] = {}            # 当前状态标签
self._typing_start_time: dict[str, float] = {}      # send_typing 开始时间
```

### 卡片显示内容示例

```
💭 **思考过程** (3.2s)
> 用户想要了解天气情况，我需要调用天气 API...

搜索: 天气查询
分析: 数据解读

---
今天北京天气晴，最高温度 25°C ▍

⏱ 5.3s · 生成回复
```

完成时 footer 变为：`⏱ 完成 (8.1s)`

### chain_push 开关交互

Gateway 判断逻辑：`can_stream_thinking = chain_push AND hasattr(adapter, "stream_thinking")`

- **chain_push = OFF**: 思考不展示，与之前行为一致
- **chain_push = ON**: 思考内容通过 `stream_thinking` 实时写入卡片

不影响开关语义，ON 时体验改善（从独立文本消息变为卡片内联展示）。

### Bug 修复

- `_patch_card_content` 签名扩展为 `(self, card_state, text, sk=None, *, final=False)`，修复 gateway 调用时传 3 个位置参数的 TypeError
- `stream_token` 改用 `_compose_thinking_display`，使回复与思考/工具链共存于卡片

### 缓存清理

在 `finalize_stream`、`clear_typing`、`send_message` 中统一清理所有新增缓存字段，避免跨会话残留。
