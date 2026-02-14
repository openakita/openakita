---
name: send-sticker
description: 搜索并发送表情包图片。在闲聊、问候、鼓励等场景下使用，让对话更生动有趣。
system: true
handler: sticker
tool-name: send_sticker
category: Communication
---

# 发送表情包

## 何时使用

- 闲聊问候时，增加互动趣味
- 鼓励用户完成任务时
- 表达情绪（开心/难过/惊讶等）
- 早安/晚安问候时（配合活人感模式）
- 庆祝任务成功完成

## 何时不使用

- 商务角色（sticker_preference=never）
- 用户明确表示不喜欢表情包
- 正式的技术讨论中
- 用户情绪不好需要安慰时（文字更合适）

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| query | string | 否 | 搜索关键词（如：鼓掌/开心/加油） |
| mood | string | 否 | 情绪类型，与 query 二选一 |
| category | string | 否 | 限定分类（如：猫/程序员） |

## mood 类型

- `happy` - 开心、高兴
- `sad` - 难过、伤心
- `angry` - 生气
- `greeting` - 问候（早安、晚安）
- `encourage` - 加油、鼓励
- `love` - 爱心、比心
- `tired` - 累了、摸鱼
- `surprise` - 震惊、惊讶

## 角色配合

| 角色 | 频率 | 偏好 |
|------|------|------|
| 默认 | 偶尔 | 通用 |
| 商务 | 不使用 | - |
| 技术 | 偶尔 | 程序员 |
| 女友 | 频繁 | 可爱/心心 |
| 男友 | 适中 | 搞笑 |
| 家人 | 适中 | 通用 |
| 管家 | 偶尔 | 通用 |
| 贾维斯 | 适中 | 滑稽/程序员 |

## 示例

```
# 按情绪发送
send_sticker(mood="happy")

# 按关键词搜索
send_sticker(query="加油")

# 限定分类
send_sticker(query="笑", category="猫")
```
