---
name: openakita/skills@obsidian-skills
description: Manage Obsidian vaults with full support for Obsidian Flavored Markdown — wikilinks, embeds, callouts, YAML properties, Dataview queries, Canvas, and Bases. Organize notes using MOCs (Maps of Content), atomic note principles, and consistent folder/tag taxonomies. Always ask the user where to save before creating notes.
license: MIT
metadata:
 author: openakita
 version: "1.0.0"
 upstream: kepano/obsidian-skills
---

# Obsidian Note Management Skill

Help users create, organize, and manage notes in their Obsidian knowledge vault, following Obsidian Flavored Markdown (OFM) standards and knowledge management best practices.

---

## Core Principles

1. **Always ask where to save before creating notes** — Never assume paths; confirm the user's vault structure and target folder first.
2. **Follow Obsidian Flavored Markdown** — Use wikilinks, embeds, callouts, and other OFM-specific syntax.
3. **Atomic notes** — Each note focuses on one concept, building a knowledge network through links.
4. **Metadata-driven** — Use YAML frontmatter properties to ensure notes are searchable and queryable.

---

## Obsidian Flavored Markdown

### Wikilinks (Internal Links)

Obsidian uses `[[]]` syntax for internal links, which is the most important distinction from standard Markdown.

```markdown
[[]] #
[[|Display]] # Display
[[#]] #
[[#^block-id]] #
[[#|Display]] # +
```

**Best practices:**
- Note names should be descriptive and unique, avoid special characters `[ ] # ^ | \`

### Embeds

Use `![[]]` syntax to embed other notes or resources directly into the current note.

```markdown
![[]] #
![[#]] #
![[#^block-id]] #
![[.png]] #
![[.png|300]] #
![[.mp3]] #
![[.mp4]] #
![[.pdf]] # PDF
![[.pdf#page=3]] # PDF
```

### Callouts

Based on Markdown blockquote syntax extension, used to highlight important information.

```markdown
> [!note]
>

> [!tip]
> have

> [!warning]
> need

> [!important] need
>

> [!info]
>

> [!question]
> need

> [!example]
>

> [!abstract] need
> or

> [!todo]
> need

> [!quote]
>
```

** Callout: **
```markdown
> [!tip]- Click # Default
>

> [!tip]+ Click # Default
>
```

** Callout: **
```markdown
> [!note]
>
>> [!tip]
>>
```

### Tags

```markdown
# #
#/ #
```

Declare tags in frontmatter:
```yaml
tags:
- Manage
- Manage/PKM
```

### Comments

```markdown
%%thisYes Obsidian, notwillinDisplay%%

%%

alsoYes
%%
```

### Math Formulas

```markdown
Inline formula:$E = mc^2$

Block formula:
$$
\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}
$$
```

### Mermaid Diagrams

````markdown
```mermaid
graph TD
A[] --> B{}
 B -->|Yes| C[Execute]
B -->|No| D[]
```
````

---

## YAML Frontmatter Properties

YAML frontmatter,. 

### Standard Properties

```yaml
---
title:
aliases:
- 1
- 2
tags:
- 1
- /
date: 2026-03-01
created: 2026-03-01T10:30:00
modified: 2026-03-01T14:20:00
author:
status: draft | in-progress | reviewed | published
type: note | moc | reference | project | daily
cssclasses:
 - custom-class
publish: true
---
```

### Custom Properties (for Dataview)

Add custom fields based on note type for Dataview queries:

```yaml
---
#
book_title: ""
book_author: "Cal Newport"
rating: 4
started: 2026-01-15
finished: 2026-02-20
category:
---
```

```yaml
---
#
project: OpenAkita
priority: high
deadline: 2026-06-30
stakeholders:
 - Alice
 - Bob
---
```

```yaml
---
# will
meeting_type: standup | review | planning
participants:
-
-
decisions:
-
action_items:
- "[ ] "
---
```

### Property Types

Obsidian supports the following property types:
- **Text**: plain text
- **List**: array values
- **Number**: numeric values
- **Checkbox**: `true`/`false`
- **Date**: `YYYY-MM-DD`
- **Date & time**: `YYYY-MM-DDTHH:mm:ss`
- **Aliases**: alias list (built-in)
- **Tags**: tag list (built-in)

---

## Folder Structure and Naming Conventions

### Recommended Vault Structure

```
MyVault/
├── 00 - Inbox/ # Quick capture, to be organized
├── 01 - Projects/ # Active projects
│ ├── ProjectA/
│ └── ProjectB/
├── 02 - Areas/ # Ongoing areas of focus
│ ├── /
│ ├── /
│ └── /
├── 03 - Resources/ # Reference materials
│ ├── Reading Notes/
│ ├── Article Clippings/
│ └── Course Notes/
├── 04 - Archive/ # Completed/inactive
├── 05 - Templates/ # Templates
├── 06 - Daily Notes/ # Daily notes
├── 07 - MOCs/ # Maps of Content
├── Attachments/ # Attachments (images, PDFs, etc.)
└── Canvas/ # Canvas files
```

### File Naming Conventions

| Type | Format | Example |
|------|---------|------|
| Regular note | Descriptive name | `Atomic Note Methodology.md` |
| Daily note | `YYYY-MM-DD` | `2026-03-01.md` |
| Meeting note | `YYYY-MM-DD Topic` | `2026-03-01 Product Review.md` |
| MOC | `MOC - ` | `MOC - Manage.md` |
| Template | `Template - ` | `Template -.md` |
| Project home | ` - Home` | `OpenAkita - Home.md` |

**: **
-: `/ \: *? " < > |`
- Useand (Obsidian ) 
- Names should be self-describing, understandable without folder paths

---

## MOC (Maps of Content)

MOCs are navigation hubs that connect related notes, serving as a "table of contents" and "mind map".

### MOC Template

```markdown
---
title: MOC - Manage
type: moc
tags:
 - MOC
- Manage
date: 2026-03-01
---

# Manage

> [!abstract]
> Manage (PKM), and. 

## Core

- [[]]
- [[]]
- [[]]
- [[]]

## Method

- [[PARA ]]
- [[Zettelkasten ]]
- [[Building a Second Brain]]
- [[CODE ]]

## Tooland

- [[Obsidian Use]]
- [[Dataview ]]
- [[]]

## Related MOC

- [[MOC - ]]
- [[MOC - ]]
- [[MOC - ]]
```

### MOC

1. **** — MOC, MOC MOC
2. **Update** — YesNoneed MOC
3. **** — in MOC
4. **need** —, and

---

## Dataview

frontmatter and Dataview. 

### Dataview

**: **
````markdown
```dataview
TABLE rating, book_author, finished
FROM "03 - Resources/"
WHERE rating >= 4
SORT finished DESC
```
````

**: **
````markdown
```dataview
LIST
FROM #Manage AND -"04 - Archive"
WHERE status = "in-progress"
SORT priority ASC
```
````

**: **
````markdown
```dataview
TASK
FROM "01 - Projects"
WHERE!completed
GROUP BY file.link
```
````

**: **
```markdown
Yes `= date(today)`, have `= length(file.lists)`. 
```

### Dataview

- Use (in) 
- Use `YYYY-MM-DD`
- Use `true`/`false`
- Use YAML

---

## Canvas Supports

Obsidian Canvas Yes. 

### Canvas files

Canvas files `.canvas` JSON, Includes (nodes) and (edges). 

```json
{
 "nodes": [
 {
 "id": "node1",
 "type": "text",
"text": "",
 "x": 0, "y": 0,
 "width": 250, "height": 60
 },
 {
 "id": "node2",
 "type": "file",
"file": "03 - Resources/.md",
 "x": 300, "y": 0,
 "width": 250, "height": 60
 }
 ],
 "edges": [
 {
 "id": "edge1",
 "fromNode": "node1",
 "toNode": "node2",
 "label": "Includes"
 }
 ]
}
```

### Canvas

| Type | Description | |
|------|------|---------|
| `text` | | `text` |
| `file` | Vault | `file` |
| `link` | | `url` |
| `group` | | `label` |

### Create Canvas

-
- 200–400px
- Use `group`
- Includes `label`

---

## Bases Supports

Obsidian Bases Yes Obsidian 1.8+. 

### Bases

- Bases `.base`, in Vault
- Automatic frontmatter properties Extract
- Supports,,, 
- Dataview

### Create Bases

1. **** — Use frontmatter
2. **** — in Obsidian Set, 
3. **** — Useor
4. **** —, and

---

## Notes

###

```markdown
---
title: "{{date:YYYY-MM-DD}}"
type: daily
date: {{date:YYYY-MM-DD}}
tags:
 - daily
---

# {{date:YYYY-MM-DD dddd}}

##
- [ ] 

## Notesand


##

### Complete


### Tomorrow

```

###

```markdown
---
title: "{{title}}"
type: reference
book_title: ""
book_author: ""
category: ""
rating: 
started: {{date:YYYY-MM-DD}}
finished: 
status: in-progress
tags:
-
---

# {{title}}

> [!info]
> - ****:
> - ****:
> - **ISBN**: 

## Core


##

###


##

> 

##


## Row
- [ ] 

## Related
- [[]]
```

###

```markdown
---
title: "{{title}}"
type: project
project: ""
status: active
priority: medium
deadline: 
stakeholders: []
tags:
-
---

# {{title}}

> [!abstract]
> 

##


##

| | | |
|--------|---------|------|
| | | 🔴 |

## Tasks
- [ ] 

## will
- [[]]

## References
- [[]]

##

```

---

## Workflow

### Create

1. **Save** —
2. **orCreate** — Use
3. ** frontmatter** — haveFull
4. **** — Use OFM
5. **** — wikilinks
6. **Update MOC** — have MOC, 

### Inbox

1. `00 - Inbox/`
2. or frontmatter
3.
4. andhave
5. Update MOC

### Refactor

1. (and) 
2.
3.
4. Update
5. not `04 - Archive/`

---

## Notes

- **notneedSave** —
- **** —
- **Use wikilinks** — and Markdown
- **YAML frontmatter Yes** — nothave
- **Use** — Dataview and Bases
- **in** — `Attachments/`
- **** — YesNoAutomaticUpdate
- ** Vault** — Use Git or Obsidian Sync