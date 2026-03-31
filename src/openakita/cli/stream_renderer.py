"""
CLI stream renderer — consumes chat_with_session_stream() events
and renders them in real-time using Rich Live.
"""

from __future__ import annotations

from typing import AsyncIterator

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from ..events import StreamEventType
from ..utils.errors import format_user_friendly_error

E = StreamEventType


async def render_stream(
    event_stream: AsyncIterator[dict],
    console: Console,
    agent_name: str = "OpenAkita",
) -> str:
    """Consume an SSE event stream and render it progressively in the terminal.

    Returns the final assistant text content.
    """
    buffer: list[str] = []
    iteration = 0
    thinking_started = False
    tool_stack: list[str] = []
    has_error = False
    graceful_done = False

    state = {
        "iteration": iteration,
        "thinking_started": thinking_started,
        "has_error": has_error,
        "graceful_done": graceful_done,
    }

    with Live(console=console, refresh_per_second=12, vertical_overflow="visible") as live:
        try:
            async for event in event_stream:
                etype = event.get("type", "")

                if etype == E.SECURITY_CONFIRM:
                    live.stop()
                    _handle_security_confirm_interactive(event, console)
                    live.start()
                    continue

                if etype == E.ASK_USER:
                    live.stop()
                    _handle_ask_user_interactive(event, console)
                    live.start()
                    continue

                _handle_event(
                    etype, event, live, console, agent_name,
                    buffer, tool_stack, state,
                )
                if state["graceful_done"]:
                    break
        except Exception as exc:
            console.print(f"  [red]⚠️ 流式传输中断: {exc}[/red]")
            state["has_error"] = True

    if not state["graceful_done"] and not state["has_error"]:
        console.print("  [yellow]⚠️ 流式响应未正常结束[/yellow]")

    content = "".join(buffer)

    if content and not state["has_error"]:
        console.print()
        console.print(
            Panel(
                Markdown(content),
                title=f"[bold green]{agent_name}[/bold green]",
                border_style="green",
            )
        )

    console.print()
    return content


def _handle_event(
    etype: str,
    event: dict,
    live: Live,
    console: Console,
    agent_name: str,
    buffer: list[str],
    tool_stack: list[str],
    ref: dict,
) -> None:
    """Dispatch a single SSE event to the appropriate Rich renderer."""

    if etype == E.HEARTBEAT:
        return

    if etype == E.ITERATION_START:
        ref["iteration"] = event.get("iteration", ref["iteration"] + 1)
        if ref["iteration"] > 1:
            live.update(Text(f"  ⟳ 轮次 {ref['iteration']}...", style="dim"))
        return

    if etype == E.THINKING_START:
        ref["thinking_started"] = True
        live.update(
            Text(f"  💭 思考中 (轮次 {ref['iteration']})...", style="dim italic")
        )
        return

    if etype == E.THINKING_END:
        duration = (event.get("duration_ms") or 0) / 1000
        if ref["thinking_started"]:
            console.print(f"  [dim]💭 思考完成 ({duration:.1f}s)[/dim]")
        ref["thinking_started"] = False
        return

    if etype == E.THINKING_DELTA:
        return

    if etype == E.CHAIN_TEXT:
        text = event.get("content", event.get("text", ""))
        if text:
            console.print(f"  [dim]⎿ {text[:120]}[/dim]")
        return

    if etype == E.TEXT_DELTA:
        buffer.append(event.get("content", ""))
        content = "".join(buffer)
        live.update(
            Panel(
                Markdown(content),
                title=f"[bold green]{agent_name}[/bold green]",
                border_style="green",
                padding=(0, 1),
            )
        )
        return

    if etype == E.TOOL_CALL_START:
        tool_name = event.get("tool", "unknown")
        tool_stack.append(tool_name)
        console.print(f"  [cyan]⎿ {tool_name}[/cyan] ...")
        return

    if etype == E.TOOL_CALL_END:
        tool_name = event.get("tool", "unknown")
        is_error = event.get("is_error", False)
        skipped = event.get("skipped", False)
        if skipped:
            status = "[yellow]skipped[/yellow]"
        elif is_error:
            status = "[red]failed[/red]"
        else:
            status = "[green]done[/green]"
        console.print(f"  [cyan]⎿ {tool_name}[/cyan] {status}")
        if tool_stack and tool_stack[-1] == tool_name:
            tool_stack.pop()
        return

    if etype == E.CONTEXT_COMPRESSED:
        before = event.get("before_tokens", "?")
        after = event.get("after_tokens", "?")
        console.print(f"  [dim]📦 上下文压缩: {before} → {after} tokens[/dim]")
        return

    if etype == E.TODO_CREATED:
        plan = event.get("plan", {})
        title = plan.get("title", "任务计划")
        steps = plan.get("steps", [])
        console.print(f"  [bold]📋 {title}[/bold]")
        for step in steps[:5]:
            label = step.get("label", step.get("content", ""))
            console.print(f"    □ {label}")
        if len(steps) > 5:
            console.print(f"    [dim]... 共 {len(steps)} 步[/dim]")
        return

    if etype == E.TODO_STEP_UPDATED:
        step_idx = event.get("stepIdx", event.get("step_idx"))
        status = event.get("status", "")
        icon = "✓" if status == "completed" else "⟳" if status == "in_progress" else "□"
        if step_idx is not None:
            console.print(f"    {icon} 步骤 {step_idx + 1}: {status}")
        return

    if etype == E.TODO_COMPLETED:
        console.print("  [green]📋 任务计划已完成[/green]")
        return

    if etype == E.TODO_CANCELLED:
        console.print("  [yellow]📋 任务已取消[/yellow]")
        return

    if etype == E.SECURITY_CONFIRM or etype == E.ASK_USER:
        return

    if etype == E.AGENT_HANDOFF:
        from_agent = event.get("from_agent", "")
        to_agent = event.get("to_agent", "")
        reason = event.get("reason", "")
        console.print(
            f"  [magenta]🤝 Agent 委托: {from_agent} → {to_agent}[/magenta]"
            + (f" ({reason})" if reason else "")
        )
        return

    if etype == E.USER_INSERT:
        content = event.get("content", "")
        if content:
            console.print(f"  [blue]📝 用户插入: {content[:80]}[/blue]")
        return

    if etype == E.ARTIFACT:
        name = event.get("name", "")
        atype = event.get("artifact_type", "")
        console.print(f"  [green]📎 产出: {name} ({atype})[/green]")
        return

    if etype == E.ERROR:
        msg = event.get("message", "")
        friendly = format_user_friendly_error(msg)
        console.print(f"  [red]{friendly}[/red]")
        ref["has_error"] = True
        return

    if etype == E.DONE:
        ref["graceful_done"] = True
        usage = event.get("usage")
        if usage:
            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            ctx = usage.get("context_tokens")
            parts = [f"输入 {inp}", f"输出 {out}"]
            if ctx:
                limit = usage.get("context_limit", 0)
                parts.append(f"上下文 {ctx}/{limit}")
            console.print(f"  [dim]📊 {' | '.join(parts)} tokens[/dim]")
        return


# ── Interactive event handlers (called with Live paused) ──


def _handle_security_confirm_interactive(event: dict, console: Console) -> None:
    """Prompt the user for a security confirmation decision in the terminal."""
    tool = event.get("tool", "")
    reason = event.get("reason", "")
    risk = event.get("risk_level", "medium")
    confirm_id = event.get("id", "")
    needs_sandbox = event.get("needs_sandbox", False)
    args = event.get("args") or {}

    color = "red" if risk.lower() in ("high", "critical") else "yellow"

    info_lines = [f"工具: {tool}", f"原因: {reason}", f"风险等级: {risk}"]
    cmd = args.get("command") or args.get("code") or args.get("url", "")
    if cmd:
        info_lines.append(f"参数: {str(cmd)[:200]}")

    console.print()
    console.print(
        Panel(
            "\n".join(info_lines),
            title="[bold]🔒 安全确认[/bold]",
            border_style=color,
        )
    )

    choices = ["y", "n"]
    hint = "[bold]y[/bold]=允许  [bold]n[/bold]=拒绝"
    if needs_sandbox:
        choices.append("s")
        hint += "  [bold]s[/bold]=沙箱执行"

    decision_str = Prompt.ask(hint, choices=choices, default="n")
    decision_map = {"y": "allow", "n": "deny", "s": "sandbox"}
    decision = decision_map.get(decision_str, "deny")

    try:
        from ..core.policy import get_policy_engine

        engine = get_policy_engine()
        found = engine.resolve_ui_confirm(confirm_id, decision)
        if found:
            label = {"allow": "✅ 已允许", "deny": "❌ 已拒绝", "sandbox": "🔒 沙箱执行"}[decision]
            console.print(f"  {label}")
        else:
            console.print(f"  [yellow]⚠️ 确认项已过期或不存在 (id={confirm_id[:8]}…)[/yellow]")
    except Exception as exc:
        console.print(f"  [red]确认处理失败: {exc}[/red]")


def _handle_ask_user_interactive(event: dict, console: Console) -> None:
    """Display ask_user prompt with structured options for CLI users."""
    question = event.get("question", "")
    options = event.get("options", [])

    console.print()
    console.print(
        Panel(question, title="[bold]❓ 需要你的回答[/bold]", border_style="blue")
    )

    if options:
        for i, opt in enumerate(options, 1):
            label = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)
            console.print(f"  [cyan][{i}][/cyan] {label}")
        console.print()
    console.print("  [dim]请在下次输入中回答（输入序号或直接输入文字）[/dim]")
