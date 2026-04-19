---
name: send-sticker
description: Search and send sticker images in chat. Use during casual conversations, greetings, encouragement, or celebrations to make interactions more lively and engaging.
system: true
handler: sticker
tool-name: send_sticker
category: Communication
---

# Send

## When to Use

-, 
-
- (//) 
- / () 
-

## notUse

- (sticker_preference=never) 
- not
-
- notneed () 

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| query | string | No | Search keyword (: //) |
| mood | string | No |, and query |
| category | string | No | (: /) |

## mood

- `happy` -, 
- `sad` -, 
- `angry` -
- `greeting` - (, ) 
- `encourage` -, 
- `love` -, 
- `tired` -, 
- `surprise` -, 

## Roles

| | | |
|------|------|------|
| Default | | |
| | notUse | - |
| | | |
| | | / |
| | | |
| | | |
| | | |
| | | / |

## Examples

```
# Send
send_sticker(mood="happy")

# Search
send_sticker(query="")

#
send_sticker(query="", category="")
```