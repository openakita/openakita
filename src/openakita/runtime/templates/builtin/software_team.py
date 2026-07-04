"""Built-in template: Software Engineering Team.

Ten-node engineering org that mirrors the legacy ``software-team``
template (``orgs/templates.py`` lines 357-545):

::

    tech_lead (LLM)
       ├── fe_lead (LLM)
       │     ├── fe_dev_a (LLM)
       │     └── fe_dev_b (LLM)
       ├── be_lead (LLM)
       │     ├── be_dev_a (LLM)
       │     └── be_dev_b (LLM)
       ├── qa (LLM)
       ├── devops_eng (LLM)
       └── tech_writer (LLM)

    Collaborate edges: fe_lead ↔ be_lead (API integration),
    devops_eng → fe_lead, devops_eng → be_lead (deploy coordination).

    Consult edges: qa → fe_lead, qa → be_lead (test feedback).

The legacy schema carried positional fields (x/y), department
strings, and avatars that the supervisor never read. The v2 spec
strips them; if a user wants those for layout they can add them
at instantiation overrides time once the editor lands in Phase 6.

The ten role-handles are normalised to lowercase + underscores per
the role-handle regex in ADR-0008. Display labels stay Chinese to
match the legacy template's audience.
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

_TECH_LEAD_PERSONA = """\
你是【技术负责人】节点，负责整体技术方向与跨组协调。
直属下级：fe_lead（前端组长）、be_lead（后端组长）、qa（QA 工程师）、
devops_eng（DevOps 工程师）、tech_writer（技术文档）。
工作流：
1) 把产品需求拆成里程碑，向 fe_lead 与 be_lead 同步派发，要求他们各自
   把任务分发给所辖前端/后端开发；
2) 让 qa 持续编写并执行测试，结果以 consult 方式回流到前后端组长；
3) 让 devops_eng 维护 CI / 部署流水线，与前后端组长 collaborate；
4) 让 tech_writer 同步整理技术文档与 changelog；
5) 收到所有下游交付后向产品负责人统一交付。
"""

_FE_LEAD_PERSONA = """\
你是【前端组长】节点，把控前端开发进度与质量。
直属下级：fe_dev_a / fe_dev_b（前端开发）。
工作流：
1) 把上级派发的功能拆为前端任务，分别派给 fe_dev_a 与 fe_dev_b；
2) 与 be_lead collaborate 完成 API 对接与字段约定；
3) 接受 qa 的 consult 反馈并把缺陷转为开发任务；
4) 收齐两位开发的交付后向 tech_lead 交付。
"""

_BE_LEAD_PERSONA = """\
你是【后端组长】节点，把控后端开发进度与质量。
直属下级：be_dev_a / be_dev_b（后端开发）。
工作流：
1) 把上级派发的功能拆为后端任务，分别派给 be_dev_a 与 be_dev_b；
2) 与 fe_lead collaborate 完成 API 契约；
3) 接受 qa 的 consult 反馈并把缺陷转为开发任务；
4) 与 devops_eng collaborate 协调部署；
5) 收齐两位开发的交付后向 tech_lead 交付。
"""

_FE_DEV_PERSONA = """\
你是【前端开发】节点，专注完成上级派发的前端功能开发。
按 filesystem / memory 两个工具集合实施：阅读已有代码、写新组件 / 状态 /
样式、补单元测试，最后用 deliverable 把改动文件清单 + 关键变更摘要回交给
fe_lead。遇到 API 字段歧义先在 deliverable 里提出问题给 fe_lead 协调。
"""

_BE_DEV_PERSONA = """\
你是【后端开发】节点，专注完成上级派发的后端功能开发。
按 filesystem / memory 实施：阅读已有代码、新增/修改 handler / service /
存储层、补单元测试，最后用 deliverable 把改动文件清单 + 关键接口变更回交
给 be_lead。需要 schema 变更或 fe 配合时，先在 deliverable 里写明字段表。
"""

_QA_PERSONA = """\
你是【QA 工程师】节点，负责测试用例编写与执行，覆盖功能 / 接口 / 集成层面。
通过 consult 边把缺陷反馈给 fe_lead / be_lead；deliverable 必须列出：
覆盖率简述、关键缺陷 ID、是否阻塞发布。
"""

_DEVOPS_PERSONA = """\
你是【DevOps 工程师】节点，负责 CI / CD 流水线与生产环境。
与 fe_lead / be_lead collaborate：当上级或组长要求发布或回滚时，先在
deliverable 里把 pipeline 状态、构建版本、需要的 secret / 配置改动写清楚，
再执行；不要在没有上级确认的情况下直接发起破坏性变更。
"""

_WRITER_PERSONA = """\
你是【技术文档】节点，负责把上级与各组长的交付沉淀为面向用户与开发者
的文档。deliverable 给出：文档章节清单、变更摘要、待审稿内容。
"""


@template
def software_team() -> TemplateSpec:
    """Return a fresh :class:`TemplateSpec` for the Software Team."""
    common_tools = ("research", "planning", "filesystem", "memory")
    dev_tools = ("filesystem", "memory")
    return TemplateSpec(
        id="software_team",
        name="Software Engineering Team",
        category="engineering",
        description=(
            "Ten-node engineering organisation: a tech lead, paired "
            "frontend / backend leads each owning two developers, plus QA, "
            "DevOps, and a technical writer. Mirrors the legacy "
            "software-team template in the v2 schema."
        ),
        version=1,
        defaults=DefaultsSpec(max_turns=40, max_stalls=3, suspect_secs=90),
        nodes=(
            NodeSpec(
                id="tech_lead",
                type=NodeType.LLM,
                role="tech_lead",
                label="技术负责人",
                persona_prompt=_TECH_LEAD_PERSONA,
                tool_subset=common_tools,
                department="工程",
            ),
            NodeSpec(
                id="fe_lead",
                type=NodeType.LLM,
                role="fe_lead",
                label="前端组长",
                persona_prompt=_FE_LEAD_PERSONA,
                tool_subset=common_tools,
                department="前端组",
            ),
            NodeSpec(
                id="fe_dev_a",
                type=NodeType.LLM,
                role="fe_dev",
                label="前端开发A",
                persona_prompt=_FE_DEV_PERSONA,
                tool_subset=dev_tools,
                department="前端组",
            ),
            NodeSpec(
                id="fe_dev_b",
                type=NodeType.LLM,
                role="fe_dev",
                label="前端开发B",
                persona_prompt=_FE_DEV_PERSONA,
                tool_subset=dev_tools,
                department="前端组",
            ),
            NodeSpec(
                id="be_lead",
                type=NodeType.LLM,
                role="be_lead",
                label="后端组长",
                persona_prompt=_BE_LEAD_PERSONA,
                tool_subset=common_tools,
                department="后端组",
            ),
            NodeSpec(
                id="be_dev_a",
                type=NodeType.LLM,
                role="be_dev",
                label="后端开发A",
                persona_prompt=_BE_DEV_PERSONA,
                tool_subset=dev_tools,
                department="后端组",
            ),
            NodeSpec(
                id="be_dev_b",
                type=NodeType.LLM,
                role="be_dev",
                label="后端开发B",
                persona_prompt=_BE_DEV_PERSONA,
                tool_subset=dev_tools,
                department="后端组",
            ),
            NodeSpec(
                id="qa",
                type=NodeType.LLM,
                role="qa",
                label="QA 工程师",
                persona_prompt=_QA_PERSONA,
                tool_subset=dev_tools,
                department="工程",
            ),
            NodeSpec(
                id="devops_eng",
                type=NodeType.LLM,
                role="devops",
                label="DevOps 工程师",
                persona_prompt=_DEVOPS_PERSONA,
                tool_subset=dev_tools,
                department="工程",
            ),
            NodeSpec(
                id="tech_writer",
                type=NodeType.LLM,
                role="tech_writer",
                label="技术文档",
                persona_prompt=_WRITER_PERSONA,
                tool_subset=("research", "filesystem", "memory"),
                department="工程",
            ),
        ),
        edges=(
            EdgeSpec(src="tech_lead", dst="fe_lead", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="tech_lead", dst="be_lead", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="tech_lead", dst="qa", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="tech_lead", dst="devops_eng", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="tech_lead", dst="tech_writer", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="fe_lead", dst="fe_dev_a", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="fe_lead", dst="fe_dev_b", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="be_lead", dst="be_dev_a", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="be_lead", dst="be_dev_b", kind=EdgeKind.HIERARCHY),
            EdgeSpec(src="fe_lead", dst="be_lead", kind=EdgeKind.COLLABORATE),
            EdgeSpec(src="qa", dst="fe_lead", kind=EdgeKind.CONSULT),
            EdgeSpec(src="qa", dst="be_lead", kind=EdgeKind.CONSULT),
            EdgeSpec(src="devops_eng", dst="fe_lead", kind=EdgeKind.COLLABORATE),
            EdgeSpec(src="devops_eng", dst="be_lead", kind=EdgeKind.COLLABORATE),
        ),
    )
