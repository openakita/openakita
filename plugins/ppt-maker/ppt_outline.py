"""Outline generation and confirmation helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ppt_models import DeckMode, SlideType

# Generic, presentable bullet templates per slide type. The {topic} placeholder
# is replaced with the deck title or section title so the deterministic fallback
# never ships empty bullets / placeholder strings like "解释为什么要做这件事".
FALLBACK_BULLETS: dict[SlideType, list[str]] = {
    SlideType.AGENDA: [
        "背景与目标",
        "现状与挑战",
        "核心方案",
        "执行计划与里程碑",
        "总结与下一步",
    ],
    SlideType.CONTENT: [
        "{topic} 当前的关键现状与影响",
        "推动 {topic} 的核心机会与价值点",
        "落地 {topic} 的关键举措与负责团队",
        "需要重点关注的风险与缓解策略",
    ],
    SlideType.COMPARISON: [
        "现状：成本高、迭代慢、协同零散",
        "目标：自动化、可观测、统一接入",
    ],
    SlideType.TIMELINE: [
        "Q1 立项与方案对齐",
        "Q2 试点上线与数据验证",
        "Q3 全量推广与流程嵌入",
        "Q4 复盘迭代与下一阶段规划",
    ],
    SlideType.METRIC_CARDS: [
        "核心 KPI 同比提升",
        "关键流程效率改善",
        "客户满意度提升",
    ],
    SlideType.CHART_BAR: [
        "{topic} 各维度对比，凸显关键差异",
        "聚焦排名前列的高价值切入点",
    ],
    SlideType.CHART_LINE: [
        "{topic} 趋势持续上行",
        "拐点出现于关键举措落地期",
    ],
    SlideType.CHART_PIE: [
        "{topic} 主要构成比例",
        "排名前三占据主要份额",
    ],
    SlideType.DATA_TABLE: [
        "保留原始数据细节，方便比对",
    ],
    SlideType.DATA_OVERVIEW: [
        "数据来源、口径与统计周期",
        "样本量与覆盖范围",
        "数据质量校验结论",
    ],
    SlideType.INSIGHT_SUMMARY: [
        "{topic} 的核心发现",
        "需要警惕的风险与边界条件",
    ],
    SlideType.SECTION: [
        "进入 {topic} 章节",
    ],
    SlideType.SUMMARY: [
        "{topic} 的关键结论",
        "下一步行动与责任分配",
        "成功指标与复盘节奏",
    ],
    SlideType.CLOSING: [
        "感谢聆听",
        "欢迎提问与讨论",
    ],
    SlideType.COVER: [],
}


def _format_bullets(slide_type: SlideType, topic: str) -> list[str]:
    template = FALLBACK_BULLETS.get(slide_type) or []
    safe_topic = (topic or "本议题").strip()
    return [item.replace("{topic}", safe_topic) for item in template]


def _body_for(slide_type: SlideType, topic: str, audience: str) -> str:
    safe_topic = (topic or "本议题").strip()
    target = (audience or "业务相关方").strip()
    presets: dict[SlideType, str] = {
        SlideType.COVER: f"{safe_topic} 汇报材料：聚焦 {target} 关心的核心议题。",
        SlideType.AGENDA: f"本场围绕 {safe_topic} 展开，先讲背景，再讲方案，最后给出落地计划。",
        SlideType.CONTENT: f"围绕 {safe_topic}，结合 {target} 的关注点说明现状、思路和下一步。",
        SlideType.COMPARISON: "对比当前做法与新方案在效率、风险和体验上的差异。",
        SlideType.TIMELINE: f"按季度展开 {safe_topic} 的执行节奏，明确里程碑与责任人。",
        SlideType.DATA_OVERVIEW: f"先用一页让 {target} 看清数据的范围、口径与可信度。",
        SlideType.METRIC_CARDS: f"挑出最重要的几个 KPI，让 {target} 一眼抓住成果。",
        SlideType.CHART_BAR: f"用柱状图突出 {safe_topic} 各维度的差异。",
        SlideType.CHART_LINE: f"用折线图说明 {safe_topic} 的趋势走向和拐点。",
        SlideType.CHART_PIE: f"用饼图表达 {safe_topic} 的构成比例。",
        SlideType.DATA_TABLE: f"保留原始数据，方便 {target} 进一步追问细节。",
        SlideType.INSIGHT_SUMMARY: "基于数据给出关键洞察，并提示需要注意的边界条件。",
        SlideType.SECTION: f"进入 {safe_topic} 章节。",
        SlideType.SUMMARY: f"回顾 {safe_topic} 的核心结论，并给出明确的下一步动作。",
        SlideType.CLOSING: "感谢聆听，欢迎提问。",
    }
    return presets.get(slide_type, f"围绕 {safe_topic}，与 {target} 一起对齐关键观点。")


class OutlineBuilder:
    """Create a deterministic outline scaffold for all MVP modes."""

    def build(
        self,
        *,
        mode: DeckMode,
        title: str,
        slide_count: int,
        audience: str = "",
        style: str = "tech_business",
        requirements: dict[str, Any] | None = None,
        table_insights: dict[str, Any] | None = None,
        template_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        requirements = requirements or {}
        slides = self._slides_for_mode(mode, title, slide_count, audience, style, table_insights)
        outline = {
            "title": title,
            "mode": mode.value,
            "audience": audience,
            "requirements": requirements,
            "storyline": [slide["purpose"] for slide in slides],
            "slides": slides,
            "needs_confirmation": True,
            "confirmation_questions": self._confirmation_questions(mode, template_profile),
        }
        if table_insights:
            outline["table_insights_summary"] = table_insights.get("key_findings", [])
        if template_profile:
            outline["template_profile_summary"] = {
                "name": template_profile.get("name"),
                "warnings": template_profile.get("warnings", []),
            }
        return outline

    def confirm(self, outline: dict[str, Any], updates: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {**outline, **(updates or {})}
        result["confirmed"] = True
        result["needs_confirmation"] = False
        return result

    def save(self, outline: dict[str, Any], project_dir: str | Path) -> Path:
        path = Path(project_dir) / "outline.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _slides_for_mode(
        self,
        mode: DeckMode,
        title: str,
        slide_count: int,
        audience: str,
        style: str,
        table_insights: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if mode == DeckMode.TABLE_TO_DECK:
            base = [
                (title or "数据故事", SlideType.COVER, "建立数据汇报的主题。"),
                ("数据概况", SlideType.DATA_OVERVIEW, "说明数据范围、口径和质量。"),
                ("核心指标", SlideType.METRIC_CARDS, "提炼最重要的经营指标。"),
                ("趋势与对比", SlideType.CHART_LINE, "展示关键趋势或分组对比。"),
                ("重点洞察", SlideType.INSIGHT_SUMMARY, "总结数据背后的业务含义。"),
                ("行动建议", SlideType.SUMMARY, "给出下一步建议。"),
                ("Q&A", SlideType.CLOSING, "结束和互动。"),
            ]
            findings = (table_insights or {}).get("key_findings", [])
        else:
            base = [
                (title or "汇报议题", SlideType.COVER, "建立主题和汇报场景。"),
                ("议程", SlideType.AGENDA, "让听众了解结构。"),
                ("背景与目标", SlideType.CONTENT, "对齐立项动因与预期成果。"),
                ("现状与挑战", SlideType.CONTENT, "讲清当前痛点和约束条件。"),
                ("核心方案", SlideType.CONTENT, "展开主要观点和关键能力。"),
                ("关键对比", SlideType.COMPARISON, "比较选择和取舍。"),
                ("路线图", SlideType.TIMELINE, "说明执行节奏与里程碑。"),
                ("总结", SlideType.SUMMARY, "收束结论与下一步动作。"),
                ("Q&A", SlideType.CLOSING, "结束和互动。"),
            ]
            findings = []
        selected = base[: max(1, min(slide_count, len(base)))]
        # Pad with content slides while preserving the cover/closing positions
        while len(selected) < slide_count:
            insertion_idx = max(1, len(selected) - 1)
            selected.insert(
                insertion_idx,
                (f"补充内容 {len(selected) - 1}", SlideType.CONTENT, "补充支撑论据与数据。"),
            )
        slides = []
        for index, (slide_title, slide_type, purpose) in enumerate(selected, start=1):
            topic_for_bullets = slide_title if slide_type != SlideType.COVER else title
            if slide_type == SlideType.INSIGHT_SUMMARY and findings:
                bullets = [str(item) for item in findings[:5]] or _format_bullets(slide_type, topic_for_bullets)
            elif slide_type == SlideType.METRIC_CARDS and findings:
                bullets = [str(item) for item in findings[:3]]
            else:
                bullets = _format_bullets(slide_type, topic_for_bullets)
            slides.append(
                {
                    "id": f"slide_{index:02d}",
                    "index": index,
                    "title": slide_title,
                    "slide_type": slide_type.value,
                    "purpose": purpose,
                    "key_points": bullets,
                    "body": _body_for(slide_type, topic_for_bullets, audience),
                    "speaker_note": purpose,
                    "image_query": None,
                    "icon_query": None,
                }
            )
        return slides

    def _confirmation_questions(
        self,
        mode: DeckMode,
        template_profile: dict[str, Any] | None,
    ) -> list[str]:
        questions = ["页数是否合适？", "受众和汇报语气是否准确？", "是否需要调整章节顺序？"]
        if mode == DeckMode.TABLE_TO_DECK:
            questions.append("图表页是否覆盖了你最关心的指标？")
        if mode == DeckMode.TEMPLATE_DECK or template_profile:
            questions.append("是否接受模板 fallback，而不是 1:1 复刻所有母版效果？")
        return questions

