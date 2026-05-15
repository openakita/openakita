import pytest

from openakita.core.risk_intent import OperationKind, RiskIntentClassifier, TargetKind
from openakita.orgs.models import OrgNode
from openakita.orgs.runtime import OrgRuntime
from openakita.tools.handlers.memory import MemoryHandler
from openakita.tools.handlers.powershell import PowerShellHandler
from openakita.tools.handlers.todo_handler import PlanHandler
from openakita.tools.input_normalizer import normalize_tool_input

# C8b-6b：原本 5 个 v1 PolicyEngine 测试（test_workspace_delete_is_confirmed_even_in_trust_mode
# / test_unknown_mcp_write_tool_requires_confirmation / test_readonly_mcp_tool_is_allowed_in_trust_mode
# / test_powershell_remove_item_is_confirmed_even_in_trust_mode 等）已随 v1
# ``policy.py`` 删除一并清理。等价 v2 行为已被 ``test_policy_engine_v2.py`` /
# ``test_policy_v2_*`` 系列覆盖（v2 PolicyEngine 决策矩阵 + safety_immune + ApprovalClassifier）。


def test_desktop_delete_natural_language_requires_confirmation():
    result = RiskIntentClassifier().classify("请删除我桌面上的 old.log")

    assert result.operation_kind == OperationKind.DELETE
    assert result.target_kind == TargetKind.FILE_SYSTEM
    assert result.requires_confirmation is True


def test_unknown_target_delete_natural_language_requires_confirmation():
    result = RiskIntentClassifier().classify("把不要的备份删掉，不用问我")

    assert result.operation_kind == OperationKind.DELETE
    assert result.requires_confirmation is True


def test_legacy_org_node_gets_profile_binding():
    node = OrgNode.from_dict({
        "id": "dev-a",
        "role_title": "全栈工程师",
        "department": "技术部",
    })

    assert node.agent_profile_id == "code-assistant"


def test_org_runtime_collects_tool_stats_from_trace():
    stats = OrgRuntime._collect_tool_stats_from_trace([
        {
            "tool_calls": [
                {"id": "t1", "name": "org_delegate_task"},
                {"id": "t2", "name": "org_accept_deliverable"},
            ],
            "tool_results": [{"tool_use_id": "t1", "is_error": False}],
        }
    ])

    assert stats["tools_total"] == 2
    assert [t["name"] for t in stats["tools_used"]] == [
        "org_delegate_task",
        "org_accept_deliverable",
    ]


def test_powershell_clixml_noise_is_stripped():
    raw = "#< CLIXML\r\n<Objs><Obj><MS>progress noise</MS></Obj></Objs>\r\nreal output"

    assert PowerShellHandler._strip_clixml_noise(raw) == "real output"


def test_powershell_multiline_clixml_noise_is_stripped():
    raw = "#< CLIXML\r\n<Objs Version=\"1.1.0.1\">\r\n<Obj>progress</Obj>\r\n</Objs>\r\nreal output"

    assert PowerShellHandler._strip_clixml_noise(raw) == "real output"


def test_plan_steps_are_parsed_from_markdown_body():
    steps = PlanHandler._parse_plan_todos_from_markdown(
        "## 计划\n1. 调研现状\n2. 制定方案\n- [ ] 验证回归"
    )

    assert [s["content"] for s in steps] == ["调研现状", "制定方案", "验证回归"]


def test_plan_steps_are_parsed_from_markdown_table():
    steps = PlanHandler._parse_plan_todos_from_markdown(
        "| 步骤 | 任务 |\n|------|------|\n| 1 | 创建 README 模板 |\n| 2 | 编写贡献指南 |"
    )

    assert [s["content"] for s in steps] == ["创建 README 模板", "编写贡献指南"]


def test_create_plan_file_input_aliases_are_normalized():
    normalized = normalize_tool_input(
        "create_plan_file",
        {
            "plan_name": "文档计划",
            "content": "## 计划\n1. 创建 README\n2. 写贡献指南",
            "steps": [{"description": "创建 README"}, {"description": "写贡献指南"}],
        },
    )

    assert normalized["name"] == "文档计划"
    assert normalized["body"].startswith("## 计划")
    assert [t["content"] for t in normalized["todos"]] == ["创建 README", "写贡献指南"]


@pytest.mark.asyncio
async def test_create_plan_file_rejects_empty_plan(tmp_path, monkeypatch):
    handler = PlanHandler(agent=object())
    handler.plan_dir = tmp_path

    result = await handler._create_plan_file({"name": "empty", "body": "没有步骤的说明"})

    assert result.startswith("❌ 无法创建空 Plan")
    assert not list(tmp_path.glob("*.plan.md"))


@pytest.mark.asyncio
async def test_create_plan_file_accepts_legacy_aliases(tmp_path):
    handler = PlanHandler(agent=object())
    handler.plan_dir = tmp_path

    result = await handler._create_plan_file({
        "plan_name": "文档计划",
        "content": "| 步骤 | 任务 |\n|---|---|\n| 1 | 创建 README |\n| 2 | 写贡献指南 |",
    })

    assert result.startswith("✅ Plan 文件已创建")
    plan_file = next(tmp_path.glob("*.plan.md"))
    content = plan_file.read_text(encoding="utf-8")
    assert "创建 README" in content
    assert "写贡献指南" in content


class _FakeMemoryManager:
    def __init__(self):
        self.store = None
        self.calls = 0

    def search_memories(self, **kwargs):
        self.calls += 1
        return []

    def record_cited_memories(self, cited):
        pass


class _FakeSession:
    id = "session-a"
    messages = [
        {"role": "user", "content": "请记住项目代号 SEAGULL"},
        {"role": "assistant", "content": "已记录"},
    ]


class _FakeAgent:
    def __init__(self):
        self.memory_manager = _FakeMemoryManager()
        self._current_conversation_id = "session-a"
        self._current_session_id = "session-a"
        self._current_session = _FakeSession()


def test_search_conversation_traces_checks_current_session_first():
    handler = MemoryHandler(_FakeAgent())

    result = handler._search_conversation_traces({"keyword": "SEAGULL", "max_results": 5})

    assert "current_session" in result
    assert "SEAGULL" in result


def test_search_memory_reuses_same_turn_cache():
    agent = _FakeAgent()
    handler = MemoryHandler(agent)

    first = handler._search_memory({"query": "不存在的内容"})
    second = handler._search_memory({"query": "不存在的内容"})

    assert "未找到" in first
    assert "复用缓存结果" in second
    assert agent.memory_manager.calls == 1
