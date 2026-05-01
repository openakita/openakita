# PPT Maker

Guided presentation generation for editable PPTX decks, table-driven reports,
and enterprise templates.

`ppt-maker` is a self-contained OpenAkita UI plugin. It uses Akita Brain for
structured reasoning when `brain.access` is granted, stores project artifacts in
`api.get_data_dir()/ppt-maker/`, and exports editable PowerPoint files through
`python-pptx`.

## Modes

- `topic_to_deck`: create a deck from a topic and guided requirements.
- `files_to_deck`: create a deck from PDF/DOCX/Markdown/PPTX/URL/text sources.
- `outline_to_deck`: turn an existing outline into a designed deck.
- `table_to_deck`: generate profile, insights, chart specs, and data slides from CSV/XLSX.
- `template_deck`: diagnose enterprise PPTX templates and apply brand tokens/layout fallback.
- `revise_deck`: revise a project or one slide through the slide update route/tool.

## Core Flow

1. Create a project.
2. Add sources, datasets, or templates.
3. Generate and confirm `outline.json`.
4. Generate and confirm `design_spec.md` and `spec_lock.json`.
5. Generate `slides_ir.json`.
6. Export editable `.pptx`.
7. Review `audit_report.json`.

## Data Layout

```text
{data_dir}/ppt-maker/
├── ppt_maker.db
├── uploads/
├── datasets/{dataset_id}/profile.json
├── templates/{template_id}/brand_tokens.json
├── projects/{project_id}/outline.json
├── projects/{project_id}/design_spec.md
├── projects/{project_id}/spec_lock.json
├── projects/{project_id}/slides_ir.json
├── projects/{project_id}/audit_report.json
└── projects/{project_id}/exports/{project_id}.pptx
```

## Optional Dependencies

Settings exposes a whitelist-only dependency panel:

- `doc_parsing`: `python-docx`, `pypdf`, `beautifulsoup4`
- `table_processing`: `openpyxl`
- `chart_rendering`: `matplotlib`
- `advanced_export`: `python-pptx`
- `marp_bridge`: detect-only placeholder for future Marp/.NET integration

## Five-Minute Smoke Test

1. Open the plugin UI and run Settings health check.
2. Create a `topic_to_deck` project: “OpenAkita 插件生态路线图，8 页，科技商务风”.
3. Generate the deck and verify `outline/design/slides_ir/audit` are created.
4. Create a `table_to_deck` project from a CSV and verify profile/insights/chart specs.
5. Upload a PPTX template and verify brand tokens/layout map diagnostics.
6. Open the exported PPTX in PowerPoint and confirm text is editable.

## Troubleshooting

- Missing PDF/DOCX/XLSX parsing means the corresponding optional dependency group is not installed.
- Enterprise template diagnostics are best-effort. Complex animations, SmartArt, and all master details are not 1:1 copied in MVP.
- If export fails, check `audit_report.json`, `logs/`, and whether `python-pptx` is installed.

