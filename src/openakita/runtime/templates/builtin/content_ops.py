"""Built-in template: Content Operations Team.

Seven-node editorial team led by an editor-in-chief, mirroring the
legacy ``content-ops`` template (lines 546-695):

::

    editor_in_chief (LLM)
       ├── planner (LLM)
       │     ├── writer_a (LLM)
       │     ├── writer_b (LLM)
       │     └── visual (LLM)
       ├── seo_opt (LLM)
       └── data_analyst (LLM)

    Collaborate edges: writer_a / writer_b ↔ seo_opt (content
    optimisation), writer_a / writer_b ↔ visual (illustration
    coordination), data_analyst ↔ planner (data-driven topics).
"""

from __future__ import annotations

from ...models import EdgeKind, NodeType
from ..registry import template
from ..schema import (
    DefaultsSpec,
    EdgeSpec,
    NodeSpec,
    TemplateSpec,
)

_EDITOR_PERSONA = """\
你是【主编】节点。直属下级：planner（策划编辑）、seo_opt（SEO）、
data_analyst（数据分析）。工作流：
1) 把出品人的内容方向拆成选题 / 排期任务派给 planner；
2) 让 seo_opt 全程关注关键词与流量目标，让 data_analyst 把读者数据
   反馈给 planner 形成迭代闭环；
3) deliverable 给出品人：本期内容总览、流量数据、下期建议。
"""

_PLANNER_PERSONA = """\
你是【策划编辑】节点。直属下级：writer_a / writer_b / visual。
工作流：
1) 根据主编的方向 + data_analyst 的数据线索，输出本期选题清单与排期；
2) 把每个选题派给 writer_a 或 writer_b，并让 visual 负责配图/封面；
3) deliverable 列出：选题 → 文案 → 配图 → 发布时间的对照表。
"""

_WRITER_A_PERSONA = """\
你是【文案写手 A】节点，擅长深度长文。按 planner 派单产出文章：
research → 大纲 → 正文 → 自检。deliverable 列出：标题、字数、引用来源、
亮点段落。与 seo_opt collaborate 优化关键词，与 visual collaborate 提供
配图说明。
"""

_WRITER_B_PERSONA = """\
你是【文案写手 B】节点，擅长短文与社媒文案。按 planner 派单产出短稿：
角度 → 钩子 → 正文 → CTA。deliverable 列出：标题、平台、字数、转化点。
与 seo_opt 协调关键词，与 visual 协调配图。
"""

_VISUAL_PERSONA = """\
你是【视觉设计】节点，按 planner / writer 协调结果产出配图与素材。
deliverable 列出：素材清单、规格、版权状态。
"""

_SEO_PERSONA = """\
你是【SEO 优化师】节点。负责关键词选择与内容 SEO 体检。
deliverable 列出：本期关键词覆盖、风险点、给 writer 的优化建议清单。
"""

_DATA_PERSONA = """\
你是【数据分析】节点。负责追踪内容数据并把洞察 collaborate 给 planner。
deliverable 列出：核心指标变化、读者画像变化、下一波选题建议。
"""


@template
def content_ops() -> TemplateSpec:
    """Return a fresh :class:`TemplateSpec` for the Content Ops team."""
    return TemplateSpec(
        id="content_ops",
        name="Content Operations Team",
        category="content",
        description=(
            "Seven-node editorial team led by an editor-in-chief: a "
            "planning editor owns two writers and a visual designer; "
            "an SEO specialist and a data analyst close the loop with "
            "data-driven topic suggestions. Mirrors the legacy content-ops "
            "template in the v2 schema."
        ),
        version=1,
        defaults=DefaultsSpec(max_turns=40, max_stalls=3, suspect_secs=90),
        nodes=(
            NodeSpec(
                id="editor_in_chief",
                type=NodeType.LLM,
                role="editor_in_chief",
                label="主编",
                persona_prompt=_EDITOR_PERSONA,
                tool_subset=("research", "planning", "memory"),
                department="编辑部",
            ),
            NodeSpec(
                id="planner",
                type=NodeType.LLM,
                role="content_planner",
                label="策划编辑",
                persona_prompt=_PLANNER_PERSONA,
                tool_subset=("research", "planning", "memory"),
                department="编辑部",
            ),
            NodeSpec(
                id="writer_a",
                type=NodeType.LLM,
                role="writer",
                label="文案写手A",
                persona_prompt=_WRITER_A_PERSONA,
                tool_subset=("research", "filesystem", "memory"),
                department="创作组",
            ),
            NodeSpec(
                id="writer_b",
                type=NodeType.LLM,
                role="writer",
                label="文案写手B",
                persona_prompt=_WRITER_B_PERSONA,
                tool_subset=("research", "filesystem", "memory"),
                department="创作组",
            ),
            NodeSpec(
                id="visual",
                type=NodeType.LLM,
                role="visual_designer",
                label="视觉设计",
                persona_prompt=_VISUAL_PERSONA,
                tool_subset=("browser", "filesystem"),
                department="创作组",
            ),
            NodeSpec(
                id="seo_opt",
                type=NodeType.LLM,
                role="seo_optimizer",
                label="SEO 优化师",
                persona_prompt=_SEO_PERSONA,
                tool_subset=("research", "memory"),
                department="运营组",
            ),
            NodeSpec(
                id="data_analyst",
                type=NodeType.LLM,
                role="data_analyst",
                label="数据分析",
                persona_prompt=_DATA_PERSONA,
                tool_subset=("research", "memory"),
                department="运营组",
            ),
        ),
        edges=(
            EdgeSpec(
                src="editor_in_chief", dst="planner", kind=EdgeKind.HIERARCHY
            ),
            EdgeSpec(
                src="editor_in_chief", dst="seo_opt", kind=EdgeKind.HIERARCHY
            ),
            EdgeSpec(
                src="editor_in_chief", dst="data_analyst", kind=EdgeKind.HIERARCHY
            ),
            EdgeSpec(src="planner", dst="writer_a", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="planner", dst="writer_b", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="planner", dst="visual", kind=EdgeKind.HIERARCHY),
            EdgeSpec(
                src="writer_a", dst="seo_opt", kind=EdgeKind.COLLABORATE
            ),
            EdgeSpec(
                src="writer_b", dst="seo_opt", kind=EdgeKind.COLLABORATE
            ),
            EdgeSpec(src="writer_a", dst="visual", kind=EdgeKind.COLLABORATE),
            EdgeSpec(src="writer_b", dst="visual", kind=EdgeKind.COLLABORATE),
            EdgeSpec(
                src="data_analyst", dst="planner", kind=EdgeKind.COLLABORATE
            ),
        ),
    )
