"""Excel formula helpers."""

from __future__ import annotations

from excel_models import FormulaSuggestion


def generate_formula(kind: str, *, range_ref: str, criteria_ref: str = "", criteria: str = "") -> FormulaSuggestion:
    kind_normalized = kind.strip().lower()
    if kind_normalized == "sumifs":
        formula = f'=SUMIFS({range_ref},{criteria_ref},"{criteria}")' if criteria_ref else f"=SUM({range_ref})"
        explanation = "按条件汇总数值；未提供条件区域时退化为 SUM 汇总。"
    elif kind_normalized == "countifs":
        formula = f'=COUNTIFS({criteria_ref},"{criteria}")' if criteria_ref else f"=COUNT({range_ref})"
        explanation = "按条件统计记录数；未提供条件区域时统计数字单元格。"
    elif kind_normalized == "averageifs":
        formula = (
            f'=AVERAGEIFS({range_ref},{criteria_ref},"{criteria}")' if criteria_ref else f"=AVERAGE({range_ref})"
        )
        explanation = "按条件计算平均值；未提供条件区域时计算整体平均值。"
    elif kind_normalized in {"yoy", "同比"}:
        formula = f'=IFERROR(({range_ref}-{criteria_ref})/{criteria_ref},0)'
        explanation = "同比或变化率公式，使用 IFERROR 避免除零报错。"
    else:
        formula = f"=SUM({range_ref})"
        explanation = "默认生成 SUM 汇总公式。"
    return FormulaSuggestion(
        formula=formula,
        explanation=explanation,
        applies_to=range_ref,
        test_example={"kind": kind, "range_ref": range_ref, "criteria_ref": criteria_ref, "criteria": criteria},
    )


def explain_formula(formula: str) -> str:
    value = formula.strip().upper()
    if value.startswith("=SUMIFS"):
        return "SUMIFS 会按一个或多个条件筛选数据，然后汇总目标区域。"
    if value.startswith("=COUNTIFS"):
        return "COUNTIFS 会按一个或多个条件统计符合条件的记录数量。"
    if value.startswith("=AVERAGEIFS"):
        return "AVERAGEIFS 会按条件计算目标区域平均值。"
    if value.startswith("=XLOOKUP") or value.startswith("=VLOOKUP"):
        return "查找公式会按键值从映射表中取回对应字段。"
    if value.startswith("=IFERROR"):
        return "IFERROR 会在内部公式报错时返回指定默认值，常用于除零保护。"
    if value.startswith("=SUM"):
        return "SUM 会汇总指定区域内的数字。"
    return "该公式已写入工作簿，请结合引用区域检查业务口径。"

