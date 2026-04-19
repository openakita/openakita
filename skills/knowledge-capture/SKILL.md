---
name: openakita/skills@knowledge-capture
description: Transform conversations and unstructured information into structured Notion documentation. Extract key insights, decisions, and action items. Create cross-linked knowledge bases with templates for meeting notes, how-to guides, decision records, and project documentation. Integrates with Notion API for seamless content creation.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
  upstream: tommy-ca/notion-skills/knowledge-capture
---

# Notion Knowledge Capture Skill

Transform conversations, meetings, research, and unstructured information into a structured Notion documentation system — building a searchable, linkable, and traceable team knowledge base.

---

## Core Capabilities

1. **Information Extraction** — Identify key insights, decisions, and action items from conversations and text
2. **Structural Transformation** — Convert unstructured content into Notion pages following template specifications
3. **Cross-Linking** — Establish bidirectional associations between pages to form a knowledge network
4. **Metadata Preservation** — Record participants, dates, context, and other traceability information
5. **Notion API Integration** — Directly create and update Notion content via the API

---

## Information Extraction Framework

### Key Elements to Extract from Conversations

When processing any conversation or text, systematically extract the following elements:

| Element | Description | Example |
|---------|-------------|---------|
| **Key Insight** | Valuable observations, discoveries, or conclusions | "Users prefer asynchronous communication" |
| **Decision** | Confirmed choices and their rationale | "Chose PostgreSQL over MongoDB due to transaction support requirements" |
| **Action Item** | Tasks that need follow-up | "Zhang San to complete API documentation by Friday" |
| **Open Question** | Unresolved doubts | "Do we need to support offline mode?" |
| **Risks & Dependencies** | Potential risks and external dependencies | "Depends on the stability of the third-party payment API" |
| **References** | Mentioned documents, links, tools | "Refer to RFC 7231 HTTP Semantics specification" |
| **Context** | Background and prerequisites of the discussion | "Based on Q1 user feedback data" |

### Extraction Quality Standards

- **Completeness** — Do not miss key information
- **Accuracy** — Faithfully reflect the original expression without adding inferences
- **Actionability** — Action items include owner, deadline, and acceptance criteria
- **Traceability** — Every piece of information can be traced back to its source

---

## Notion Document Templates

### Template 1: Meeting Notes

```markdown
# 📋 [Meeting Topic]

## Metadata
| Property | Value |
|----------|-------|
| **Date** | YYYY-MM-DD |
| **Time** | HH:MM - HH:MM |
| **Participants** | @Alice, @Bob, @Charlie |
| **Meeting Type** | Weekly / Review / Planning / Brainstorm / Decision |
| **Status** | In Progress / Completed / Needs Follow-up |
| **Related Project** | [[Project Name]] |

## Background & Objectives
> Briefly describe the purpose and expected outcomes of this meeting.

## Agenda
1. [ ] Topic One
2. [ ] Topic Two
3. [ ] Topic Three

## Discussion Points

### Topic 1: [Title]
**Discussion:**
- Point 1
- Point 2

**Conclusion:**
> Final consensus or decision reached.

### Topic 2: [Title]
**Discussion:**
- Point 1

**Conclusion:**
> 

## Decisions
| # | Decision | Rationale | Scope of Impact |
|---|----------|-----------|-----------------|
| 1 |          |           |                 |

## Action Items
| # | Task | Owner | Deadline | Status |
|---|------|-------|----------|--------|
| 1 |      | @Name | YYYY-MM-DD | 🔴 Not Started |

## Open Questions
- [ ] Question description → Owner: @Name

## Next Meeting
- **Date**: 
- **Agenda Preview**: 
```

### Template 2: How-To Guide

```markdown
# 🔧 How to [Complete a Specific Task]

## Metadata
| Property | Value |
|----------|-------|
| **Created** | YYYY-MM-DD |
| **Last Updated** | YYYY-MM-DD |
| **Author** | @Name |
| **Difficulty** | Beginner / Intermediate / Advanced |
| **Estimated Time** | X minutes |
| **Applicable Version** | v1.0+ |
| **Tags** | #Category1, #Category2 |

## Overview
> A one-sentence description of what problem this guide solves.

## Prerequisites
- [ ] Condition 1: description
- [ ] Condition 2: description
- [ ] Required tools/permissions: description

## Steps

### Step 1: [Step Title]
**Action:**
Detailed description of the specific operation.

**Example:**
```
Code or command example
```

**Expected Result:**
> What you should see after completion.

> ⚠️ **Note**: Common pitfalls or things to watch out for.

### Step 2: [Step Title]
**Action:**
Detailed description.

**Example:**
```
Code or command example
```

### Step 3: [Step Title]
**Action:**
Detailed description.

## Validation
How to confirm the operation succeeded:
1. Check item 1
2. Check item 2

## Troubleshooting
| Problem | Possible Cause | Solution |
|---------|---------------|----------|
| Description | Root cause analysis | Specific steps |

## Related Documents
- [[Related Guide 1]]
- [[Related Guide 2]]
- [External Link](https://...)
```

### Template 3: Decision Record

```markdown
# 🔖 DR-[Number]: [Decision Title]

## Metadata
| Property | Value |
|----------|-------|
| **Date** | YYYY-MM-DD |
| **Status** | Proposed / Approved / Deprecated / Superseded |
| **Decision Maker** | @Name1, @Name2 |
| **Related Project** | [[Project Name]] |
| **Supersedes** | [[DR-XXX]] (if applicable) |
| **Superseded By** | [[DR-YYY]] (if applicable) |

## Background
> What circumstances prompted this decision? Include technical context, business requirements, and constraints.

## Problem Statement
> Describe the problem to be solved in one clear sentence.

## Options Analysis

### Option A: [Name]
**Description:** Brief explanation of the approach.

| Dimension | Assessment |
|-----------|------------|
| **Pros** | List advantages |
| **Cons** | List disadvantages |
| **Cost** | Time/resource estimate |
| **Risk** | Potential risks |
| **Reversibility** | High / Medium / Low |

### Option B: [Name]
**Description:** Brief explanation of the approach.

| Dimension | Assessment |
|-----------|------------|
| **Pros** |  |
| **Cons** |  |
| **Cost** |  |
| **Risk** |  |
| **Reversibility** |  |

### Option C: [Name] (if additional options exist)

## Decision
> **We chose Option [X].**
>
> Core rationale: ...

## Impact
- **Short-term Impact**: 
- **Long-term Impact**: 
- **Components to Change**: 
- **Migration Plan**: (if applicable)

## Follow-Up Actions
| # | Action | Owner | Deadline |
|---|--------|-------|----------|
| 1 |        | @Name |          |
```

### Template 4: Knowledge Entry

```markdown
# 💡 [Knowledge Topic]

## Metadata
| Property | Value |
|----------|-------|
| **Source** | Conversation / Document / Research / Practice |
| **Capture Date** | YYYY-MM-DD |
| **Captured By** | @Name |
| **Confidence** | High / Medium / Low |
| **Tags** | #Domain, #Topic |
| **Related** | [[Related Page]] |

## Core Content
> A concise statement of this piece of knowledge.

## Detailed Explanation
Expand with background, principles, and applicable conditions.

## Evidence & Sources
- Source 1: description
- Source 2: description

## Application Scenarios
- Scenario 1: How to apply this knowledge
- Scenario 2: 

## Limitations
- Not suitable for...
- Things to note...

## Related Knowledge
- [[Related Concept 1]]
- [[Related Concept 2]]
```

---

## Notion API Integration

### Authentication & Configuration

Obtain the API Token via Notion Integration. All API operations require:

1. Create an Integration at [Notion Developers](https://developers.notion.com/)
2. Get the Internal Integration Token
3. Grant the Integration access on the target page or database (Share → Invite)

### Core API Operations

#### Create a Page

```json
{
  "parent": {
    "type": "database_id",
    "database_id": "DATABASE_ID"
  },
  "properties": {
    "Name": {
      "title": [
        { "text": { "content": "Page Title" } }
      ]
    },
    "Status": {
      "select": { "name": "In Progress" }
    },
    "Date": {
      "date": { "start": "2026-03-01" }
    },
    "Tags": {
      "multi_select": [
        { "name": "Meeting" },
        { "name": "Important" }
      ]
    }
  },
  "children": [
    {
      "object": "block",
      "type": "heading_2",
      "heading_2": {
        "rich_text": [{ "text": { "content": "Section Title" } }]
      }
    },
    {
      "object": "block",
      "type": "paragraph",
      "paragraph": {
        "rich_text": [{ "text": { "content": "..." } }]
      }
    }
  ]
}
```

#### Append Children

```json
{
  "block_id": "PAGE_OR_BLOCK_ID",
  "children": [
    {
      "object": "block",
      "type": "to_do",
      "to_do": {
        "rich_text": [{ "text": { "content": "Task description" } }],
        "checked": false
      }
    }
  ]
}
```

#### Query Database

```json
{
  "database_id": "DATABASE_ID",
  "filter": {
    "and": [
      {
        "property": "Status",
        "select": { "equals": "In Progress" }
      },
      {
        "property": "Date",
        "date": { "on_or_after": "2026-01-01" }
      }
    ]
  },
  "sorts": [
    {
      "property": "Date",
      "direction": "descending"
    }
  ]
}
```

### Notion Block Types

| Block Type | Function | API Type |
|------------|----------|----------|
| Paragraph | Plain text | `paragraph` |
| Heading 1-3 | Section titles | `heading_1` / `heading_2` / `heading_3` |
| Checklist | To-do items | `to_do` |
| Bullet List | Bulleted items | `bulleted_list_item` |
| Numbered List | Ordered items | `numbered_list_item` |
| Quote | Highlighted text | `quote` |
| Callout | Alert box | `callout` |
| Code | Code block | `code` |
| Divider | Separator line | `divider` |
| Table | Data table | `table` |
| Toggle | Expandable section | `toggle` |

---

## Knowledge Base Architecture

### Internal Linking Methods

| Link Type | Notion Syntax | Function |
|-----------|---------------|----------|
| Page Link | `[[Page Name]]` | Bidirectional link |
| Database Relation | Database Relation field | Structured association |
| Backlinks | Automatically generated | View all pages linking here |
| Synced Block | Synced Block | Share identical content across pages |

### Linking Strategies

1. **Use the "@" symbol** — Link to people and pages in meeting notes
2. **Use Relation and Rollup fields** — Connect pages across databases
3. **List pages under "Related"** — Automatically display linked content
4. **Leverage Backlinks** — View "linked to" and "linked from" pages

---

## Usage Workflow

### Meeting Notes Workflow

```
Meeting preparation → Define agenda → Capture notes → Create meeting notes page
                                                    ↓
                                              Follow up
                                                    ↓
                                       Create/Update action items
```

1. **Before the meeting**: Define the agenda (create a template or reuse the previous one)
2. **During the meeting**: Take rough notes
3. **After the meeting — Extraction**: Create and assign to-do items
4. **After the meeting — Decisions**: If there are decisions, create a decision record
5. **Cross-reference**: Link the meeting notes, action items, and decision records together

### Decision Record Workflow

1. **Identify the decision to be made** — Do not skip, record all decisions
2. **Describe the background** — Why is this decision needed?
3. **Extract key elements** — Use the extraction framework
4. **Create the Notion page** — Select the Decision Record template
5. **Analyze options** — List advantages, disadvantages, and risks for each
6. **Mark the status** — Proposed / Approved / Deprecated

### Information Extraction Workflow

- **Capture phase**: Record complete information
- **Processing phase**: Extract key elements
- **Structuring phase**: Map to Notion pages and metadata

---

## Database Design

### Required Fields

Every Notion database should include:

| Field Name | Type | Description |
|------------|------|-------------|
| **Title** | Title | Page title |
| **Created Date** | Date | When the page was created |
| **Type** | Select | Type (Meeting / Decision / How-To / Knowledge) |
| **Status** | Select | Status (Not Started / In Progress / Completed) |
| **Tags** | Multi-select | Category tags |

### Optional Fields

| Field Name | Type | Description |
|------------|------|-------------|
| **Author** | Person | The person who created the page |
| **Participants** | Person | People involved |
| **Project** | Relation | Related project |
| **Source** | URL/Text | Where the information came from |
| **Confidence** | Select | How reliable this information is |
| **Review Date** | Date | When to review this page |

---

## Notes

- **Maintain templates** — Update templates as the team evolves
- **Use @ mentions** — Assign responsibility to people in action items
- **Regular reviews** — Clean up outdated knowledge at least once a month
- **Search first, create second** — Always search the knowledge base before creating a new page to avoid duplicates
- **Manage permissions** — Notion Integration only has access to pages that have been shared with it
- **API Rate Limits** — The Notion API has rate limits (about 3 requests/second). Batch operations are recommended
- **Batch children** — Each append-children call supports a maximum of 100 blocks
- **Naming conventions** — Keep Notion page names consistent to improve searchability
