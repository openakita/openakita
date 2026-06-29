"""Built-in template: Startup Company.

Sixteen-node generic startup org — CEO + four C-level directors
(CTO / CPO / CMO / CFO), each with their own department:

::

    ceo (LLM)
       ├── cto (LLM)
       │     ├── architect (LLM)
       │     ├── dev_a (LLM)
       │     ├── dev_b (LLM)
       │     └── devops (LLM)
       ├── cpo (LLM)
       │     ├── pm (LLM)
       │     └── ui_designer (LLM)
       ├── cmo (LLM)
       │     ├── content_op (LLM)
       │     ├── seo (LLM)
       │     └── social_media (LLM)
       └── cfo (LLM)
             ├── hr (LLM)
             └── legal (LLM)

    Cross-department collaborate edges: cpo↔cto (product/tech
    alignment), pm↔dev_a, pm↔dev_b (requirements), content_op↔seo
    (content tuning).

Mirrors the legacy ``startup-company`` template (lines 19-355) but
expressed in the v2 schema — no avatars / positions / departments
in the spec because the supervisor never read them.
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

_CEO_PERSONA = """\
你是【CEO】节点，公司最高决策者。直属下级：cto / cpo / cmo / cfo。
工作流：
1) 把董事长的战略目标拆为部门级里程碑，分别派发给四位 C-level；
2) 收到各部门交付后做横向对齐，必要时让 cpo 与 cto collaborate 解决
   产品技术分歧；
3) deliverable 给董事长：里程碑达成情况 + 阻塞 + 下阶段建议。
"""

_CTO_PERSONA = """\
你是【CTO】节点，技术线最高负责人。直属下级：architect、dev_a、
dev_b、devops。把上级里程碑拆成架构 / 开发 / 运维任务并并行派发。
deliverable 必须列出：当前架构状态、已交付功能、运维健康度、风险。
"""

_CPO_PERSONA = """\
你是【CPO】节点，产品线最高负责人。直属下级：pm、ui_designer。
与 cto collaborate 完成产品/技术对齐。deliverable 列出：产品 roadmap、
本期需求、UX 状态、用户反馈摘要。
"""

_CMO_PERSONA = """\
你是【CMO】节点，市场线最高负责人。直属下级：content_op、seo、
social_media。deliverable 列出：本期营销主题、内容产出 / 投放数据、
增长指标。
"""

_CFO_PERSONA = """\
你是【CFO】节点，行政与财务线最高负责人。直属下级：hr、legal。
deliverable 列出：现金流状况、招聘进展、合规 / 法务状态。
"""

_ARCHITECT_PERSONA = """\
你是【架构师】节点。负责设计与维护系统架构、制定技术规范，给 cto
deliverable 时附：目标系统图、关键决策、技术债清单。
"""

_DEV_A_PERSONA = """\
你是【全栈工程师 A】节点。专注前后端均衡的功能交付。deliverable
包括：变更文件列表、关键改动摘要、测试结论。
"""

_DEV_B_PERSONA = """\
你是【全栈工程师 B】节点。擅长性能优化与可靠性。deliverable 包括：
变更文件列表、性能 / 测试基准、与 dev_a 的接口对接情况。
"""

_DEVOPS_PERSONA = """\
你是【DevOps 工程师】节点。负责 CI / CD、容器化与可观测性。
deliverable 列出：流水线状态、环境健康度、最近一次发布的版本与回滚路径。
"""

_PM_PERSONA = """\
你是【产品经理】节点。负责需求拆解、排期、与 dev_a / dev_b 沟通需求。
deliverable 列出：需求文档摘要、排期表、阻塞与依赖。
"""

_UI_PERSONA = """\
你是【UI 设计师】节点。产出可交付的视觉与交互稿。deliverable 列出：
本次稿件清单、关键交互说明、与产品 / 工程的对齐进度。
"""

_CONTENT_PERSONA = """\
你是【内容运营】节点。产出高质量内容、维护发布节奏。deliverable
列出：本期内容主题、产出清单、与 seo 的协调结果。
"""

_SEO_PERSONA = """\
你是【SEO 专员】节点。优化搜索引擎自然流量。deliverable 列出：
关键词覆盖、收录变化、需要 content_op 配合调整的内容点。
"""

_SOCIAL_PERSONA = """\
你是【社媒运营】节点。维护社交媒体账号与社群互动。deliverable 列出：
本期话题、互动指标、品牌口碑监测要点。
"""

_HR_PERSONA = """\
你是【HR】节点。负责招聘与团队文化。deliverable 列出：候选人漏斗、
入职 / 离职动态、文化建设动作。
"""

_LEGAL_PERSONA = """\
你是【法务顾问】节点。提供法律咨询与合规检查。deliverable 列出：
当前合规风险点、合同状态、需要 cfo 知会的法律变更。
"""


@template
def startup_company() -> TemplateSpec:
    """Return a fresh :class:`TemplateSpec` for the Startup Company."""
    director_tools = ("research", "planning", "memory")
    director_full = ("research", "planning", "filesystem", "memory")
    builder_tools = ("filesystem", "memory")
    research_only = ("research", "memory")
    return TemplateSpec(
        id="startup_company",
        name="Startup Company",
        category="company",
        description=(
            "Sixteen-node generic startup organisation: CEO + four C-level "
            "directors (CTO / CPO / CMO / CFO) each with their own "
            "department. Mirrors the legacy startup-company template in the "
            "v2 schema."
        ),
        version=1,
        defaults=DefaultsSpec(max_turns=50, max_stalls=3, suspect_secs=120),
        nodes=(
            NodeSpec(
                id="ceo",
                type=NodeType.LLM,
                role="ceo",
                label="CEO",
                persona_prompt=_CEO_PERSONA,
                tool_subset=director_tools,
                department="管理层",
            ),
            NodeSpec(
                id="cto",
                type=NodeType.LLM,
                role="cto",
                label="CTO",
                persona_prompt=_CTO_PERSONA,
                tool_subset=director_full,
                department="技术部",
            ),
            NodeSpec(
                id="architect",
                type=NodeType.LLM,
                role="architect",
                label="架构师",
                persona_prompt=_ARCHITECT_PERSONA,
                tool_subset=("research", "filesystem", "memory"),
                department="技术部",
            ),
            NodeSpec(
                id="dev_a",
                type=NodeType.LLM,
                role="full_stack_dev",
                label="全栈工程师A",
                persona_prompt=_DEV_A_PERSONA,
                tool_subset=builder_tools,
                department="技术部",
            ),
            NodeSpec(
                id="dev_b",
                type=NodeType.LLM,
                role="full_stack_dev",
                label="全栈工程师B",
                persona_prompt=_DEV_B_PERSONA,
                tool_subset=builder_tools,
                department="技术部",
            ),
            NodeSpec(
                id="devops",
                type=NodeType.LLM,
                role="devops",
                label="DevOps 工程师",
                persona_prompt=_DEVOPS_PERSONA,
                tool_subset=builder_tools,
                department="技术部",
            ),
            NodeSpec(
                id="cpo",
                type=NodeType.LLM,
                role="cpo",
                label="CPO",
                persona_prompt=_CPO_PERSONA,
                tool_subset=director_tools,
                department="产品部",
            ),
            NodeSpec(
                id="pm",
                type=NodeType.LLM,
                role="product_manager",
                label="产品经理",
                persona_prompt=_PM_PERSONA,
                tool_subset=("research", "planning", "memory"),
                department="产品部",
            ),
            NodeSpec(
                id="ui_designer",
                type=NodeType.LLM,
                role="ui_designer",
                label="UI 设计师",
                persona_prompt=_UI_PERSONA,
                tool_subset=("browser", "filesystem"),
                department="产品部",
            ),
            NodeSpec(
                id="cmo",
                type=NodeType.LLM,
                role="cmo",
                label="CMO",
                persona_prompt=_CMO_PERSONA,
                tool_subset=director_tools,
                department="市场部",
            ),
            NodeSpec(
                id="content_op",
                type=NodeType.LLM,
                role="content_operator",
                label="内容运营",
                persona_prompt=_CONTENT_PERSONA,
                tool_subset=("research", "filesystem", "memory"),
                department="市场部",
            ),
            NodeSpec(
                id="seo",
                type=NodeType.LLM,
                role="seo_specialist",
                label="SEO 专员",
                persona_prompt=_SEO_PERSONA,
                tool_subset=research_only,
                department="市场部",
            ),
            NodeSpec(
                id="social_media",
                type=NodeType.LLM,
                role="social_media_op",
                label="社媒运营",
                persona_prompt=_SOCIAL_PERSONA,
                tool_subset=research_only,
                department="市场部",
            ),
            NodeSpec(
                id="cfo",
                type=NodeType.LLM,
                role="cfo",
                label="CFO",
                persona_prompt=_CFO_PERSONA,
                tool_subset=research_only,
                department="行政支持",
            ),
            NodeSpec(
                id="hr",
                type=NodeType.LLM,
                role="hr",
                label="HR",
                persona_prompt=_HR_PERSONA,
                tool_subset=research_only,
                department="行政支持",
            ),
            NodeSpec(
                id="legal",
                type=NodeType.LLM,
                role="legal",
                label="法务顾问",
                persona_prompt=_LEGAL_PERSONA,
                tool_subset=research_only,
                department="行政支持",
            ),
        ),
        edges=(
            EdgeSpec(src="ceo", dst="cto", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="ceo", dst="cpo", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="ceo", dst="cmo", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="ceo", dst="cfo", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cto", dst="architect", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cto", dst="dev_a", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cto", dst="dev_b", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cto", dst="devops", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cpo", dst="pm", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cpo", dst="ui_designer", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cmo", dst="content_op", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cmo", dst="seo", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cmo", dst="social_media", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cfo", dst="hr", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cfo", dst="legal", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="cpo", dst="cto", kind=EdgeKind.COLLABORATE),
            EdgeSpec(src="pm", dst="dev_a", kind=EdgeKind.COLLABORATE),
            EdgeSpec(src="pm", dst="dev_b", kind=EdgeKind.COLLABORATE),
            EdgeSpec(src="content_op", dst="seo", kind=EdgeKind.COLLABORATE),
        ),
    )
