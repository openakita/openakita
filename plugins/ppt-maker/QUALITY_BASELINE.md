# PPT Maker Quality Baseline

This baseline records the first upgrade target for the ppt-maker rebuild. It is
intentionally small and deterministic so future changes can be compared against
the same scenarios.

## Fixed Scenarios

1. `topic_tech_roadmap`: topic-to-deck, 8 pages, tech business style.
2. `consulting_strategy_report`: topic-to-deck, 10 pages, consulting style.
3. `table_sales_report`: table-to-deck from CSV/XLSX, data insight style.
4. `product_launch_pitch`: topic-to-deck, 8 pages, creative pitch style.
5. `files_to_deck_briefing`: files-to-deck from Markdown/PDF/DOCX material.

## Current Known Issues

- Layout variety is limited by a small set of slide types.
- Design tokens mostly cover colors and fonts, not spacing, density, rhythm, or
  visual hierarchy.
- `slides_ir.json` is too thin to express component trees, region priority, and
  overflow rules.
- The Python fallback exporter uses fixed coordinates, which can make long text,
  wide tables, and mixed media pages feel crowded.
- Audit checks are useful but too shallow; they need scoring and repair hints.

## Target Quality Signals

- Every deck has a stable `brief.json`, `context_pack.json`, `story_plan.json`,
  `design_system.json`, `slide_specs.json`, `render_model.json`, and
  `slides_ir.json`.
- Slide titles should be meaningful and preferably conclusion-oriented.
- Dense slides should be flagged before export.
- Chart slides should use real `categories` and `series` when data is available.
- The default output remains editable PPTX; high-visual creative output must be
  an explicit mode.

