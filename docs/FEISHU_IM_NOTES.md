# 飞书 IM 通道 — 功能清单 / 逻辑约束 / 已知问题

> 本文档记录飞书适配器（`feishu.py`）及其与 gateway、session、prompt 等模块的交互逻辑。
> 目的：后续修改或修 bug 时不遗漏既有逻辑约束。

---

## 一、核心功能清单

### 1. 消息接收

| 功能 | 关键代码位置 | 说明 |
|------|------------|------|
| WebSocket 长连接 | `start_websocket()` | 推荐方式，SDK 内置重连 |
| Webhook 回调 | `handle_event()` | 需外部 HTTP 服务器 |
| 消息类型解析 | `_convert_message()` | 支持 text/image/file/audio/video/sticker/merge_forward |
| @mention 检测 | `_convert_message()` 内 `is_mentioned` | 遍历 mentions 匹配 `_bot_open_id` |
| @所有人 检测 | `_convert_message()` 内 `@_all` | 缓冲为事件，不影响 is_mentioned |
| 话题 ID 映射 | `thread_id = root_id` | 同时设置 `reply_to = root_id` |
| 用户名提取 | mentions 占位符替换 | `@_user_N` → 实际名称 |

### 2. 消息发送

| 功能 | 方法 | reply_to 支持 |
|------|------|:---:|
| 文本（纯文本/markdown/卡片） | `send_message()` | ✅ |
| 语音 | `send_voice()` | ✅ |
| 文件/视频 | `send_file()` | ✅ |
| 图片 | `send_photo()` | ✅ |
| 卡片 | `send_card()` | ✅ |
| 辅助文本 | `_send_text()` | ✅ |
| 图片+文本混合 | `send_message()` 图片分支 | ✅ 图片发送后追加文本 |
| 思考状态指示 | `send_typing()` → `_send_thinking_card()` | ✅ 作为用户消息的回复 |
| 占位卡片更新 | `_patch_card_content()` | — PATCH API 更新卡片内容 |
| 消息删除（降级） | `_delete_feishu_message()` | — PATCH 失败时清理占位卡片 |

**约束**：所有发送方法的 `reply_to` 参数用于话题内回复。当 `reply_to` 有值时，使用 `ReplyMessageRequest`；否则使用 `CreateMessageRequest`。新增发送方法必须遵循此模式。

### 3. 启动流程

| 步骤 | 说明 |
|------|------|
| 创建 lark Client | `app_id` + `app_secret` |
| 获取 `_bot_open_id` | 3 次重试，间隔 2 秒 |
| 权限探测 `_probe_capabilities()` | 用无效 ID 调 API 判断权限 |
| 注册事件处理器 | `_setup_event_dispatcher()` |
| 启动 WebSocket/Webhook | 开始接收消息 |

### 4. 事件感知（第三层）

| 事件 | SDK 注册方法 | 处理器 |
|------|------------|--------|
| 群信息更新 | `register_p2_im_chat_updated_v1` | `_on_chat_updated` |
| 机器人入群 | `register_p2_im_chat_member_bot_added_v1` | `_on_bot_chat_added` |
| 机器人被移出 | `register_p2_im_chat_member_bot_deleted_v1` | `_on_bot_chat_deleted` |
| @所有人 | `_convert_message()` 中检测 | 缓冲为 `at_all` 事件 |

事件缓冲在 `_important_events` dict 中，per-chat 上限 10 条，`get_pending_events()` 取出并清空。

### 5. IM 查询工具

| 工具名 | 适配器方法 | 说明 |
|--------|----------|------|
| `get_chat_info` | `get_chat_info()` | 群名/描述/群主/成员数 |
| `get_user_info` | `get_user_info()` | 姓名/邮箱/头像 |
| `get_chat_members` | `get_chat_members()` | 群成员列表 |
| `get_recent_messages` | `get_recent_messages()` | 最近 N 条消息 |

---

## 二、关键逻辑约束（修改时必须保持）

### 约束 1：群聊"偷听"防护（双重过滤）

群消息的过滤有**两道关卡**，缺一不可：

1. **入队前过滤**（`gateway._on_message` 中断路径）：
   当会话正在处理时，新消息进入中断逻辑。对 `mention_only` 模式的群消息，
   如果未 @机器人且不是 stop/skip 指令，**必须 return 丢弃**，不能 INSERT 注入。

2. **出队后过滤**（`gateway._handle_message`）：
   消息从队列取出后，按 `GroupResponseMode` 判断是否处理。
   `mention_only` + `not is_mentioned` → return。

**根因说明**：历史上只有第 2 道关卡，导致用户 @bot 后 bot 处理期间，
同一用户在群里的非 @ 消息被 INSERT 注入上下文，表现为"偷听"。

### 约束 2：`is_mentioned` 的保守策略

- `_bot_open_id` 为 None → `is_mentioned = False`（不偷听，但群里无响应）
- `_bot_open_id` 有值但 mentions 中无匹配 → `is_mentioned = False`
- 绝不能在 `_bot_open_id` 为 None 时 fallback 到 `True` 或 `bool(mentions)`

### 约束 3：话题隔离对称性

- **接收端**：`thread_id = message["root_id"]`，`reply_to = message["root_id"]`
- **发送端**：`reply_target = message.reply_to or message.thread_id` → `ReplyMessageRequest`
- **session 层**：`session_key` 包含 `thread_id`（四段式 `channel:chat_id:user_id:thread_id`）
- **序列化**：`to_dict` / `from_dict` 都包含 `thread_id`

### 约束 4：记忆共享是设计意图

底层记忆（语义记忆、Scratchpad）是跨会话共享的。
"记忆串台"通过 **system prompt 注入 IM 环境信息** 解决，让 LLM 知道当前上下文：
- 平台名称、聊天类型、chat_id、thread_id
- 机器人身份（bot_id）
- 已确认可用能力列表
- 共享记忆警告（提醒 LLM 审慎引用来源不明的记忆）

### 约束 5：事件注入使用 system role

上下文边界标记和待处理事件注入到 session context 时，`role` 必须为 `"system"`。
不能用 `"user"`，否则 LLM 会将系统元数据误解为用户请求。

### 约束 6：IM 工具的平台兼容检查

`IMChannelHandler._handle_im_query_tool` 使用 `type(adapter).method is ChannelAdapter.method`
判断子类是否重写了方法。不能用 `getattr(adapter, method) is ChannelAdapter.method`
（bound method vs function 永远不相等）。

---

## 三、已修复的历史问题

| # | 问题 | 根因 | 修复方案 | 涉及文件 |
|---|------|------|---------|---------|
| 1 | 群聊"偷听" | (a) `_bot_open_id` 为 None 时 is_mentioned fallback 到 `bool(mentions)` (b) 中断路径无群聊过滤 | (a) fallback 改为 False + 重试获取 bot_open_id (b) 中断路径加群聊模式检查 | feishu.py, gateway.py |
| 2 | 记忆串台 | LLM 不知道当前环境 | system prompt 注入 IM 环境信息 + 共享记忆警告 | builder.py, gateway.py |
| 3 | 话题功能失效 | (a) 未传 thread_id (b) 发送用 CreateMessage 而非 ReplyMessage | (a) root_id→thread_id 映射 (b) 全部发送方法支持 reply_to | feishu.py, gateway.py, session.py, manager.py |
| 4 | 权限感知不准 | 无运行时权限检测 | _probe_capabilities 启动时探测 | feishu.py, builder.py |
| 5 | "单机AI"行为 | 无环境感知、无 IM 工具 | 环境信息注入 + 4 个 IM 查询工具 | feishu.py, builder.py, im_channel.py (定义+处理器), agent.py |
| 6 | session 序列化丢 thread_id | to_dict/from_dict 遗漏 | 补全序列化字段 | session.py |
| 7 | voice/file/photo/card 脱离话题 | 无 reply_to 参数 | 全部发送方法加 reply_to 分支 | feishu.py |
| 8 | 图片+文本丢文本 | 图片分支未发送伴随文本 | 发送图片后追加 _send_text | feishu.py |
| 9 | 事件注入 role=user | 误导 LLM | 改为 role=system | gateway.py |
| 10 | TOOLS 列表不全 | handler 白名单未更新 | 同步 8 个工具 | im_channel.py handler |
| 11 | 方法重写检测失败 | bound method is function 永远 False | 改用 type(adapter).method is base.method | im_channel.py handler |
| 12 | _resolve_task_session_id 跨话题误匹配 | 模糊匹配未考虑 thread_id | 加入 thread_id 过滤 | gateway.py |
| 13 | 多 Bot 只有一个能收消息 | `lark_oapi.ws.client` 模块级 `loop` 变量被多实例覆盖，运行时 `create_task` 投递到错误的事件循环 | 用 `importlib.util` 为每个 WS 线程创建独立模块副本，各实例 `loop` 完全隔离（移除旧的 `_ws_startup_lock` 方案） | feishu.py |
| 14 | `feishu_enabled` 与 `im_bots` 重复注册 | 同一 app_id 创建两个 adapter，WebSocket 连接互踢 | 启动时检查 im_bots 是否已有相同 app_id，重复则跳过 | main.py |
| 15 | 消息去重缺失 | WebSocket 重连可能重复投递消息 | `OrderedDict` LRU 去重，容量 500，WebSocket 和 Webhook 路径均覆盖 | feishu.py |
| 16 | 收到消息无已读回执 | 飞书不支持机器人标记已读 | `add_reaction` 添加 [了解] (emoji_type=Get) 表情回复作为回执替代 | feishu.py |
| 17 | `_parse_post_content` 解析失败 | 未处理 i18n 层级 (`post→zh_cn→content`)，缺少 img/media/emotion 标签 | 提取语言层 + 补充标签解析 | feishu.py |
| 18 | `@_user_N` 占位符泄露 | mentions 占位符未替换为实际名称 | `_convert_message` 中遍历 mentions 替换 | feishu.py |
| 19 | `asyncio.get_event_loop()` 弃用警告 | Python 3.12+ 弃用，async 上下文应用 `get_running_loop()` | 全部 12 处替换 | feishu.py |
| 20 | `send_message` 媒体 fallthrough | voices/files/videos 无委托逻辑，掉入空文本分支 | 入口处 early return 委托给 send_voice/send_file | feishu.py |
| 21 | INSERT 路径消息丢失 | `pending_user_inserts` 仅在工具执行间隙消费，无工具调用时滞留 | 任务完成后 `_rescue_pending_inserts` 回收到中断队列 | gateway.py |
| 22 | 系统重启后消息被重复回复 | 飞书 WS 断连后重投递旧消息，`_seen_message_ids` 内存字典在重启时清空 | 增加 `create_time` 时间窗口防护：消息创建超过 120 秒的重投递直接丢弃（WebSocket 和 Webhook 路径均覆盖） | feishu.py |
| 23 | 飞书语音消息处理卡死 | 飞书语音为 Opus 格式（`.opus`），`ensure_whisper_compatible` 只处理 SILK，Opus 直传 Whisper 导致 ffmpeg 内部长时间无输出；Gateway 无超时保护 | (a) `ensure_whisper_compatible` 新增 `.opus/.ogg/.amr/.webm/.wma/.aac` → WAV 的 ffmpeg 转换（30s 超时）(b) Gateway `_process_voice` 增加 60s 下载超时 + 120s 转写超时 (c) `asyncio.gather` 改为 `return_exceptions=True` 避免单个失败拖垮全部 | audio_utils.py, gateway.py |

---

## 四、已知未修复问题（后续迭代处理）

| # | 严重度 | 问题 | 说明 |
|---|--------|------|------|
| 1 | 中 | smart 模式批量缓冲未接入 | `SmartModeThrottle.buffer_message/drain_buffer` 是死代码，smart 模式只有频率限制 |
| 2 | 中 | Per-Bot 群响应模式未实现 | `_get_group_response_mode(channel)` 参数 `channel` 未使用，多 Bot 共享同一模式 |
| 3 | 中 | download_media 大文件 OOM | `response.file.read()` 整体读入内存 |
| 4 | 中 | 无 API 限流/429 退避 | 高频调用可能被飞书限流 |
| 5 | 低 | _important_events chat key 累积 | 不活跃群的 key 不会清理（每次消费会清空值但 key 不会从 dict 消失） |
| 6 | 低 | Webhook asyncio.create_task 无保护 | 非 async 上下文调用时可能 RuntimeError |
| 7 | 低 | download_media 未校验 response.file | success=True 但 file=None 的边缘情况 |
| 8 | 低 | `@_all` 检测兜底条件可能误报 | `(key and not open_id)` 对注销用户也匹配 |
| 9 | 低 | _PLATFORM_NAMES 缺少 qq 映射 | 不影响功能，显示原始名 |

---

## 五、数据流概览

### 消息接收流程

```
飞书 WebSocket 事件
  → _on_message_receive()          # SDK 事件回调（同步，在 SDK 线程）
    → _handle_message_async()      # run_coroutine_threadsafe 切到主 loop
      → 消息去重（OrderedDict LRU） # message_id 已见过则跳过
      → create_time 陈旧消息防护    # 超过 120s 的重投递丢弃
      → add_reaction(Get)          # fire-and-forget 已读回执（[了解] 表示正在处理）
      → 记录 _last_user_msg[chat_id] = msg_id  # 供 send_typing 回复定位
      → _convert_message()         # 提取消息内容、is_mentioned、thread_id
      → _emit_message()            # 触发 gateway 回调
        → gateway._on_message()    # 中断检查 + 群聊过滤（第 1 道）
          → _message_queue.put()   # 入队
            → _process_loop()
              → _handle_message()  # 系统命令检查 →
                → _send_typing()   # 调用 adapter.send_typing()
                  → 首次: _send_thinking_card() → "💭 思考中..." 卡片
                  → 后续: 已有卡片则跳过
                → _call_agent_with_typing()  # _keep_typing 每 4s 重调 send_typing
                → Agent 返回 → send_message() →
                  → pop _thinking_cards → PATCH 卡片为最终回复
                  → PATCH 失败 → 删除占位卡片 → 正常发送
```

### 消息发送流程

```
Agent 生成回复
  → gateway 构造 OutgoingMessage（附带 reply_to / thread_id）
    → adapter.send_message()
      → 检查 _thinking_cards[chat_id]
        → 有卡片: PATCH 更新为最终回复内容 → 成功则直接返回
        → PATCH 失败: 删除占位卡片 → 继续正常发送
      → reply_target = message.reply_to or message.thread_id
      → 媒体类型委托: send_voice/send_file (带 reply_to)
      → 文本/图片: ReplyMessageRequest (reply_target) 或 CreateMessageRequest
      → 图片+文本: 先发图片，后追加 _send_text
```

### session_key 格式

```
三段式（非话题）: {channel}:{chat_id}:{user_id}
四段式（话题内）: {channel}:{chat_id}:{user_id}:{thread_id}

示例:
  feishu:oc_abc123:ou_user1                     # 主聊天
  feishu:oc_abc123:ou_user1:om_root_msg_id      # 话题内
```

---

## 六、修改检查清单

修改飞书 IM 通道相关代码时，请逐一确认：

- [ ] 新增的发送方法是否支持 `reply_to` 参数？
- [ ] `is_mentioned` 逻辑是否仍然在 `_bot_open_id=None` 时返回 False？
- [ ] `session_key` 是否在所有生成位置保持一致（session.py / manager.py / gateway.py）？
- [ ] 新增的系统消息注入是否使用 `role="system"`？
- [ ] gateway 的中断路径是否包含群聊过滤？
- [ ] IM 工具是否在 4 层（definitions / handler TOOLS / handler route / agent register）同步？
- [ ] 方法重写检测是否使用 `type(adapter).method is Base.method` 而非 `getattr`？
- [ ] thread_id 是否在序列化/反序列化中包含？
- [ ] 多 Bot 场景：`_run_ws_in_thread` 是否为每个线程创建独立模块副本（不共享 `lark_oapi.ws.client.loop`）？
- [ ] 消息去重后是否有 `create_time` 陈旧消息防护（防止重启后重复处理）？

---

## 七、多 Bot 飞书平台侧检查清单

如果代码正确但某个 Bot 仍无法收到消息，请检查飞书开发者后台：

- [ ] 该应用是否启用了「机器人」能力
- [ ] 是否订阅了 `im.message.receive_v1` 事件
- [ ] 是否发布了最新版本（草稿状态的应用不推送事件）
- [ ] 事件订阅方式是否为「长连接」模式（而非 Webhook）
- [ ] 应用可见范围是否包含目标用户/群聊
