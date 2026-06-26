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

`GET /settings` and `POST /deps/check` also return `dependency_report`, a
structured diagnostic payload grouped by host, core, template, source readers,
PPT handoff, and test runtime. Each entry includes the module name, package
name, whether it is installed, whether it is required, its purpose, and the
impact when missing. This gives the UI enough detail to show actionable
dependency feedback without running package installation from the plugin.

The same responses include `brain_status`, which separates permission issues
from host LLM availability: `permission_denied` means the plugin lacks
`brain.access`, while `host_brain_unavailable` means OpenAkita has not injected
a Brain client, usually because the main LLM endpoint is not configured. Brain
calls use the host-provided client only, preferring `think_lightweight`, then
`think`, then `compiler_think`, and fall back to deterministic behavior when
the response is empty or cannot be parsed as the expected JSON shape.

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

1. Load the plugin and open the Word Maker UI (default **向导** tab).
2. Step 1: choose a doc type, enter title/requirements, optionally click **AI 澄清需求**, then **创建并继续** (or **加载示例数据** first).
3. Step 2: upload source files and an optional DOCX template (variables are detected automatically).
4. Step 3: entering this step auto-builds an **outline JSON from Step 2 materials** (Brain or rule-based fallback). Edit the JSON or cards, optionally **AI 预填字段** for templates, then **确认并生成**.
5. Step 4: wait for success and download `document.docx` (no template: outline becomes Word sections; with template: outline syncs into fields like `summary` then fills the DOCX).

Advanced JSON editing and legacy template tools remain under the **高级** tab.

## Troubleshooting

- If Word files cannot be read, check `python-docx`.
- If template loops fail, install `docxtpl`.
- If the Agent says generation is complete but no file exists, treat it as a bug: generated documents must return an output path.
- If Brain is unavailable, check plugin `brain.access` permission first, then
  the OpenAkita LLM endpoint configuration. Manual project creation and template
  filling still work without Brain.
