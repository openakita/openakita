# Contributing

## Skill Structure

Each skill is a directory with a `SKILL.md` file:

```
skills/
└── my-skill/
    ├── SKILL.md           # Required: main skill file
    └── references/        # Optional: detailed docs
        └── api.md
```

## SKILL.md Format

```yaml
---
name: my-skill
description: |
  What this skill does and when to use it.
  Include relevant keywords so agents know when to activate.
allowed-tools: Bash(infsh *)
---

# My Skill

Content, examples, and commands here.
```

## Adding New Skills

1. Create `skills/<skill-name>/SKILL.md`
2. Write a clear description of when to use the skill
3. Add working examples with real app IDs from `infsh app list`
4. Cross-reference related skills with `npx skills add` commands
5. Test with `infsh app run` to verify examples work

## Cross-Referencing

Every skill should reference related skills:

```markdown
## Related Skills

\`\`\`bash
npx skills add inference-sh/skills@inference-sh
npx skills add inference-sh/skills@related-skill
\`\`\`
```

## Updating App Lists

Apps change over time. To refresh:

```bash
infsh app list                    # All apps
infsh app list --category image   # By category
infsh app list --search "flux"    # Search
```
