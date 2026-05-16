---
name: excel-maker
description: Create, organize, improve, audit, and export Excel report workbooks from CSV or XLSX data.
risk_class: mutating_scoped
---

# Excel Maker Skill

Use this skill when the user wants to create, organize, improve, audit, or export an Excel report workbook from CSV/XLSX data.

## Primary Goal

The deliverable is an editable `.xlsx` report workbook. Do not steer the workflow toward PPT unless the user explicitly asks for a future export option.

## Tool Order

1. `excel_start_project`
2. `excel_import_workbook`
3. `excel_profile_workbook`
4. `excel_clarify_requirements`
5. `excel_generate_report_plan`
6. Ask the user to confirm the workbook structure and key formulas.
7. `excel_apply_operations`
8. `excel_build_workbook`
9. `excel_audit_workbook`
10. `excel_export_workbook`

## Rules

- Never ask the model to directly edit binary Excel content.
- Never execute arbitrary code from a model response.
- Keep source files and uploaded templates unchanged.
- Generate new workbook versions instead of overwriting previous outputs.
- Explain formulas in plain language and include assumptions.
- For large datasets, use profile summaries and samples rather than full table content.
- If optional dependencies are missing, tell the user which whitelisted dependency group is needed.

## WorkbookPlan Shape

Return a JSON object with:

- `title`
- `purpose`
- `source_workbook_id`
- `sheets`
- `operations`
- `formulas`
- `style`
- `audit_expectations`

Every formula string must start with `=`.

