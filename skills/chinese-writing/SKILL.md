---
name: openakita/skills@chinese-writing
description: Enforce modern Chinese writing standards including tone, spacing rules (Pangu), full-width punctuation, paragraph structure, and active voice. Provides specific guidelines for blog posts, error messages, UI text, and technical documentation.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# Chinese Writing Standards

A systematic Chinese writing skill that ensures all Chinese output follows unified language style, formatting rules, and expression conventions. Suitable for technical documentation, blog posts, UI text, error messages, and various other writing scenarios.

## When to Use

- Writing Chinese technical documentation and README files
- Crafting product UI copy (buttons, prompts, descriptions)
- Writing blog posts and tutorials
- Generating error messages and exception messages
- Translating English content into standardized Chinese
- Proofreading and editing existing Chinese text
- Writing product changelogs
- Drafting emails and notification copy

## Core Tone

All Chinese output should follow these three tone characteristics:

| Characteristic | Description | Good Example | Bad Example |
|------|------|---------|---------|
| **Helpful** | Provides practical information, solves user problems | Click "Settings" to modify defaults | Please check the documentation |
| **Clear** | Accurate and unambiguous, logically smooth | File size must not exceed 10 MB | The file should not be too big |
| **Friendly** | Warm but not overly familiar | Operation completed. Continue? | Congratulations! You are amazing!!! |

**Prohibited tones:**
- Overly enthusiastic (stacking exclamation marks, abusing emoji)
- Cold and stiff
- Vague and general (e.g. "in some cases", "might possibly")
- Overly humble (e.g. "sorry to bother you")

## Pangu's Whitespace: Chinese-English Spacing Rules

A space must be inserted between Chinese characters (CJK) and half-width characters (ASCII letters, numbers).

### Spacing Rules Table

| Rule | Correct | Incorrect |
|------|--------|--------|
| Chinese and English | [space between] | [no space] |
| Chinese and numbers | [space between] | [no space] |
| Numbers and units | capacity of 10 GB | capacity of 10GB |
| Numbers and percent | increased by 30% | increased by 30 % |
| Full-width punct and English | [use full-width] | [half-width after English] |
| Internal English punct | e.g. Mr. Smith | e.g. Mr .Smith |
| Around links | See https://example.com page | [no space around URL] |
| Around paths | Located in /usr/local directory | [no space around path] |
| Around code | Execute `npm install` command | [no space around code] |
| Brand names | Download VS Code editor | [no space around branding] |

### Cases Where No Space Is Needed

| Case | Rule |
|------|------|
| Around full-width punctuation | No extra space around Chinese punctuation |
| Numbers and degree symbols | 45 degrees symbol -- no space |
| Currency symbols before numbers | Yen dollar sign directly before number |
| Inside full-width parentheses | No spaces inside full-width brackets |

### Auto-Check Points

After generating Chinese text, self-check:
1. Chinese char + letter: needs space between
2. Letter + Chinese char: needs space between
3. Chinese char + digit: needs space between
4. Digit + Chinese char: needs space between
5. Chinese char before full-width punctuation: no space

## Punctuation Standards

### Full-Width vs Half-Width

Use full-width punctuation in Chinese contexts and half-width in English or code contexts.

| Punctuation | Full-Width (Chinese) | Half-Width (English) | Rule |
|------|------------|------------|---------|
| Comma | CJK comma | Comma | Use full-width in Chinese |
| Period | CJK period | Dot | Use full-width at end of Chinese sentences |
| Colon | CJK colon | Colon | Use full-width in Chinese |
| Semicolon | CJK semicolon | Semicolon | Use full-width in Chinese |
| Exclamation | CJK exclamation | Exclamation | Full-width in Chinese; do not repeat |
| Question | CJK question mark | Question mark | Full-width in Chinese |
| Quotation marks | Corner brackets | Straight quotes | Prefer corner brackets for Chinese |
| Parentheses | Full-width | Half-width | Full-width for Chinese text; half-width for code |
| Ellipsis | Double full-width ellipsis | Triple dot | Use two CJK ellipsis characters |
| Em dash | Double CJK dash | Double hyphen | Use two CJK dash characters |
| Title marks | Chinese title brackets | N/A | Chinese-specific |

### Quotation Mark Usage

Prefer corner brackets. Alternate when nesting:
- First level: corner brackets
- Second level: double corner brackets inside
- Third level: standard quotes inside double brackets

**Special scenarios:**
- UI element names use corner brackets: click the "Confirm" button
- File names and paths use backticks
- Emphatic words can use bold

### Punctuation Prohibitions

| Prohibited | Description |
|------|------|
| Triple exclamation marks | Do not repeat exclamation marks |
| Triple periods | Use ellipsis, not stacked periods |
| Tilde in formal text | Avoid tilde in formal writing |
| Half-width comma in Chinese | Use full-width comma in Chinese sentences |
| Dot ending Chinese sentences | Use full-width period, not half-width |

## Paragraph Structure Standards

### Paragraph Length

- Each paragraph: **3-5 lines** (approximately 100-200 characters)
- Split paragraphs exceeding 5 lines
- Single-sentence paragraphs only for emphasis

### Organization Principles

1. **One theme per paragraph**: Each paragraph focuses on one core idea
2. **Opening summary**: First sentence states the main point
3. **Logical progression**: Clear logic between paragraphs
4. **Natural transitions**: Use connectives to link paragraphs

**Common transitional words:**

| Relationship | Transitional Words |
|------|--------|
| Progressive | Additionally, moreover, more importantly |
| Contrast | However, but, nevertheless |
| Cause-effect | Therefore, as a result, based on this |
| Parallel | Meanwhile, on one hand... on the other |
| Summary | In conclusion, in short |
| Examples | For example, for instance, take X as an example |

### List Usage Standards

- Use lists for 3 or more parallel items
- Ordered lists for steps or priorities
- Unordered lists for parallel items
- No period at end of list items unless they are full sentences
- List items maintain consistent structure (all verbs or all nouns)

## Active Voice Priority

Prefer active voice. Passive voice only when subject is unimportant or unknown.

| Passive (Avoid) | Active (Recommended) |
|---------------|---------------|
| File was successfully uploaded | File uploaded successfully |
| Config was saved to disk | System has saved the configuration |
| Error was detected | Error detected |
| Password was modified | Password changed successfully |
| This feature was designed for... | This feature is used for... |

## Scenario-Based Writing Guide

### 1. Blog Posts

- Friendly but casual tone
- First person is acceptable
- Appropriate colloquial expressions for readability
- Technical terms kept in English

**Structure:** Title -> Intro (why write this) -> Main sections (3-5 with subheadings) -> Conclusion -> Further reading

### 2. Error Messages

**Core principle:** Tell the user what happened, why, and how to fix it.

**Format:** `[What happened]. [Why (optional)]. [Suggested fix].`

**Examples:**
| Bad | Good |
|------|------|
| "Error: Operation failed" | "Save failed: File exceeds 10 MB limit. Compress and retry." |
| "Network error" | "Cannot connect to server. Check your network and retry." |
| "Invalid input" | "Username supports letters, numbers, and underscores. 3-20 chars." |
| "Insufficient permissions" | "You do not have edit access. Contact admin for Editor role." |
| "Unknown error" | "Something went wrong. Please retry later. Contact support if it persists." |

**Taboos:**
- No technical jargon (e.g. NullPointerException)
- No all caps (ERROR)
- No exclamation marks
- No negative denial (use "temporarily unavailable" vs "cannot")
- Do not blame the user ("your operation is wrong" -> "input format does not match")

### 3. UI Copy

**Core principle:** Short, clear, actionable.

| Scenario | Recommended | Avoid |
|------|--------|--------|
| Confirm | Confirm / Save | Yes / OK |
| Cancel | Cancel | No / Whatever |
| Delete | Delete | Remove / Clear (unless semantically different) |
| Submit | Submit / Publish | "Click to submit" |
| Next step | Next / Continue | Next / GO (in English) |

**Prompt templates:**

| Type | Template | Example |
|------|------|------|
| Success | [Action] successful | Save successful |
| Loading | [Action] in progress | Loading |
| Confirmation | Are you sure you want to [action]? [Consequence] | Delete? Cannot be undone. |
| Empty state | No [content] yet. [Guide action] | No projects. Tap upper right to create. |
| Placeholder | Please enter [content] | Please enter email address |

### 4. Technical Documentation

- Third person or subjectless sentences
- Objective, neutral tone
- Technical terms: provide translation on first use
- Wrap code and commands in backticks

## Numbers and Units Standards

| Rule | Correct | Incorrect |
|------|--------|--------|
| Arabic numerals for data | 128 records | [Chinese numerals for large numbers] |
| Chinese numerals for idioms | [Fixed idioms use Chinese numerals] | [Arabic in idioms] |
| Number ranges | 3-5 business days | [tilde-based ranges] |
| Date format | Year Month Day (with spaces) | [no spaces] |
| Time format | 24h or afternoon notation | [verbose Chinese time] |
| Thousands separator | 1,000,000 | 1000000 |

## Common Error Self-Check List

- [ ] Space between Chinese and English (Pangu spacing)
- [ ] Space between Chinese and numbers
- [ ] Full-width punctuation used in Chinese context
- [ ] Quotation marks use corner brackets
- [ ] No consecutive exclamation or question marks
- [ ] Paragraphs within 3-5 lines
- [ ] Active voice used
- [ ] List items structurally consistent
- [ ] Error prompts include solutions
- [ ] Technical terms have annotations on first use

## Output Example

### Input
```
Write an error message for upload exceeding file size limit.
```

### Output
```
Upload failed: File is 25 MB, exceeding the 10 MB limit.

Please compress or split into smaller files and retry. Supported formats: JPG, PNG, PDF.
```

### Analysis
- Pangu spacing: 25 MB, 10 MB
- Full-width punctuation used
- Active voice
- Includes phenomenon, reason, and solution
- Specific values shown
