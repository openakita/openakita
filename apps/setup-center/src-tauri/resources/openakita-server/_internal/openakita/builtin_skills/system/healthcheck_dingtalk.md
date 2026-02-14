# DingTalk 健康检查记录

## 背景
- 报错：`openakita.channels.adapters.dingtalk` 发送图片失败，服务端返回“robot 不存在”。
- 常见原因：`robotCode` 配置错误/为空、机器人未创建或未启用、应用与群绑定不匹配。

## 当前本地配置发现（基于 `.env`）
- DINGTALK_ENABLED = true
- DINGTALK_CLIENT_ID = dingo7jnkd1c3hquoacu
- DINGTALK_CLIENT_SECRET = (已配置)

⚠️ 说明：OpenAkita 当前钉钉适配器将 `robotCode` 直接使用 `app_key`（即 DINGTALK_CLIENT_ID）。若你的机器人 `robotCode` 并非该值，则会导致 “robot 不存在”。

## 健康检查
由于缺少可用的 webhook / openConversationId / userId 等目标参数（本仓库配置中未发现），无法对“发送消息/图片”接口做真实探测。

建议人工补充以下至少一项后重试：
1. 任一会话的 `sessionWebhook`（从钉钉机器人回调消息中可获取），或
2. 群聊的 `openConversationId`，或
3. 单聊目标 `userId`。

可用于探测的接口：
- OAuth2 token: `POST https://api.dingtalk.com/v1.0/oauth2/accessToken`（仅验证 appKey/appSecret 是否有效）
- 单聊发送：`POST https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend`
- 群聊发送：`POST https://api.dingtalk.com/v1.0/robot/groupMessages/send`

## 处理结论
- 本次未对核心代码做修改（遵循限制）。
- 需要人工确认并提供正确的 `robotCode`（通常为机器人编码，不一定等于 appKey）以及可投递目标（userId/openConversationId 或 sessionWebhook）。
