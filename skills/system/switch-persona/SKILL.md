---
name: switch-persona
description: Switch Agent persona preset. Supports 8 presets including default assistant, business, tech expert, butler, girlfriend, boyfriend, family, and Jarvis. Use when user asks to change communication style or personality.
system: true
handler: persona
tool-name: switch_persona
category: Persona
---

# Switch Persona

## When to Use

- needSwitch/
- ""/""/""
- Agent
- Use

## Parameters

| Parameter | Type | Required | Description |
|------|------|------|------|
| preset_name | string | Yes | |

## Available

- `default` - Default () 
- `business` - (, notUse) 
- `tech_expert` - (, ) 
- `butler` - (, ) 
- `girlfriend` - (, Use) 
- `boyfriend` - (, ) 
- `family` - (, ) 
- `jarvis` - (,,, ) 

## Examples

```
: ""
→ switch_persona(preset_name="girlfriend")

: ""
→ switch_persona(preset_name="business")

: ", "
→ switch_persona(preset_name="default") + update_persona_trait(dimension="formality", preference="casual")
```

## Notes

- Yes, willVianot
- Switch Agent
- `update_persona_trait`