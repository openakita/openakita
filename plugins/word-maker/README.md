# Word Maker

Guided Word document generation for editable DOCX reports, proposals, minutes,
contracts, SOPs, and enterprise templates.

This plugin follows the same self-contained UI/runtime pattern as
`plugins/avatar-studio`: frontend assets live under `ui/dist/`, helpers are
vendored under `word_maker_inline/`, and project data is stored under
`api.get_data_dir()/word-maker/`.

## What It Does

Word Maker turns a user's goal, source files, and optional DOCX template into a
tracked document project. LLM calls are used only for requirement clarification,
outline generation, field extraction, and section rewriting. DOCX generation is
performed by deterministic Python code so the final file is real, editable, and
auditable.

## Supported Workflows

- `topic_to_doc`: generate a document from guided requirements.
- `files_to_doc`: generate from source files, Markdown, URLs, and notes.
- `template_doc`: fill an enterprise DOCX template after variable validation.
- `revise_doc`: revise an existing project or a single section.
- `brief_to_ppt`: prepare a structured summary for future PPT generation.

## Permissions

- `tools.register`: expose `word_*` tools to the Agent.
- `routes.register`: serve the UI and project APIs.
- `data.own`: keep all projects under the plugin data directory.
- `brain.access`: optional AI-assisted planning.
- `assets.publish`: optional handoff to `ppt-maker` via Asset Bus.

## Dependencies

Required:

- `python-docx`: read and write DOCX files.
- `aiosqlite`: project database.

Optional:

- `docxtpl`: full Jinja-style DOCX template rendering.
- `openpyxl`: XLSX source extraction.
- `python-pptx`: PPTX source extraction.
- `pypdf`: PDF source extraction.
- LibreOffice: future PDF export.

The UI Settings tab and `POST /deps/check` report which optional groups are
available. The plugin does not auto-install dependencies.

## Template Variables

DOCX templates may contain variables such as:

```text
{{ title }}
{{ company_name }}
{{ summary }}
```

When `docxtpl` is available, loops and conditionals are supported. Without it,
Word Maker can still render simple `{{ variable }}` placeholders.

## 5-Minute Smoke Test

1. Load the plugin and open the Word Maker UI.
2. Confirm the header shows `Loaded`.
3. In Create, enter a title and requirement, then create a project.
4. In Templates, paste the project ID and a DOCX template path, then extract variables.
5. Call `POST /projects/{id}/render` or the `word_fill_template` tool and verify `document.docx` appears under the project `exports/` folder.

## Troubleshooting

- If Word files cannot be read, check `python-docx`.
- If template loops fail, install `docxtpl`.
- If the Agent says generation is complete but no file exists, treat it as a bug: generated documents must return an output path.
- If Brain is unavailable, manual project creation and template filling still work.


