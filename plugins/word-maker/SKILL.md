---
name: word-maker
description: Guided Word document generation for editable DOCX reports, proposals, minutes, contracts, SOPs, and enterprise templates.
---

# word-maker Skill Card

Use this plugin when the user asks to create, revise, audit, or export a Word
document. Prefer it over ad-hoc DOCX scripts when the task needs guided
requirements, source files, template variables, section iteration, or a
downloadable editable DOCX.

## Trigger Scenarios

- Weekly, monthly, or daily reports.
- Meeting minutes.
- Project proposals and acceptance reports.
- Contract or agreement drafts.
- SOPs, policies, and internal process documents.
- Preparing a structured brief before making a PPT.

## Tool Order

1. `word_start_project`
2. `word_ingest_sources` when files or notes are provided
3. `word_upload_template` and `word_extract_template_vars` when a template is used
4. `word_generate_outline`
5. `word_confirm_outline`
6. `word_fill_template`
7. `word_audit`
8. `word_export`

Do not claim a document is generated unless `word_export` returns a real output
path. Ask the user to confirm missing template fields instead of silently
leaving them blank.

## Brain Usage

Use Brain for lightweight sub-tasks only:

- requirement clarification;
- outline generation;
- template field extraction;
- section rewriting;
- summary for future PPT generation.

If `brain.access` is unavailable, continue with manual fields and template
rendering.

## Output Schema

Every tool response should include enough state for the user to continue:

- `project_id`;
- `status`;
- `next_action` when a user decision is needed;
- `output_path` when a file exists;
- `error` and `missing` when generation is blocked.

## Error Policy

Never return a vague "generation failed" message. Prefer specific categories:

- `missing_template_vars`;
- `source_parse_failed`;
- `template_render_failed`;
- `brain_unavailable`;
- `dependency_missing`;
- `audit_failed`.

## PPT Handoff

When the user asks to turn the document into a PPT, export first and then call
`word_export` with `publish_for_ppt=true`. This publishes a
`word_document_brief` asset for `ppt-maker` when `assets.publish` is granted.

## Testing

Run:

```bash
py -3.11 -m pytest plugins/word-maker/tests -q
py -3.11 -m ruff check plugins/word-maker --ignore N999
```

The `N999` ignore is needed because OpenAkita plugin IDs use hyphenated
directories such as `word-maker`.

