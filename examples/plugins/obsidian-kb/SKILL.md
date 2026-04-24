---
name: obsidian-kb/ofm-guide
description: Guide for creating and editing Obsidian notes using Obsidian Flavored Markdown (OFM). Covers wikilinks, embeds, callouts, YAML properties, and knowledge organization best practices.
---

# Obsidian Flavored Markdown Guide

When creating or editing notes in the user's Obsidian vault, follow these conventions.

## Core Principles

1. **Ask before creating** — confirm the save location and vault structure first.
2. **Use OFM syntax** — prefer wikilinks, callouts, and embeds over standard Markdown equivalents.
3. **Atomic notes** — one concept per note, connected through links.
4. **Metadata-driven** — use YAML frontmatter for searchability and Dataview queries.

## Wikilinks

```markdown
[[Note Name]]                 # link to a note
[[Note Name|Display Text]]    # custom display text
[[Note Name#Heading]]         # link to a heading
[[Note Name#^block-id]]       # link to a block
```

- Use descriptive, unique note names. Avoid special characters: `[ ] # ^ | \`
- Prefer full note names over path-based links (Obsidian auto-resolves)

## Embeds

```markdown
![[Note Name]]                # embed entire note
![[Note Name#Heading]]        # embed a section
![[image.png]]                # embed image
![[image.png|300]]            # embed with width
```

## Callouts

```markdown
> [!note] Title
> Content here

> [!tip] Tip title
> Useful advice

> [!warning] Warning
> Important caution

> [!info]- Collapsible (collapsed by default)
> Hidden content
```

Available types: `note`, `tip`, `warning`, `important`, `info`, `abstract`, `todo`, `example`, `question`, `quote`, `bug`, `success`, `failure`, `danger`

## YAML Frontmatter

Always start notes with frontmatter properties:

```yaml
---
title: Note Title
date: 2026-03-22
tags: [topic1, topic2]
aliases: [alternate-name]
cssclass: custom-class
---
```

Key fields:
- `tags` — array format `[tag1, tag2]` preferred over inline `#tags`
- `aliases` — alternative names for linking
- `date` — ISO format YYYY-MM-DD

## Daily Notes

Format: `YYYY-MM-DD.md` in the configured daily folder.

Template:
```markdown
---
date: {{date}}
tags: [daily]
---

# {{date}}

## Tasks
- [ ] 

## Notes

## Reflection
```

## Folder Organization

Common vault structures:
- `Inbox/` — new unsorted notes
- `Projects/` — active project notes
- `Areas/` — ongoing responsibility areas
- `Resources/` — reference material
- `Archive/` — completed items
- `Daily/` — daily notes
- `Templates/` — note templates

## Best Practices

- Use `[[wikilinks]]` instead of `[text](url)` for internal links
- Add `tags` in frontmatter rather than inline `#tags` for consistency
- Create MOC (Map of Content) notes to organize related topics
- Keep file names concise but descriptive
- Use callouts for important information rather than bold/italic

