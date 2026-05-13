# PPT Maker

Guided presentation generation for editable PPTX decks, table-driven reports,
and enterprise templates.

`ppt-maker` is a self-contained OpenAkita UI plugin. It uses Akita Brain for
structured reasoning when `brain.access` is granted, stores project artifacts in
`api.get_data_dir()/ppt-maker/`, and exports editable PowerPoint files through
`python-pptx` by default. A generation-v2 layer now writes explicit planning,
design, slide, and render artifacts so the deck can be audited and repaired in
smaller steps.

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
5. Generate `slides_ir.json` plus generation-v2 artifacts.
6. Run audit and a deterministic repair pass.
7. Export editable `.pptx` or explicit creative image-mode `.pptx`.
8. Review `audit_report.json` and `repair_plan.json`.

Generation-v2 artifacts:

- `brief.json`: normalized request, audience, style, output mode, and quality mode.
- `context_pack.json`: compact facts, source summaries, chart specs, and caveats.
- `story_plan.json`: slide-by-slide narrative plan.
- `design_system.json`: colors, fonts, spacing, density rules, and visual style.
- `slide_specs.json`: layout id, content, assets, and repair hints per slide.
- `render_model.json`: component-tree contract for advanced renderers.

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
├── projects/{project_id}/brief.json
├── projects/{project_id}/context_pack.json
├── projects/{project_id}/story_plan.json
├── projects/{project_id}/design_system.json
├── projects/{project_id}/slide_specs.json
├── projects/{project_id}/render_model.json
├── projects/{project_id}/slides_ir.json
├── projects/{project_id}/audit_report.json
├── projects/{project_id}/repair_plan.json
└── projects/{project_id}/exports/{project_id}.pptx
```

## Generation Settings

- `quality_mode`: `draft`, `standard`, or `deep_design`. This controls density
  and design-system defaults.
- `output_mode`: `editable` or `creative_image`. Editable mode keeps text,
  shapes, tables, and charts as PowerPoint objects when possible. Creative image
  mode renders each slide as a full-page PNG before embedding it into PPTX, so
  it looks more poster-like but is less editable.
- `exporter`: `python-pptx` or `pptxgenjs`. `pptxgenjs` is optional and falls
  back to `python-pptx` when Node dependencies are not installed.

## Optional Dependencies

Settings exposes a whitelist-only dependency panel:

- `doc_parsing`: `python-docx`, `pypdf`, `beautifulsoup4`
- `table_processing`: `openpyxl`
- `chart_rendering`: `matplotlib`
- `advanced_export`: `python-pptx`
- `marp_bridge`: detect-only placeholder for future Marp/.NET integration

Optional PptxGenJS renderer:

```bash
cd plugins/ppt-maker/renderers/pptxgenjs
npm install
```

Then set `exporter=pptxgenjs` in the plugin settings. If Node or dependencies
are unavailable, the plugin automatically uses the Python fallback exporter.

## Five-Minute Smoke Test

1. Open the plugin UI and run Settings health check.
2. Create a `topic_to_deck` project: “OpenAkita 插件生态路线图，8 页，科技商务风”.
3. Generate the deck and verify `outline/design/slides_ir/audit` plus
   `brief/context_pack/story_plan/design_system/slide_specs/render_model` are created.
4. Create a `table_to_deck` project from a CSV and verify profile/insights/chart specs.
5. Upload a PPTX template and verify brand tokens/layout map diagnostics.
6. Open the exported PPTX in PowerPoint and confirm text is editable.
7. Switch `output_mode` to `creative_image` and verify the result is clearly
   marked as visual-first, editability-limited output.

## Troubleshooting

- Missing PDF/DOCX/XLSX parsing means the corresponding optional dependency group is not installed.
- Enterprise template diagnostics are best-effort. Complex animations, SmartArt, and all master details are not 1:1 copied in MVP.
- If export fails, check `audit_report.json`, `repair_plan.json`, `logs/`, and whether `python-pptx` is installed.
- If `pptxgenjs` output falls back, install Node dependencies in
  `renderers/pptxgenjs/` or switch `exporter` back to `python-pptx`.

