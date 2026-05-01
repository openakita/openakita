# Excel Maker

Excel Maker helps users turn CSV/XLSX files and loose business requirements into editable, auditable Excel report workbooks.

The primary output is `.xlsx`, not PPT. The plugin guides users through importing data, profiling sheet structure, clarifying reporting requirements, generating a controlled workbook plan, building a multi-sheet report, and auditing formulas and quality.

## Workflow

1. Create a report project.
2. Upload or import a `.xlsx`, `.csv`, or `.tsv` file.
3. Profile sheets, columns, formulas, missing values, metrics, and dimensions.
4. Clarify report requirements with OpenAkita Brain when available.
5. Generate a `WorkbookPlan` JSON object.
6. Build a formatted `.xlsx` workbook with README, raw data, clean data, summary, charts, formula check, and audit log sheets.
7. Audit formulas, required sheets, and output quality.
8. Download the generated workbook or refine it as a new version.

## Optional Dependencies

The plugin loads even when optional packages are missing. Install only whitelisted groups from Settings:

- `table_core`: `openpyxl`, `pandas`
- `legacy_excel`: `xlrd`, `pyxlsb`
- `charting`: `matplotlib`
- `template_tools`: reserved detect-only group

## 5 Minute Smoke Test

1. Open Excel Maker in Setup Center.
2. Create a project named `Sales Report`.
3. Upload a small CSV with one text column and one numeric column.
4. Import and profile the workbook.
5. Generate a report plan.
6. Build the report and download `report_v1.xlsx`.
7. Open the workbook and confirm the required sheets, styles, formulas, and `Audit_Log` are present.

## Limits

- This is not a full online Excel editor.
- The model never writes binary Excel files directly.
- Operation plans reject arbitrary Python, JavaScript, and SQL.
- Large sheets are sampled for preview and model context.
- Uploaded source files and templates are never overwritten.

