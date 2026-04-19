---
name: skill-creator
description: Guide users through creating new skills with SKILL.md format. When the user wants to create a custom skill, extend agent capabilities, or define reusable workflows.
system: true
handler: skills
tool-name: skill_creator
category: Skills Management
---

# Skill Creator

This skill guides you through creating new skills that extend agent capabilities with specialized knowledge, workflows, and tools.

## When to Use This Skill

Use this skill when the user:

- Wants to create a custom skill for a repetitive task
- Needs to define a specialized workflow or capability
- Wants to share knowledge or templates across sessions
- Mentions creating a "skill", "capability", or "extension"

## What is a Skill?

A skill is a self-contained markdown file (`SKILL.md`) that tells an agent how to perform a specific task. Skills have:

- **YAML frontmatter** — metadata like name, description, requirements
- **Instructions** — step-by-step guidance for the agent
- **Optional scripts** — pre-built scripts for automation

## Skill Structure

```
skills/
  my-custom-skill/
    SKILL.md          # Required — instructions and metadata
    scripts/          # Optional — automation scripts
      helper.py
    references/       # Optional — reference documentation
      api-docs.md
```

## SKILL.md Template

```yaml
---
name: my-custom-skill
description: Brief description of what this skill does and when to use it
license: MIT
requires:
  env: [MY_API_KEY]
---
```

```markdown
# Skill Title

Brief overview of the skill's purpose.

## When to Use

- Condition 1
- Condition 2

## Step-by-Step Instructions

### Step 1: ...

### Step 2: ...

## Examples

Show concrete usage examples.

## Notes

Important caveats, limitations, or best practices.
```

## Creation Process

### 1. Understand the Goal

Ask the user:
- What task or workflow should the skill automate?
- What inputs does it need? What outputs does it produce?
- Are there any external tools or APIs required?

### 2. Design the Structure

Plan the skill's sections:
- Identify the key steps in the workflow
- Determine what parameters are needed
- Decide if scripts or references are required

### 3. Write the SKILL.md

Fill in the template with clear, actionable instructions. Use:
- Imperative language ("do X", "run Y")
- Concrete examples with expected inputs/outputs
- Error handling guidance

### 4. Validate

- Check YAML frontmatter syntax
- Ensure all required fields are present
- Verify the instructions are clear and complete

### 5. Save and Load

Save to `skills/{skill-name}/SKILL.md` and reload skills to make it available.

## Best Practices

- Keep instructions concise but complete
- Use markdown tables for parameter definitions
- Include error recovery steps
- Write examples that users can copy-paste
- Use relative paths for internal references
