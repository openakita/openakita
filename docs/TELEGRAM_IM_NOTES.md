# Telegram 适配器 — 功能清单 / 协议约束 / 已知限制

> 本文档记录 Telegram 适配器（`telegram.py`）的功能、协议细节和与其他模块的交互逻辑。
> 参考对比：koishi（`@satorijs/adapter-telegram`）、AstrBot（`tg_adapter.py` / `tg_event.py`）。
> 目的：后续修改或修 bug 时不遗漏既有逻辑约束。

---

## 一、核心功能清单

### 1. 消息接收

| 功能 | 关键代码位置 | 说明 |
|------|------------|------|
| Long Polling | `start()` → `updater.start_polling()` | 默认模式，`drop_pending_updates=True` |
| Webhook | `start()` → `set_webhook()` | 需公网 URL，**当前实现不完整**（见问题 3） |
| 消息处理 | `MessageHandler(filters.ALL, _handle_message)` | 处理所有非命令消息 |
| 命令处理 | `CommandHandler` | `/start`、`/unpair`、`/status` |
| 编辑消息 | `_handle_message()` | `update.edited_message` 分支，**但因 `allowed_updates` 限制实际收不到**（见问题 2） |
| 代理检测 | `_get_proxy()` | 优先配置 → `TELEGRAM_PROXY` → `ALL_PROXY` → `HTTPS_PROXY` → `HTTP_PROXY` |
| Polling Watchdog | `_polling_watchdog()` | 每 120s 检查 polling 状态，停止则自动重启 |

### 2. 消息发送

| 功能 | 方法 | 说明 |
|------|------|------|
| 文本消息 | `send_message()` → `_bot.send_message()` | 支持 Markdown/HTML/纯文本 |
| 图片 | `send_message()` → `_bot.send_photo()` | 支持 `local_path` 和 `url` |
| 文件 | `send_message()` → `_bot.send_document()` | 仅支持 `local_path` |
| 语音 | `send_message()` → `_bot.send_voice()` | 仅支持 `local_path` |
| 视频 | ❌ 未实现 | `content.videos` 被忽略（见问题 4） |
| Sticker | ❌ 未实现 | 接收有处理，发送无逻辑（见问题 7） |
| 独立图片发送 | `send_photo()` | 便捷方法，`im_channel.py` 调用 |
| 独立文件发送 | `send_file()` | 便捷方法 |
| 独立语音发送 | `send_voice()` | 便捷方法 |
| Typing 状态 | `send_typing()` | `ChatAction.TYPING` |

### 3. 文件处理

| 功能 | 方法 | 说明 |
|------|------|------|
| 文件下载 | `download_media()` | `_bot.get_file()` → `download_to_drive()`，**20MB 限制**（见问题 6） |
| 文件上传 | `upload_media()` | Telegram 不需要预上传，返回空 MediaFile |

### 4. 启动流程

| 步骤 | 说明 |
|------|------|
| `start()` | 延迟导入 `python-telegram-bot`，构建 Application |
| HTTPXRequest | 配置连接池（8/4）、超时（30/60s）、代理 |
| 注册 Handler | 命令处理器（/start, /unpair, /status）+ 全消息处理器 |
| Bot 命令菜单 | `set_my_commands()` 注册 12 个命令到 Telegram 菜单 |
| Polling/Webhook | 根据 `webhook_url` 选择模式；Polling 先 `delete_webhook` 清理残留 |
| Watchdog | 非 Webhook 模式下启动 `_polling_watchdog` 任务 |

---

## 二、协议与 API 细节

### Bot API 调用方式

| 维度 | 说明 |
|------|------|
| 库 | `python-telegram-bot>=21.0`（异步版本） |
| HTTP 客户端 | `HTTPXRequest`，底层使用 httpx |
| API 端点 | `https://api.telegram.org/bot{token}` |
| 文件端点 | `https://api.telegram.org/file/bot{token}` |
| 代理 | httpx 原生代理，支持 HTTP/SOCKS5 |
| 自定义 API 地址 | ❌ 不支持（AstrBot 支持 `telegram_api_base_url`） |

### 消息格式 (parse_mode)

| 模式 | 使用场景 | 说明 |
|------|---------|------|
| Markdown | 默认 | 旧版 Markdown，语法宽容 |
| HTML | 可选 | 通过 `OutgoingMessage.parse_mode="html"` |
| None | 降级 | Markdown 解析失败时回退纯文本 |

`_convert_to_telegram_markdown()` 做了以下转换：
- 标题 `#` → 移除符号保留文字
- 表格 `|...|` → 简单格式（表头加粗）
- 水平线 `---` → Unicode 分隔符 `─`

### 配对管理机制

| 功能 | 说明 |
|------|------|
| 管理器 | `TelegramPairingManager` |
| 存储 | `data/telegram/pairing/paired_users.json` + `pairing_code.txt` |
| 配对码 | 6 位数字，可配置或自动生成 |
| 超时 | 等待配对 5 分钟超时 |
| 命令 | `/start` 开始配对、`/unpair` 取消、`/status` 查看状态 |
| 可选 | `require_pairing=False` 可关闭配对验证 |

---

## 三、与 Gateway 的交互

### 消息接收流程

```
Telegram Bot API (getUpdates / Webhook)
  → python-telegram-bot (Application)
    → _handle_message(update, context)
      → 配对验证（require_pairing 时）
      → _convert_message(message)
        → 解析: text/photo/voice/audio/video/document/location/sticker
        → UnifiedMessage.create(channel="telegram", ...)
      → _log_message(unified)
      → _emit_message(unified)
        → gateway._on_message(unified)
```

### 消息发送流程

```
Agent 生成回复
  → gateway._deliver_response()
    → _split_text(response, max_length=4000)
    → for each chunk:
        → OutgoingMessage.text(chat_id, chunk, metadata=original.metadata)
        → adapter.send_message(outgoing)
          → _convert_to_telegram_markdown(text)
          → _bot.send_message(parse_mode=Markdown)
          → 失败? → 回退 send_message(parse_mode=None)
        → sleep(_SPLIT_SEND_INTERVAL["telegram"] = 0.5s)
```

### 分片逻辑

| 参数 | 值 | 说明 |
|------|---|------|
| `_CHANNEL_MAX_LENGTH["telegram"]` | 4000 | API 限制 4096，留余量 |
| `_SPLIT_SEND_INTERVAL["telegram"]` | 0.5s | 分片间延迟，避免限流 |
| `base_channel` 提取 | `channel.split("_")[0]` | 兼容 `telegram_bot2` 等多实例命名 |

### 媒体预处理 (`_preprocess_media`)

| 类型 | 处理 |
|------|------|
| `content.voices` | 下载 → ffmpeg 转 WAV → Whisper STT 转写 |
| `content.images` | 下载到本地 |
| `content.videos` | 下载到本地 |
| `content.files` | 下载到本地 |

**关键问题**：`message.audio`（音频文件）当前归入 `content.voices`，导致被强制 STT 转写（见问题 1）。

---

## 四、与参考项目对比

### 相同点

| 维度 | 说明 |
|------|------|
| 库 | 均使用 `python-telegram-bot` |
| 基础接收 | 均支持 text/photo/voice/video/document |
| Markdown 降级 | 均在解析失败时回退纯文本 |
| Long Polling | 均通过 `updater.start_polling()` |
| 配对/认证 | OpenAkita 有配对机制，AstrBot 无；koishi 依赖 Satori 协议层 |

### 差异点

| 维度 | OpenAkita | koishi | AstrBot | 影响 |
|------|-----------|--------|---------|------|
| **audio 归类** | `content.voices`（错误） | 独立 `audio` 类型 | `Comp.Record` | **OpenAkita 缺陷**：音频文件被当成语音指令做 STT |
| **media_group** | 不聚合，逐条处理 | 1.2s debounce 合并 | APScheduler debounce + max_wait | **OpenAkita 缺失**：相册每张图独立触发 Agent |
| **视频发送** | 未实现 | `sendVideo` | `send_video` | **OpenAkita 缺失** |
| **callback_query** | 未订阅 | 完整支持 → `interaction/button` | 未实现 | **OpenAkita 缺失** |
| **inline_query** | 未订阅 | 类型+API 完整，需插件监听 | 未实现 | 低优先级 |
| **channel_post** | 未订阅 | `channel_post` → `message` | 未实现 | 低优先级 |
| **animation/GIF** | 未处理 | `sendAnimation` | 未处理 | 中优先级 |
| **video_note** | 未处理 | 未明确 | 未处理 | 低优先级 |
| **Forum/Topic** | 未实现 | `message_thread_id` | `message_thread_id` + `session_id` | **OpenAkita 缺失** |
| **流式输出** | 未实现 | 无 | 私聊 `sendMessageDraft` / 群聊 `edit_message_text` | 中优先级 |
| **消息反应** | 未实现 | 无 | `set_message_reaction` | 低优先级 |
| **parse_mode** | Markdown（旧版） | HTML（统一） | MarkdownV2 + `telegramify-markdown` | OpenAkita 更宽容但功能受限 |
| **自定义 API 地址** | 不支持 | 支持 `endpoint` | 支持 `telegram_api_base_url` | **OpenAkita 缺失**：无法用反代绕 GFW |
| **Chat Action** | 仅 `TYPING` | 无 | `upload_photo` / `upload_video` 等 | 低优先级 |
| **语音隐私兼容** | 无处理 | 无 | `Voice_messages_forbidden` → `send_document` | 低优先级 |
| **Bot 命令注册** | 硬编码 12 个 | `setMyCommands` 动态同步 | 自动从 handler_registry 收集 | OpenAkita 不够灵活 |
| **sendMediaGroup** | 不支持 | 支持 | 未明确 | 中优先级 |
| **Webhook** | 设置但无 HTTP 服务 | 完整 HTTP 服务 | 不支持 | **OpenAkita 缺陷** |
| **消息去重** | 无 | 无 | 无 | 低优先级 |
| **getFile 20MB 限制** | 无处理 | 支持本地 Bot API 服务器 | 无处理 | 中优先级 |

---

## 五、消息类型支持矩阵

### 接收（Telegram → UnifiedMessage）

| Telegram 类型 | 处理 | 映射到 | 备注 |
|--------------|------|--------|------|
| `message.text` | ✅ | `content.text` | |
| `message.photo` | ✅ | `content.images` | 取最大尺寸 `photo[-1]` |
| `message.voice` | ✅ | `content.voices` | OGG 格式，触发 STT |
| `message.audio` | ⚠️ | `content.voices`（应为 `content.files`） | **BUG**：被当作语音做 STT |
| `message.video` | ✅ | `content.videos` | |
| `message.document` | ✅ | `content.files` | |
| `message.location` | ✅ | `content.location` | `{lat, lng}` |
| `message.sticker` | ✅ | `content.sticker` | `{id, emoji, set_name}` dict |
| `message.caption` | ✅ | `content.text` | 媒体附带文字 |
| `message.video_note` | ❌ | — | 圆形短视频未处理 |
| `message.animation` | ❌ | — | GIF/动画未处理 |
| `message.contact` | ❌ | — | |
| `message.poll` | ❌ | — | |
| `media_group_id` | ❌ | — | 相册未聚合 |
| `callback_query` | ❌ | — | 未订阅 |
| `inline_query` | ❌ | — | 未订阅 |
| `channel_post` | ❌ | — | 未订阅 |
| `edited_message` | ❌ | — | 代码有处理但 `allowed_updates` 未订阅 |

### 发送（OutgoingMessage → Telegram）

| 内容类型 | API 调用 | 支持 URL | 支持 local_path | 备注 |
|---------|---------|---------|----------------|------|
| 文本 | `send_message` | — | — | Markdown 降级纯文本 |
| 图片 | `send_photo` | ✅ | ✅ | |
| 文件 | `send_document` | ❌ | ✅ | 缺 URL 分支 |
| 语音 | `send_voice` | ❌ | ✅ | 缺 URL 分支 |
| 视频 | ❌ | — | — | 未实现 |
| Sticker | ❌ | — | — | 未实现 |
| 多图相册 | ❌ | — | — | 应使用 `sendMediaGroup` |

---

## 六、已发现问题与修复建议

### 问题 0（紧急）：Agent 响应中工具调用模拟文本泄漏到 IM 通道

**现象**: 用户在 Telegram 发送简单对话消息（如"什么情况"、"?"），Bot 回复中包含 `.run_shell(command="date")`、`.browser_navigate(url="...")`、`.browsergetcontent()` 等工具调用语法文本。LLM 生成的自然语言回复在日志中可见，但未发送到 Telegram；用户仅看到原始工具调用模拟文本。

**根因（双层缺陷叠加）**:

1. **正则缺陷** — `response_handler.py` 的 `strip_tool_simulation_text()` 中 `pattern1`：
   ```python
   # 改前：不匹配前导 "."，且 [^)]* 无法处理参数内含 ")" 的情况
   pattern1 = r"^[a-z_]+\s*\([^)]*\)\s*$"
   ```
   导致 `.run_shell(...)`、`.browser_navigate(...)` 等 LLM 模拟的工具调用语法无法被识别和移除。

2. **返回路径遗漏** — `reasoning_engine.py` 中 5 个返回路径仅调用 `strip_thinking_tags` + `parse_intent_tag`，完全跳过了包含 `strip_tool_simulation_text` 的 `clean_llm_response`：
   - `run()` 的 `end_turn` 路径和 Supervisor 终止路径
   - `reason_stream()` 的 `end_turn` 路径和 Supervisor 终止路径
   - `_handle_final_answer()` 的 `tools_executed_in_task` 分支

**已修复**:
- `response_handler.py`：`pattern1` 改为 `r"^\.?[a-z_]+\s*\(.*\)\s*$"`（支持前导点号 + 贪婪匹配处理嵌套括号）
- `reasoning_engine.py`：5 个返回路径全部统一使用 `clean_llm_response()`

---

### 问题 1（高）：音频文件被错误归类为语音消息

**现象**: 用户通过 Telegram 发送音频文件（音乐/播客/长录音），系统只识别到部分时长（如 24 秒），原始文件用途丢失。

**根因** (`_convert_message` L667):
```python
# 音频
if message.audio:
    audio = message.audio
    media = await self._create_media_from_file(...)
    media.duration = audio.duration
    content.voices.append(media)  # ← 错误：应为 content.files
```

Telegram 中 `message.voice` 是录音按钮产生的语音消息（应做 STT），`message.audio` 是音频文件附件（音乐/播客等，应作为文件处理）。当前代码将两者都归入 `content.voices`，导致：
1. 音频文件被 Gateway 强制 STT 转写（`_process_voice` → Whisper）
2. ffmpeg 转换有 30s `timeout`，长音频可能被截断
3. Whisper 转写有 120s `timeout`，长音频可能超时
4. 转写文本替代了用户输入，Agent 以为用户"说了一句话"而非"发了一个文件"

**修复建议**:
```python
if message.audio:
    audio = message.audio
    media = await self._create_media_from_file(
        audio.file_id,
        audio.file_name or f"audio_{audio.file_id}.mp3",
        audio.mime_type or "audio/mpeg",
        audio.file_size or 0,
    )
    media.duration = audio.duration
    content.files.append(media)  # 作为文件处理，不做 STT
```

**引入风险**: 低。仅改变归类路径，不影响 voice 的 STT 流程。若需对音频文件做 STT，可在 Gateway 层根据 MIME 类型判断。

### 问题 2（高）：`allowed_updates` 导致 edited_message 收不到

**现象**: `_handle_message` 中有 `update.edited_message` 分支（L557），但 polling 只订阅了 `["message"]`（L420），`edited_message` 类型的 update 永远不会到达。

**当前代码** (`start()` L418-420):
```python
await self._app.updater.start_polling(
    drop_pending_updates=True,
    allowed_updates=["message"],
)
```

**修复建议**:
```python
allowed_updates=["message", "edited_message"]
```
若未来需要 callback_query、channel_post 等，继续扩展此列表。

**引入风险**: 极低。仅增加接收的 update 类型。

### 问题 3（高）：Webhook 模式无法工作

**现象**: `start()` 在 webhook 模式下只调用 `self._app.start()` + `self._bot.set_webhook(url)`，但没有启动 HTTP 服务器接收 Telegram 的 webhook POST 请求。

**当前代码** (`start()` L403-407):
```python
if self.webhook_url:
    await self._app.start()
    await self._bot.set_webhook(self.webhook_url)
```

`python-telegram-bot` 需要调用 `updater.start_webhook()` 来启动内置 HTTP 服务器，或手动将收到的 POST 请求传给 `application.update_queue`。

**修复建议**: 使用 `start_webhook()`:
```python
if self.webhook_url:
    await self._app.start()
    await self._app.updater.start_webhook(
        listen="0.0.0.0",
        port=webhook_port,
        url_path=f"/telegram/{bot_token_hash}",
        webhook_url=self.webhook_url,
    )
```
或集成到项目已有的 FastAPI/aiohttp 服务中。

**引入风险**: 中。需要确定端口和路径配置方案。当前无人使用 webhook 模式，暂不紧急。

### 问题 4（高）：视频发送未实现

**现象**: `send_message()` 处理了 images/files/voices 但跳过了 `content.videos`。Agent 通过 `OutgoingMessage.with_video()` 发送视频时会被静默丢弃，返回空字符串。

**当前代码** (`send_message()` L855-946):
```python
# 发送图片
for img in message.content.images: ...
# 发送文档
for file in message.content.files: ...
# 发送语音
for voice in message.content.voices: ...
# ← 缺少: 发送视频
```

**修复建议**:
```python
# 发送视频
for video in message.content.videos:
    if video.local_path:
        with open(video.local_path, "rb") as f:
            sent_message = await self._bot.send_video(
                chat_id=chat_id,
                video=f,
                caption=message.content.text,
                parse_mode=parse_mode,
                reply_to_message_id=int(message.reply_to) if message.reply_to else None,
            )
    elif video.url:
        sent_message = await self._bot.send_video(
            chat_id=chat_id,
            video=video.url,
            caption=message.content.text,
            parse_mode=parse_mode,
            reply_to_message_id=int(message.reply_to) if message.reply_to else None,
        )
```

**引入风险**: 低。新增发送分支，不影响现有逻辑。

### 问题 5（高）：`deliver_artifacts` 不支持 video 类型

**现象**: `im_channel.py` 的 `_deliver_artifacts` 和 `_deliver_artifacts_cross_channel` 只处理 voice/image/file 三种类型，video 返回 `unsupported_type`。

**当前代码** (`im_channel.py` L601-633):
```python
elif art_type == "voice": ...
elif art_type == "image": ...
elif art_type == "file": ...
else:
    receipt["error"] = f"unsupported_type:{art_type}"
```

**修复建议**: 新增 video 分支，调用 `adapter.send_video()` 或通过 `OutgoingMessage.with_video()` 发送。若适配器不支持 `send_video`，降级为 `send_file`。

**引入风险**: 低。新增分支，不影响已有类型。

### 问题 6（高）：Telegram Bot API `getFile` 20MB 下载限制

**现象**: `download_media()` 直接调用 `self._bot.get_file()`，Telegram 标准 Bot API 服务器不支持下载超过 20MB 的文件。大文件下载会抛出异常，外层捕获后标记为处理失败，无友好提示。

**当前代码** (`download_media()` L948-970):
```python
file = await self._bot.get_file(media.file_id)
local_path = self.media_dir / media.filename
await file.download_to_drive(local_path)
```

**修复建议**:
1. 在下载前检查 `media.size`，若超过 20MB 记录 warning 并在消息中告知用户
2. 长期方案：支持配置本地 Bot API 服务器（`telegram_api_base_url`），解除 20MB 限制
3. 参考 koishi：支持 `files.server` 和自定义 `endpoint`

**引入风险**: 低。

### 问题 7（中）：Sticker 发送未实现

**现象**: 接收时 sticker 存为 `content.sticker` dict（含 `id/emoji/set_name`），但 `send_message()` 中无 sticker 发送逻辑。

**修复建议**: 在 `send_message()` 中增加 sticker 发送分支：
```python
if message.content.sticker:
    sticker_id = message.content.sticker.get("id")
    if sticker_id:
        sent_message = await self._bot.send_sticker(chat_id=chat_id, sticker=sticker_id)
```

**引入风险**: 极低。

### 问题 8（中）：media_group 未聚合

**现象**: Telegram 相册消息（多张图片共享同一 `media_group_id`）的每张图分别到达，各自触发独立的 Agent 回复。用户发 3 张图，会收到 3 次独立回复。

**AstrBot 做法**: APScheduler debounce，`media_group_timeout=2.5s`，`media_group_max_wait=10s`。

**koishi 做法**: 1.2s 延迟窗口合并同一 `media_group_id` 的消息。

**修复建议**: 在 `_handle_message` 中检测 `media_group_id`，使用 debounce 机制合并同组消息后再 `_emit_message`。

**引入风险**: 中。需要引入延迟和缓冲逻辑。

### 问题 9（中）：video_note / animation 接收未处理

**现象**: `_convert_message` 不解析 `message.video_note`（圆形短视频）和 `message.animation`（GIF 动画）。

**修复建议**:
```python
if message.video_note:
    vn = message.video_note
    media = await self._create_media_from_file(
        vn.file_id, f"videonote_{vn.file_id}.mp4",
        "video/mp4", vn.file_size or 0,
    )
    media.duration = vn.duration
    content.videos.append(media)

if message.animation:
    anim = message.animation
    media = await self._create_media_from_file(
        anim.file_id, anim.file_name or f"animation_{anim.file_id}.mp4",
        anim.mime_type or "video/mp4", anim.file_size or 0,
    )
    content.images.append(media)  # GIF 归类为图片
```

**引入风险**: 低。

### 问题 10（中）：文件/语音 URL 发送缺失

**现象**: `send_message()` 中 images 支持 `url` 和 `local_path` 两种来源，但 files 和 voices 只支持 `local_path`。当 MediaFile 只有 `url` 时，文件/语音发送会被跳过。

**修复建议**: 为 files 和 voices 增加 `url` 分支：
```python
for file in message.content.files:
    if file.local_path:
        # ... existing code
    elif file.url:
        sent_message = await self._bot.send_document(
            chat_id=chat_id, document=file.url, ...)
```

**引入风险**: 极低。

### 问题 11（中）：Caption 超长无保护

**现象**: Telegram caption 限制 1024 字符，但 `send_photo`/`send_document`/`send_voice` 使用 `message.content.text` 作 caption 时无截断保护。若文本超过 1024 字符，API 调用会失败。

**修复建议**: 发送带 caption 的媒体时，截断超长文本：
```python
caption = message.content.text
if caption and len(caption) > 1024:
    caption = caption[:1021] + "..."
```

**引入风险**: 极低。

### 问题 12（中）：多图片逐条发送而非 sendMediaGroup

**现象**: 当 `message.content.images` 有多张图片时，逐条调用 `send_photo`，每张重复 caption。应使用 `sendMediaGroup` 作为相册发送。

**修复建议**: 检测多图场景并使用 `send_media_group`：
```python
if len(message.content.images) > 1:
    media_group = [InputMediaPhoto(media=open(img.local_path, "rb")) for img in images]
    media_group[0].caption = message.content.text  # 仅首图附 caption
    await self._bot.send_media_group(chat_id=chat_id, media=media_group)
```

**引入风险**: 低。

### 问题 13（中）：Forum/Topic 不支持

**现象**: Telegram 超级群的 Forum（话题）功能使用 `message_thread_id` 标识话题。当前适配器未读取此字段，也未在发送时传递，导致话题群中的消息无法正确路由。

**修复建议**:
1. 接收时：将 `message.message_thread_id` 映射到 `UnifiedMessage.thread_id`
2. 发送时：在 `send_message` 中传递 `message_thread_id`

**引入风险**: 低。

### 问题 14（低）：`from_user` 可能为 None

**现象**: Channel post 和某些系统消息的 `message.from_user` 为 None。`_convert_message` 中 `message.from_user.id` 会触发 `AttributeError`。

**当前代码** (`_convert_message` L752-753):
```python
user_id=f"tg_{message.from_user.id}",
channel_user_id=str(message.from_user.id),
```

**修复建议**:
```python
from_user = message.from_user
user_id = f"tg_{from_user.id}" if from_user else "tg_unknown"
channel_user_id = str(from_user.id) if from_user else "unknown"
```

**引入风险**: 极低。

### 问题 15（低）：download_media 文件名碰撞

**现象**: `download_media()` 使用 `self.media_dir / media.filename` 保存文件。若两条消息的 `media.filename` 相同，后者会覆盖前者。

**修复建议**: 文件名中加入唯一前缀：
```python
import uuid
safe_name = f"{uuid.uuid4().hex[:8]}_{media.filename}"
local_path = self.media_dir / safe_name
```

**引入风险**: 极低。

### 问题 16（低）：Bot 命令菜单硬编码

**现象**: `start()` 中硬编码了 12 个 `BotCommand`（L380-394），新增或修改系统命令时必须手动同步。

**AstrBot 做法**: 从 `star_handlers_registry` 自动收集命令，使用哈希去重避免重复注册。

**修复建议**: 从 Gateway 或命令注册表动态收集命令列表，而非硬编码。

**引入风险**: 低。

---

## 七、配置说明

### .env 配置

```ini
# Telegram
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_WEBHOOK_URL=                          # 留空使用 Long Polling
TELEGRAM_PAIRING_CODE=                         # 留空自动生成 6 位数字
TELEGRAM_REQUIRE_PAIRING=true                  # 是否需要配对验证
TELEGRAM_PROXY=http://127.0.0.1:7890           # 可选代理
```

### im_bots JSON 配置（多 Bot 模式）

```json
{
  "type": "telegram",
  "bot_token": "123456:ABC-DEF...",
  "webhook_url": "",
  "pairing_code": "",
  "require_pairing": true,
  "proxy": ""
}
```

### 注意事项

- Bot Token 在 Telegram @BotFather 创建机器人后获取
- Long Polling 模式**不需要公网 IP**，适合开发和内网部署
- Webhook 模式需要公网可达的 HTTPS URL（**当前实现不完整**，见问题 3）
- 代理支持 HTTP 和 SOCKS5 协议
- 配对码文件默认路径：`data/telegram/pairing/pairing_code.txt`
- 多 Bot 模式通过 `im_bots` 配置项支持多个 Telegram Bot 实例

---

## 八、数据流概览

### 消息接收流程

```
Telegram 平台 (getUpdates Long Polling)
  → python-telegram-bot Application
    → MessageHandler(filters.ALL)
      → _handle_message(update, context)
        → message = update.message or update.edited_message
        → 配对检查: pairing_manager.is_paired(chat_id)?
          → 未配对: start_pairing / verify_code 流程
          → 已配对: 继续
        → _convert_message(message)
          → 解析 text/photo/voice/audio/video/document/location/sticker
          → 解析 chat_type: private/group/channel
          → 检测 @机器人 mention (entities)
          → UnifiedMessage.create(channel="telegram", ...)
        → _emit_message(unified)
          → gateway._on_message(unified)
```

### 消息发送流程

```
Agent 生成回复
  → gateway._deliver_response()
    → _split_text(response, 4000)
    → for chunk in chunks:
        → OutgoingMessage.text(chat_id, chunk)
        → adapter.send_message(outgoing)
          → 文本: _bot.send_message(parse_mode=Markdown)
            → BadRequest("Can't parse entities")? → 回退 parse_mode=None
          → 图片: _bot.send_photo(local_path / url)
          → 文件: _bot.send_document(local_path)
          → 语音: _bot.send_voice(local_path)
        → sleep(0.5s)
```

### 连接生命周期

```
start()
  → _import_telegram()
  → HTTPXRequest(proxy, timeouts)
  → Application.builder().token().request().build()
  → app.add_error_handler(_on_error)
  → app.add_handler(CommandHandler: /start, /unpair, /status)
  → app.add_handler(MessageHandler: filters.ALL)
  → app.initialize()
  → _bot.set_my_commands(12 commands)
  → Polling 模式:
      → _bot.delete_webhook(drop_pending_updates=True)
      → app.start()
      → updater.start_polling(allowed_updates=["message"])
      → _polling_watchdog() [asyncio.Task, 120s 周期检查]
  → Webhook 模式:
      → app.start()
      → _bot.set_webhook(webhook_url)

stop()
  → _running = False
  → cancel _watchdog_task
  → updater.stop()
  → app.stop()
  → app.shutdown()
```

---

## 九、与其他 IM 适配器对比

| 特性 | Telegram | 飞书 | 钉钉 | 企微 WS | 企微 Bot | OneBot | QQ 官方 |
|------|----------|------|------|---------|---------|--------|--------|
| 连接方式 | Polling/Webhook | WS (SDK) | Stream (SDK) | 原生 WS | HTTP 回调 | WS 正/反向 | WS (SDK)/Webhook |
| 公网需求 | Polling 否 | WS 否 | 否 | 否 | 是 | 视模式 | 视模式 |
| 认证方式 | Bot Token | AppId + AppSecret | AppKey + AppSecret | bot_id + secret | corp_id + token + aeskey | 无(协议层) | AppId + Secret |
| 线程模型 | 主事件循环 | 独立 WS 线程 | 独立 Stream 线程 | 主事件循环 | 主事件循环 | 主事件循环 | 视模式 |
| Token 刷新 | 无需（Bot Token 永久） | SDK 管理 | 双 Token 手动刷新 | 无 Token | OAuth2 + 手动 | 无 | OAuth2 手动 |
| 消息加密 | 无 (HTTPS) | 无 (WSS) | 无 (WSS) | 文件 AES-CBC | 全局 AES-CBC | 无 | Ed25519/HMAC |
| 流式回复 | 未实现 | 未实现 | 未实现 | 原生 WS stream | HTTP stream | 无 | 无 |
| Typing 状态 | `ChatAction.TYPING` | 卡片"思考中..." | 无 | 流式帧 | stream(finish=false) | NapCat 扩展 | 文本+撤回 |
| 心跳/重连 | SDK 管理 + watchdog | SDK 管理 | SDK 管理 | 自实现 30s 心跳 | 不需要 | 指数退避 | SDK 管理 |
| 配对机制 | ✅ 配对码验证 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 十、关键逻辑约束（修改时必须保持）

### 约束 1：配对验证在消息处理之前

- `_handle_message` 中配对检查在 `_convert_message` 之前
- 未配对用户的消息**不会**到达 Gateway
- `/start`、`/unpair`、`/status` 由 `CommandHandler` 优先处理，不经过 `_handle_message` 的配对检查

### 约束 2：Markdown 降级策略

- 默认使用旧版 Markdown（非 MarkdownV2），兼容性更好
- `_convert_to_telegram_markdown()` 将标准 Markdown 转换为 Telegram 兼容格式
- `BadRequest("Can't parse entities")` 时自动回退纯文本
- **不能**在降级时使用 `message.content.text` 以外的文本（否则丢失原始内容）

### 约束 3：Polling 重启不丢消息

- 初次启动：`drop_pending_updates=True`（跳过离线消息）
- Watchdog 重启：`drop_pending_updates=False`（保留重启期间的消息）
- **不能**在 watchdog 重启时也设为 True（会丢消息）

### 约束 4：代理设置透传

- `HTTPXRequest` 的 `proxy` 参数需要同时设置给 `request` 和 `get_updates_request`
- 遗漏其中一个会导致部分 API 调用（如 getUpdates）不走代理

### 约束 5：`_on_polling_error` 必须是同步函数

- `python-telegram-bot` 要求 `error_callback` 是同步函数
- **不能**定义为 `async def`，否则会被忽略

### 约束 6：延迟导入 telegram 库

- `_import_telegram()` 在 `start()` 时才调用
- 避免未安装 `python-telegram-bot` 时影响其他适配器加载
- 全局变量 `telegram`、`Application`、`Update`、`ContextTypes` 延迟初始化

---

## 十一、修改检查清单

修改 Telegram 适配器相关代码时，请逐一确认：

- [ ] `message.audio` 是否归入 `content.files` 而非 `content.voices`？
- [ ] `allowed_updates` 是否包含了需要接收的所有 update 类型？
- [ ] 发送消息的 Markdown 解析失败时是否有纯文本降级？
- [ ] 新增的发送类型（视频/sticker 等）是否在 `send_message()` 中处理？
- [ ] caption 长度是否不超过 1024 字符？
- [ ] 大文件（>20MB）下载失败时是否有友好提示？
- [ ] `from_user` 为 None 的情况是否处理？
- [ ] Polling watchdog 重启是否保持 `drop_pending_updates=False`？
- [ ] 代理是否同时设置给 `request` 和 `get_updates_request`？
- [ ] `_on_polling_error` 是否保持为同步函数（非 async）？
- [ ] 新增命令是否同步更新了 `set_my_commands` 列表？
- [ ] 多图场景是否使用 `sendMediaGroup` 而非逐条发送？
- [ ] 配对验证是否在消息转换之前执行？
- [ ] `deliver_artifacts` 中新增的类型是否有对应的发送方法？

---

## 十二、协议参考

- Telegram Bot API 官方文档: https://core.telegram.org/bots/api
- python-telegram-bot 文档: https://docs.python-telegram-bot.org/
- python-telegram-bot GitHub: https://github.com/python-telegram-bot/python-telegram-bot
- Bot API 文件大小限制: https://core.telegram.org/bots/api#getfile (20MB download / 50MB upload)
- Telegram Bot API 本地服务器（解除限制）: https://core.telegram.org/bots/api#using-a-local-bot-api-server
- koishi Telegram 适配器 (satorijs): https://github.com/satorijs/satori (`adapters/telegram/`)
- AstrBot Telegram 适配器: https://github.com/Soulter/AstrBot (`astrbot/core/platform/sources/telegram/`)

---

## 十三、问题记录：图片双发与 300s 超时取消（2026-03-18 实证）

### 现象

1. 用户请求生成图片后，Bot 先发送了 **photo**，紧接着又以 **document**（文件卡片）形式发送同一张图片
2. 随后 Bot 发送 "🚫 请求已取消"，后续请求也返回相同取消消息

### 日志实证时间线

```
16:35:24  收到消息："生成一张可爱的英国长毛猫"
16:35:40  generate_image 调用（Iter 1）
16:36:02  图片生成成功（22s），保存到 data/generated_images/
16:36:06  deliver_artifacts 调用，artifacts=[{type=image, ...}]（仅 image，无 file）
16:36:26  [IM] send_image failed for telegram:telegram-bot-main: Timed out（20s 超时）
16:36:47  deliver_artifacts 返回 ok=false, status=failed
16:36:51  Iter 3 final_answer → ArtifactValidator FAILED → LLM verify INCOMPLETE
16:38:50  Iter 4 final_answer → ArtifactValidator FAILED → 继续循环
16:40:24  dispatch_cancelled, elapsed_ms=300000（精确 300s = AGENT_HANDLER_TIMEOUT 默认值）
```

### 根因分析

**问题一：photo + document 双发**

`_send_image`（im_channel.py）先调用 `adapter.send_image`（→ send_photo），失败后无条件降级到 `adapter.send_file`（→ send_document）。当 send_photo 在 Telegram 服务端成功但客户端 HTTP 响应超时时，photo 已投递但异常触发了 send_document 降级，导致双发。

贡献因素：日志显示大量 `Conflict: terminated by other getUpdates request` 错误，表明有多个 Bot 实例使用同一 token 进行长轮询，导致网络不稳定和 API 超时。

**问题二：ArtifactValidator 无限重试 → 300s 超时取消**

deliver_artifacts 返回 `ok=false` 后，ArtifactValidator 检测到 `delivery_receipts` 中有 `status=failed`，返回 FAIL。LLM 验证也判定 INCOMPLETE。Agent 进入无限重试循环（每轮产出 final_answer → ArtifactValidator FAIL → LLM INCOMPLETE → 继续），直到 Gateway 的 `AGENT_HANDLER_TIMEOUT`（默认 300s）触发任务取消。

delegation_logs 确认：`dispatch_cancelled, elapsed_ms=300000`。

### 修复措施

1. **`_send_image` 超时不降级**（im_channel.py）：区分超时与其他错误，超时时直接返回 "⚠️ 图片发送超时（可能已发送成功）" 而非降级到 send_document
2. **ArtifactValidator FAIL 降级为 PASS**（response_handler.py）：交付失败是基础设施问题而非 Agent 过错，不应阻塞任务完成
3. **去重 key 去除 art_type 前缀**（im_channel.py）：`dedupe_key` 从 `{art_type}:{sha256}` 改为 `content:{sha256}`，同一文件无论以 image 还是 file 类型发送都被去重
4. **generate_image hint 加强**（system.py）：明确指导"仅需调用一次，不要以 file 类型重复发送"

### Bot 多实例冲突说明

日志中持续出现的 `Conflict: terminated by other getUpdates request` 说明存在多个进程使用同一 Bot Token 进行长轮询。可能原因：
- 旧进程未完全退出，新进程已启动
- 配置中同时启用了 `TELEGRAM_ENABLED`（全局 env）和 `im_bots` 中的 telegram bot

解决方法：确保同一 Token 只有一个 Bot 实例运行。重启前先确认旧进程已停止。

## 十四、问题记录：文本消息被 LLM 误判为语音命令（2026-03-19 实证）

### 现象

用户发送纯文本消息 "v6 策略昨天没有修复的是哪部分，继续完成修复"，Bot 回复"我收到语音命令：v6 策略昨天没有修复的是哪部分，继续完成修复。请确认以上识别结果是否准确..."，要求用户确认。

Bot 将纯文本消息当作语音转写来处理，并主动走了一个不必要的"语音确认"流程。

### 根因分析

**关键事实**："我收到语音命令" 文本在代码库中不存在任何位置——这完全是 LLM 自主生成的回复，而非系统标记。

**原因一：会话历史中 `[语音转文字:]` 标签的误导**

- `MessageContent.to_plain_text()` 将语音消息格式化为 `[语音转文字: <transcription>]`
- 该 `plain_text` 在 `_handle_message` 中被原样记录到会话历史（`session.add_message`）
- 但发给 Agent 处理的 `input_text` 会将此标签替换为纯转写文字（无标签）
- 结果：历史中有大量 `[语音转文字:]` 标签，当前消息无标签 → LLM 无法区分来源
- 系统提示词说"你收到的消息中，语音内容已经被转写为文字了" → 进一步误导 LLM 认为所有文本可能是语音转写

**原因二：`pending_audio` 元数据泄漏 BUG**

- `_call_agent` 中 `pending_audio` 的清理代码（`session.set_metadata("pending_audio", None)`）不在 `finally` 块中
- 如果上一次语音消息的 Agent 调用因异常失败（非 TimeoutError），清理被跳过
- 下一条纯文本消息进来时，`pending_audio` 仍残留在 session 中
- `_build_messages_for_llm` 读取到残留的旧 `pending_audio` → LLM 收到过期的音频数据

**原因三：`message.audio` 错误分类为 `content.voices`**

- `telegram.py` 的 `_convert_message` 将 Telegram 音频文件（`message.audio`，如 MP3/FLAC）归入 `content.voices`
- 这导致音频文件走了 STT 转写流程，在历史中留下 `[语音转文字:]` 标签
- 进一步加剧了原因一中标签泛滥的问题

### 修复措施

1. **`pending_audio` 清理移至 `finally` 块**（gateway.py `_call_agent`）：
   - 将 `session.set_metadata("pending_*", None)` 等 8 项清理操作从 `try` 正常路径移到 `finally` 块
   - 确保无论成功、异常还是取消，临时数据都会被清理

2. **`message.audio` 重新分类为文件**（telegram.py `_convert_message`）：
   - `content.voices.append(media)` → `content.files.append(media)`
   - 音频文件不再走 STT 转写流程，避免在历史中产生误导性的 `[语音转文字:]` 标签

3. **为语音转写添加来源标记**（gateway.py `_call_agent`）：
   - 语音转写替换 `input_text` 时，添加 `[来源:语音转写]` 前缀
   - LLM 可以明确判断：有前缀 = 语音消息，无前缀 = 文本消息

4. **更新系统提示词**（agent.py 系统提示）：
   - 新增"消息来源判断规则"：明确列出三种语音标记 vs 无标记 = 文本
   - 添加"绝对禁止"条款：禁止将无标记的文本当作语音命令处理
   - 更新语音处理流程说明，强调无标记 = 文本输入

### 影响范围

- 所有 IM 通道（Telegram、WeWork、DingTalk、Feishu 等）共享 gateway 和 agent 代码，修复均有效
- `message.audio` 分类修复仅影响 Telegram 适配器（其他适配器需各自检查）
