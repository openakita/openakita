---
name: toggle-proactive
description: Toggle proactive messaging mode. When enabled, Agent proactively sends greetings, task reminders, and key recaps via IM channel. Use when user asks to enable/disable proactive messages.
system: true
handler: persona
tool-name: toggle_proactive
category: Persona
---

#

## When to Use

- need/Close
- " "
- ""
- in

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| enabled | boolean | Yes | YesNoEnable |

## Features

Agent willVia IM Send: 

- ****: (7-9 ) 
- ****:
- ****: need
- ****:
- ****: () 

##

- 3 () 
- 2
- (23:00-07:00) notSend
- Automatic

## Examples

```
: ""
→ toggle_proactive(enabled=true)

: " "
→ toggle_proactive(enabled=false)

: ""
→ update_persona_trait(dimension="proactiveness", preference="low")
```