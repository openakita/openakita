"""Unit tests for the W3 Stage 2 report simplifier."""

from __future__ import annotations

from finance_auto_backend.renderers.simplifier import (
    DetailRow,
    SimplifyConfig,
    simplify_aux_details,
)


def _make_rows(n: int) -> list[DetailRow]:
    return [
        DetailRow(row_id=f"r{i}", name=f"客户{i:03d}", amount=float(1000 - i * 10))
        for i in range(n)
    ]


def test_simplify_disabled_returns_input_unchanged() -> None:
    rows = _make_rows(50)
    res = simplify_aux_details(rows, SimplifyConfig(enabled=False))
    assert res.merged_row is None
    assert res.kept_rows == rows
    assert res.all_source_ids == [r.row_id for r in rows]


def test_top_n_keeps_top_10_and_merges_rest() -> None:
    rows = _make_rows(50)
    cfg = SimplifyConfig(enabled=True, strategy="top_n", top_n=10)
    res = simplify_aux_details(rows, cfg)
    assert res.visible_count == 11  # 10 kept + 1 merged
    assert res.merged_row is not None
    assert res.merged_row.name == "其他"
    assert res.merged_count == 40
    # Top 10 must be the largest amounts
    assert res.kept_rows[0].amount == 1000.0
    assert res.kept_rows[9].amount == 910.0  # 1000 - 9*10
    # Source ids cover everything
    assert len(res.all_source_ids) == 50


def test_threshold_only_keeps_rows_above_threshold() -> None:
    rows = _make_rows(20)
    cfg = SimplifyConfig(enabled=True, strategy="threshold", min_threshold=900)
    res = simplify_aux_details(rows, cfg)
    # rows with amount >= 900 → 11 kept (1000, 990, ..., 900)
    visible_keep = [r for r in res.kept_rows if not r.extra.get("is_merged")]
    assert all(r.amount >= 900 for r in visible_keep if r.name.startswith("客户"))
    assert res.merged_row is not None  # 9 merged rows


def test_both_strategy_combines_threshold_and_top_n() -> None:
    rows = _make_rows(30)
    cfg = SimplifyConfig(
        enabled=True, strategy="both", top_n=5, min_threshold=850
    )
    res = simplify_aux_details(rows, cfg)
    # rows >= 850: amounts 1000..850 = 16 rows; cap at top 5
    customer_visible = [r for r in res.kept_rows if r.name.startswith("客户")]
    assert len(customer_visible) == 5
    assert res.merged_row is not None
    # merged = (16 passed threshold but missed top5) + (14 failed threshold) = 25
    assert res.merged_count == 25


def test_negative_rows_separated_from_merge() -> None:
    rows = [
        DetailRow(row_id="r1", name="A", amount=500),
        DetailRow(row_id="r2", name="B", amount=400),
        DetailRow(row_id="r3", name="C-neg", amount=-100),
    ]
    cfg = SimplifyConfig(enabled=True, strategy="top_n", top_n=1,
                         keep_negative_separate=True)
    res = simplify_aux_details(rows, cfg)
    # kept: A (top), merged: B, then negative C-neg appended verbatim
    visible_names = [r.name for r in res.kept_rows]
    assert "A" in visible_names
    assert "C-neg" in visible_names
    assert res.merged_count == 1
    assert res.merged_row is not None


def test_footnote_format() -> None:
    rows = _make_rows(20)
    cfg = SimplifyConfig(
        enabled=True, strategy="top_n", top_n=5,
        footnote_template="共 {count} 家 合计 {amount}",
    )
    res = simplify_aux_details(rows, cfg)
    assert res.footnote.startswith("共 15 家 合计")


def test_no_merging_when_rows_fit_under_top_n() -> None:
    rows = _make_rows(5)
    cfg = SimplifyConfig(enabled=True, strategy="top_n", top_n=10)
    res = simplify_aux_details(rows, cfg)
    assert res.merged_row is None
    assert res.merged_count == 0
    assert res.visible_count == 5


def test_simplify_config_from_yaml_roundtrip() -> None:
    src = {
        "enabled": True,
        "strategy": "top_n",
        "top_n": 7,
        "merge_label": "其他客户",
    }
    cfg = SimplifyConfig.from_yaml(src)
    assert cfg.top_n == 7
    assert cfg.merge_label == "其他客户"
    d = cfg.to_dict()
    assert d["enabled"] is True
    assert d["top_n"] == 7
