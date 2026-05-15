from pathlib import Path
from types import SimpleNamespace

from openakita.core.current_turn import CurrentTurnInput, SessionObjectRegistry
from openakita.core.tool_executor import ToolExecutor
from openakita.tools.handlers import SystemHandlerRegistry


def test_current_turn_url_blocks_historical_web_fetch():
    turn = CurrentTurnInput.from_inputs("帮我读这个链接 https://example.com/new")

    blocked = turn.validate_tool_call("web_fetch", {"url": "https://example.com/old"})

    assert blocked is not None
    assert "应使用的 URL" in blocked
    assert "https://example.com/new" in blocked


def test_current_turn_url_allows_explicit_history_reference():
    turn = CurrentTurnInput.from_inputs("读一下上次那个链接，再对比 https://example.com/new")

    blocked = turn.validate_tool_call("web_fetch", {"url": "https://example.com/old"})

    assert blocked is None


def test_browser_page_read_requires_navigation_to_current_url():
    turn = CurrentTurnInput.from_inputs("看看这个 https://example.com/current")

    blocked = turn.validate_tool_call("browser_get_content", {})
    assert blocked is not None
    assert "browser_navigate" in blocked

    assert turn.validate_tool_call("browser_navigate", {"url": "https://example.com/current"}) is None
    turn.observe_tool_result("browser_navigate", {"url": "https://example.com/current"}, "✅ OK")

    assert turn.validate_tool_call("browser_get_content", {}) is None


def test_url_guard_allows_derived_links_after_current_url_is_grounded():
    turn = CurrentTurnInput.from_inputs("调研这个网站 https://example.com/current")

    blocked = turn.validate_tool_call("web_fetch", {"url": "https://example.com/other"})
    assert blocked is not None

    turn.observe_tool_result("web_fetch", {"url": "https://example.com/current"}, "content")

    assert turn.validate_tool_call("web_fetch", {"url": "https://example.com/other"}) is None


def test_browser_content_allows_non_current_page_after_current_url_is_grounded():
    turn = CurrentTurnInput.from_inputs("看看这个网站 https://example.com/current")
    turn.observe_tool_result("browser_navigate", {"url": "https://example.com/current"}, "✅ OK")

    assert turn.validate_tool_call("browser_navigate", {"url": "https://example.com/next"}) is None
    turn.observe_tool_result("browser_navigate", {"url": "https://example.com/next"}, "✅ OK")

    assert turn.validate_tool_call("browser_get_content", {}) is None


def test_current_turn_image_blocks_historical_view_image(tmp_path: Path):
    current = tmp_path / "current.png"
    old = tmp_path / "old.png"
    turn = CurrentTurnInput.from_inputs(
        "分析这张图",
        pending_images=[{"local_path": str(current), "filename": "current.png"}],
    )

    blocked = turn.validate_tool_call("view_image", {"path": str(old)})

    assert blocked is not None
    assert "应使用的图片" in blocked


def test_current_turn_file_blocks_implicit_historical_read(tmp_path: Path):
    current = tmp_path / "current.pdf"
    old = tmp_path / "old.pdf"
    turn = CurrentTurnInput.from_inputs(
        "总结一下这个文件",
        pending_files=[{"local_path": str(current), "filename": "current.pdf"}],
    )

    blocked = turn.validate_tool_call("read_file", {"path": str(old)})

    assert blocked is not None
    assert "应使用的文件" in blocked


def test_current_turn_file_allows_explicit_path_comparison(tmp_path: Path):
    current = tmp_path / "current.pdf"
    old = tmp_path / "old.pdf"
    turn = CurrentTurnInput.from_inputs(
        f"把这个文件和 {old} 对比",
        pending_files=[{"local_path": str(current), "filename": "current.pdf"}],
    )

    blocked = turn.validate_tool_call("read_file", {"path": str(old)})

    assert blocked is None


def test_prompt_block_lists_current_objects(tmp_path: Path):
    current = tmp_path / "current.png"
    turn = CurrentTurnInput.from_inputs(
        "看这个 https://example.com/a",
        pending_images=[{"local_path": str(current), "filename": "current.png"}],
    )

    prompt = turn.prompt_block()

    assert "当前轮输入对象" in prompt
    assert "https://example.com/a" in prompt
    assert "current.png" in prompt


def test_inject_preserves_latest_message_marker():
    turn = CurrentTurnInput.from_inputs("看这个 https://example.com/a")

    injected = turn.inject_into_message("[最新消息]\n看这个 https://example.com/a")

    assert injected.startswith("[最新消息]\n[当前轮输入对象]")


def test_registry_resolves_recent_url_for_followup_without_new_object():
    registry = SessionObjectRegistry()
    first_turn = CurrentTurnInput.from_inputs("看看这个链接 https://example.com/current")
    registry.register_turn(first_turn)

    followup = CurrentTurnInput.from_inputs("继续分析这个链接")
    followup.with_recent_objects(registry.resolve_for_turn(followup))

    assert followup.reference_urls
    assert followup.reference_urls[0].value == "https://example.com/current"
    prompt = followup.prompt_block()
    assert "最近可引用对象" in prompt
    assert "https://example.com/current" in prompt


def test_recent_url_guard_blocks_unrelated_url_until_recent_url_is_grounded():
    registry = SessionObjectRegistry()
    registry.register_turn(CurrentTurnInput.from_inputs("读这个 https://example.com/current"))
    followup = CurrentTurnInput.from_inputs("继续分析这个链接")
    followup.with_recent_objects(registry.resolve_for_turn(followup))

    blocked = followup.validate_tool_call("web_fetch", {"url": "https://example.com/old"})

    assert blocked is not None
    assert "应使用的 URL" in blocked


def test_recent_image_guard_blocks_unrelated_image(tmp_path: Path):
    current = tmp_path / "current.png"
    old = tmp_path / "old.png"
    registry = SessionObjectRegistry()
    registry.register_turn(
        CurrentTurnInput.from_inputs(
            "看这张图",
            pending_images=[{"local_path": str(current), "filename": "current.png"}],
        )
    )
    followup = CurrentTurnInput.from_inputs("继续分析这张图")
    followup.with_recent_objects(registry.resolve_for_turn(followup))

    blocked = followup.validate_tool_call("view_image", {"path": str(old)})

    assert blocked is not None
    assert "应使用的图片" in blocked


def test_recent_file_guard_blocks_unrelated_file(tmp_path: Path):
    current = tmp_path / "current.pdf"
    old = tmp_path / "old.pdf"
    registry = SessionObjectRegistry()
    registry.register_turn(
        CurrentTurnInput.from_inputs(
            "看这个文件",
            pending_files=[{"local_path": str(current), "filename": "current.pdf"}],
        )
    )
    followup = CurrentTurnInput.from_inputs("继续总结这个文件")
    followup.with_recent_objects(registry.resolve_for_turn(followup))

    blocked = followup.validate_tool_call("read_file", {"path": str(old)})

    assert blocked is not None
    assert "应使用的文件" in blocked


def test_registry_injects_recent_object_when_current_turn_mentions_history():
    registry = SessionObjectRegistry()
    registry.register_turn(CurrentTurnInput.from_inputs("先看这个 https://example.com/old"))

    turn = CurrentTurnInput.from_inputs("把上次那个链接和 https://example.com/new 对比")
    turn.with_recent_objects(registry.resolve_for_turn(turn))

    prompt = turn.prompt_block()
    assert "当前轮输入对象" in prompt
    assert "最近可引用对象" in prompt
    assert "https://example.com/old" in prompt
    assert "https://example.com/new" in prompt


def test_registry_round_trips_serializable_state():
    registry = SessionObjectRegistry()
    registry.register_turn(CurrentTurnInput.from_inputs("看看 https://example.com/a"))

    restored = SessionObjectRegistry.from_dict(registry.to_dict())
    followup = CurrentTurnInput.from_inputs("继续看这个链接")
    followup.with_recent_objects(restored.resolve_for_turn(followup))

    assert followup.reference_urls
    assert followup.reference_urls[0].value == "https://example.com/a"


async def _fake_web_handler(tool_name: str, params: dict) -> str:
    return f"handled {tool_name}: {params.get('url', '')}"


async def test_tool_executor_applies_current_turn_guard_before_handler():
    registry = SystemHandlerRegistry()
    registry.register("web", _fake_web_handler, ["web_fetch"])
    executor = ToolExecutor(registry)
    executor._agent_ref = SimpleNamespace(
        _current_turn_input=CurrentTurnInput.from_inputs("读这个 https://example.com/new")
    )

    # tuple unpack: current-turn grounding gate returns (text, None)
    result, hint = await executor.execute_tool(
        "web_fetch", {"url": "https://example.com/old"}
    )

    assert "正在使用其它 URL" in result
    assert "handled" not in result
    assert hint is None


async def test_tool_executor_policy_path_applies_current_turn_guard():
    registry = SystemHandlerRegistry()
    registry.register("web", _fake_web_handler, ["web_fetch"])
    executor = ToolExecutor(registry)
    executor._agent_ref = SimpleNamespace(
        _current_turn_input=CurrentTurnInput.from_inputs("读这个 https://example.com/new")
    )

    result, hint = await executor.execute_tool_with_policy(
        "web_fetch",
        {"url": "https://example.com/old"},
        SimpleNamespace(metadata={}),
    )

    assert "正在使用其它 URL" in result
    assert "handled" not in result
    assert hint is None
