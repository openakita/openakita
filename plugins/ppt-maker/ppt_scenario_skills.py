"""Scenario skill registry for ppt-maker generation v2."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioSkill:
    id: str
    label: str
    description: str
    storyline: tuple[str, ...]
    title_rule: str
    layout_bias: tuple[str, ...]


SCENARIO_SKILLS: dict[str, ScenarioSkill] = {
    "consulting_report": ScenarioSkill(
        id="consulting_report",
        label="Consulting Report",
        description="Conclusion-first executive report with MECE sections.",
        storyline=("结论摘要", "背景与问题", "关键洞察", "方案设计", "落地计划", "风险与下一步"),
        title_rule="Use action-oriented titles that state the conclusion.",
        layout_bias=("insight_summary", "comparison", "matrix", "chart_bar", "summary"),
    ),
    "product_launch": ScenarioSkill(
        id="product_launch",
        label="Product Launch",
        description="High-impact product story for external or internal launch.",
        storyline=("开场主张", "用户痛点", "产品亮点", "场景案例", "价值证明", "行动号召"),
        title_rule="Use bold benefit-led titles.",
        layout_bias=("cover", "hero_image", "metric_cards", "comparison", "closing"),
    ),
    "data_report": ScenarioSkill(
        id="data_report",
        label="Data Report",
        description="Evidence-driven report centered on metrics, charts, and caveats.",
        storyline=("数据概况", "核心指标", "趋势对比", "关键洞察", "风险口径", "行动建议"),
        title_rule="Use titles that pair insight with evidence.",
        layout_bias=("data_overview", "metric_cards", "chart_line", "chart_bar", "insight_summary"),
    ),
    "training_course": ScenarioSkill(
        id="training_course",
        label="Training Course",
        description="Teaching-oriented deck with concepts, examples, and exercises.",
        storyline=("学习目标", "核心概念", "案例拆解", "步骤演练", "常见错误", "复盘总结"),
        title_rule="Use clear learning-objective titles.",
        layout_bias=("agenda", "content", "process_flow", "comparison", "summary"),
    ),
    "project_review": ScenarioSkill(
        id="project_review",
        label="Project Review",
        description="Retrospective or milestone report with status, risks, and next steps.",
        storyline=("项目目标", "进展概览", "关键成果", "问题风险", "里程碑", "下一步"),
        title_rule="Use status-forward titles.",
        layout_bias=("timeline", "metric_cards", "insight_summary", "summary"),
    ),
}


def select_scenario_skill(*, mode: str, style: str = "", prompt: str = "", has_table: bool = False) -> ScenarioSkill:
    """Pick a conservative skill from explicit signals."""

    text = f"{mode} {style} {prompt}".lower()
    if has_table or "data" in text or "数据" in text or "table" in text:
        return SCENARIO_SKILLS["data_report"]
    if "consult" in text or "咨询" in text or "strategy" in text or "战略" in text:
        return SCENARIO_SKILLS["consulting_report"]
    if "launch" in text or "发布" in text or "pitch" in text or "提案" in text:
        return SCENARIO_SKILLS["product_launch"]
    if "training" in text or "培训" in text or "course" in text or "课件" in text:
        return SCENARIO_SKILLS["training_course"]
    if "review" in text or "复盘" in text or "项目" in text:
        return SCENARIO_SKILLS["project_review"]
    return SCENARIO_SKILLS["consulting_report" if "consulting" in text else "project_review"]

