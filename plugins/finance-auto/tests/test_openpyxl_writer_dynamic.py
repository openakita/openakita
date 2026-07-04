"""Tests for the M2 Biz Stage 5 ``OpenpyxlDirectWriter.write_detail_rows``.

Coverage:

* default styles → header / row / total render cleanly
* custom styles → font + fill + border applied
* 50 / 500 / 1500 row scale benchmark (must finish < 5 s each)
* simplifier integration: long supplier list collapses to top-N + 其他
* second write into same workbook creates / reuses sheet correctly
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from finance_auto_backend.renderers.openpyxl_writer import (
    DetailStyle,
    OpenpyxlDirectWriter,
)
from finance_auto_backend.renderers.simplifier import SimplifyConfig


COLUMNS = [
    {"key": "vendor", "label": "供应商",   "width": 28},
    {"key": "code",   "label": "代码",     "width": 12},
    {"key": "amount", "label": "期末余额", "width": 18, "numeric": True},
]


def _rows(n: int) -> list[dict]:
    return [
        {"vendor": f"供应商-{i:04d}", "code": f"V{i:05d}", "amount": float(1000 + i)}
        for i in range(1, n + 1)
    ]


def test_write_default_styles(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    writer = OpenpyxlDirectWriter()
    res = writer.write_detail_rows(
        workbook=wb, sheet_name="应付明细",
        columns=COLUMNS, rows=_rows(10),
        total_label="合计", total_columns=["amount"],
    )
    out = tmp_path / "default.xlsx"
    wb.save(out)
    assert out.exists()
    assert res.row_count == 11  # 10 data + total
    assert res.elapsed_ms < 5000

    wb2 = openpyxl.load_workbook(out)
    ws = wb2["应付明细"]
    # Header row.
    assert ws.cell(1, 1).value == "供应商"
    assert ws.cell(1, 1).font.bold is True
    # First data row.
    assert ws.cell(2, 1).value == "供应商-0001"
    assert ws.cell(2, 3).value == 1001.0
    # Total row uses SUM formula.
    total_row = 12
    assert ws.cell(total_row, 1).value == "合计"
    assert str(ws.cell(total_row, 3).value).startswith("=SUM(C2:C11")


def test_write_custom_header_fill(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    writer = OpenpyxlDirectWriter()
    res = writer.write_detail_rows(
        workbook=wb, sheet_name="custom",
        columns=COLUMNS, rows=_rows(5),
        header_style=DetailStyle(
            bold=True, italic=False, fill_color="FFFFCC00",
            horizontal="center",
        ),
        row_style=DetailStyle(number_format="0.00"),
        total_label="TOTAL", total_columns=["amount"],
    )
    assert res.row_count == 6
    cell = wb["custom"].cell(1, 1)
    assert cell.fill.start_color.rgb in ("FFFFCC00", "00FFCC00")


@pytest.mark.parametrize("n", [50, 500, 1500])
def test_scale_benchmark_under_5_seconds(n: int, tmp_path: Path) -> None:
    """Each of 50 / 500 / 1500 row writes must finish in <5 s (per spec)."""
    wb = openpyxl.Workbook()
    writer = OpenpyxlDirectWriter()
    started = time.perf_counter()
    res = writer.write_detail_rows(
        workbook=wb, sheet_name=f"scale_{n}",
        columns=COLUMNS, rows=_rows(n),
        total_label="合计", total_columns=["amount"],
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"{n}-row write took {elapsed:.2f}s; spec limit 5s"
    assert res.row_count == n + 1
    out = tmp_path / f"scale_{n}.xlsx"
    wb.save(out)
    assert out.exists()


def test_simplifier_integration(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    writer = OpenpyxlDirectWriter()
    res = writer.write_detail_rows(
        workbook=wb, sheet_name="simplified",
        columns=COLUMNS,
        rows=_rows(50),
        total_label="合计", total_columns=["amount"],
        simplify_config=SimplifyConfig(enabled=True, strategy="top_n", top_n=5),
    )
    assert res.simplify_applied is True
    assert res.grouped_into is not None
    # top 5 + 其他 = 6 rows + 1 total = 7
    assert res.row_count == 7


def test_excess_rows_truncated(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    writer = OpenpyxlDirectWriter()
    res = writer.write_detail_rows(
        workbook=wb, sheet_name="too_many",
        columns=COLUMNS, rows=_rows(2000),
    )
    assert res.row_count == 1500  # MAX_ROWS cap
    assert any("exceeds MAX_ROWS" in w for w in res.warnings)


def test_multiple_sheets_in_same_workbook(tmp_path: Path) -> None:
    wb = openpyxl.Workbook()
    writer = OpenpyxlDirectWriter()
    writer.write_detail_rows(
        workbook=wb, sheet_name="货币资金",
        columns=COLUMNS, rows=_rows(8),
        total_label="合计", total_columns=["amount"],
    )
    writer.write_detail_rows(
        workbook=wb, sheet_name="应收账款",
        columns=COLUMNS, rows=_rows(12),
        total_label="合计", total_columns=["amount"],
    )
    assert "货币资金" in wb.sheetnames
    assert "应收账款" in wb.sheetnames
    out = tmp_path / "multi.xlsx"
    wb.save(out)
    wb2 = openpyxl.load_workbook(out)
    assert wb2["货币资金"].cell(2, 1).value == "供应商-0001"
    assert wb2["应收账款"].cell(2, 1).value == "供应商-0001"


def test_existing_renderer_still_works() -> None:
    """Sanity: the original OpenpyxlDirectRenderer is still importable + the
    new writer added beside it didn't break the dunder __all__ list."""
    from finance_auto_backend.renderers import openpyxl_writer as mod
    assert hasattr(mod, "OpenpyxlDirectRenderer")
    assert hasattr(mod, "OpenpyxlDirectWriter")
    assert hasattr(mod, "DetailStyle")
    assert hasattr(mod, "STATIC_ROW_THRESHOLD")
