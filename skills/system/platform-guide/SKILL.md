---
name: platform-guide
description: "OpenAkita Platform guide for searching and installing Agents from Agent Hub and Skills from Skill Store. Use when user asks to find, browse, or install Agents or Skills from the platform."
system: true
handler: system
tool-name: platform_guide
category: Platform
---

# OpenAkita Platform Guide — AI

> ****: needhave, **Search**. 

##? 

- " XX Agent / / Skill"
- needhave
- "havehave ", "have", ""
- Agent Hub, Skill Store, OpenAkita

****: Yes ****, havenot. 

---

## Agent

Get Agent: 

** 1 — Search**
```
search_hub_agents(query="", category="customer_service")
```
- ViewReturns Agent, Agent `id`

** 2 — (Recommendations) **
```
get_hub_agent_detail(agent_id="the-agent-id")
```
- View,,, Download

** 3 — **
```
install_hub_agent(agent_id="the-agent-id")
```
- Automatic: Download → → → Agent →
- Agent ****

### Installation? 
1. Download `.akita-agent`
2. → `skills/custom/` (: haveUpdate) 
3. → GitHub `skills/community/`
4. Write `.openakita-origin.json`
5. Agent → Automatic

---

## Skill

Get Skill: 

** 1 — Search**
```
search_store_skills(query="", trust_level="official")
```
- `trustLevel`: official () > certified () > community () 

** 2 — (Recommendations) **
```
get_store_skill_detail(skill_id="the-skill-id")
```
- View,, GitHub Stars, 

** 3 — **
```
install_store_skill(skill_id="the-skill-id")
```
- GitHub () 
- ****

---

## Local Agent (notneed) 

```
list_exportable_agents() # View
export_agent(profile_id="my-agent", version="1.0.0") #
inspect_agent_package(package_path="xxx.akita-agent") #
import_agent(package_path="xxx.akita-agent") #
```

---

## Version

| | |
|------|------|
| have skill >= | **** (Update) |
| > | **** () |
| | **** () |

---

## Platform

| | |
|------|----------|
| Agent | `.akita-agent` |
| Skill | `install_skill` GitHub; Setup Center skills.sh |
| Agent | `export_agent` |

---

## All

| | |
|------|------|
| `search_hub_agents` | Search Agent |
| `get_hub_agent_detail` | View Agent |
| `install_hub_agent` | Agent |
| `search_store_skills` | Search Skill |
| `get_store_skill_detail` | View Skill |
| `install_store_skill` | Skill |
| `submit_skill_repo` | GitHub Skill |
| `export_agent` | Agent |
| `import_agent` | Import Agent |
| `list_exportable_agents` | List Agent |
| `inspect_agent_package` | Agent |

---

## License

- Skill ****, 
- Agent `LICENSE-3RD-PARTY.md` List
-
-: zacon365@gmail.com