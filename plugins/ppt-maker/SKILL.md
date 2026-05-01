---
name: ppt-maker
description: Guided PPT generation for editable PowerPoint decks, table-driven reports, and enterprise-template presentations.
---

# ppt-maker Skill Card

Use this plugin when the user asks to create, revise, audit, or export a PPT
deck. Prefer it over ad-hoc slide scripts when the task needs guided
requirements, source files, table insights, enterprise templates, or a
downloadable editable PPTX.

## Tool Order

1. `ppt_start_project`
2. `ppt_ingest_sources` or `ppt_ingest_table` when files/data are provided
3. `ppt_generate_outline`
4. `ppt_confirm_outline`
5. `ppt_generate_design`
6. `ppt_confirm_design`
7. `ppt_generate_deck`
8. `ppt_audit`
9. `ppt_export`

For enterprise templates, call `ppt_upload_template` and
`ppt_diagnose_template` before generating the design spec.

## Mode Decision

- Use `topic_to_deck` when the user gives only a topic or goal.
- Use `files_to_deck` when source files or URLs are the primary material.
- Use `outline_to_deck` when the user already has an outline.
- Use `table_to_deck` when CSV/XLSX/table data is central to the request.
- Use `template_deck` when a corporate PPTX template or brand guideline is provided.
- Use `revise_deck` when the user asks to rewrite a slide, change page count, or adjust style.

## Important Gates

- Always confirm the outline before generating the final deck.
- Always confirm the design/spec_lock before export.
- For table decks, ensure profile, insights, and chart specs exist.
- For template decks, ensure template profile, brand tokens, and layout map exist.

## Error Codes

- `validation`: input or schema issue.
- `dependency`: optional dependency missing.
- `brain`: Akita Brain unavailable or failed.
- `source_parse`: source document parsing failed.
- `table_parse`: table parsing/profiling failed.
- `template`: PPTX template diagnostics failed.
- `export`: PPTX export failed.
- `audit`: quality check failed.
- `cancelled`: user cancelled the task.
- `unknown`: inspect project logs.

