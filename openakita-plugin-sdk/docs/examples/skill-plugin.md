# Skill 插件示例 / Skill Plugin Example

最轻量的插件类型——仅需 `plugin.json` + `SKILL.md`，无 Python 代码。

The lightest plugin type — just `plugin.json` + `SKILL.md`, no Python code.

**运行时类型 / Runtime type:** `skill` | **权限级别 / Permission Level:** Basic

---

## 目录结构 / Directory Structure

```
translate-skill/
  plugin.json
  SKILL.md
  README.md
```

## plugin.json

```json
{
  "id": "translate-skill",
  "name": "Translation Skill",
  "version": "1.0.0",
  "type": "skill",
  "entry": "SKILL.md",
  "description": "Provides translation guidelines and prompts for the AI",
  "author": "OpenAkita Team",
  "license": "MIT",
  "permissions": [],
  "provides": {
    "skill": "SKILL.md"
  },
  "category": "skill",
  "tags": ["translation", "i18n", "prompt"]
}
```

> **注意 / Note:** `type: "skill"` 的插件不需要 `plugin.py`、不需要 `Plugin` 类。`entry` 指向 SKILL.md 文件，宿主加载的是该文件的**父目录**作为 Skill 根。
>
> `type: "skill"` plugins don't need `plugin.py` or `Plugin` class. `entry` points to the SKILL.md file; the host loads its **parent directory** as the skill root.

## SKILL.md

```markdown
# Translation Assistant

Provide high-quality translations between languages.

## Guidelines

- Always preserve the original meaning and tone
- Use natural expressions in the target language
- For technical terms, provide both the translation and the original in parentheses
- When translating Chinese to English, prefer active voice
- When translating English to Chinese, use concise modern Chinese

## Supported Languages

- Chinese (Simplified / Traditional)
- English
- Japanese
- Korean
- French
- German
- Spanish

## Usage

When the user asks to translate text, follow these steps:
1. Detect the source language
2. Ask for the target language if not specified
3. Provide the translation with brief notes on any ambiguities
```

---

## Skill 与 Python 插件的区别 / Skill vs Python Plugin with Skill

| 方式 | `type` | 需要 Python？ | 用途 |
|------|--------|:---:|------|
| **纯 Skill 插件** | `skill` | 否 | 仅注入提示词/指南 |
| **Python 插件附带 Skill** | `python` | 是 | 插件逻辑 + 提示词（`provides.skill` 指定路径） |

Python 插件可以在 `provides.skill` 中声明一个 SKILL.md 路径，宿主会自动加载它。这与 `type: "skill"` 插件效果相同，但 Python 插件还可以注册工具、路由等。

A Python plugin can declare a SKILL.md path in `provides.skill`; the host loads it automatically. This has the same effect as a `type: "skill"` plugin, but the Python plugin can also register tools, routes, etc.

---

## 相关文档 / Related

- [../plugin-json.md](../plugin-json.md) — 清单文件规范 / Manifest reference
- [../getting-started.md](../getting-started.md) — 不同 type 的要求 / Requirements by type
