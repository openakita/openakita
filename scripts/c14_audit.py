"""C14 audit — verify all five phases of the Headless 入口统一 milestone.

Phases
======

A. ``core/policy_v2/entry_point.py`` exists and exports a usable classifier.
B. R4-8: ``main.py::main`` consults the classifier for non-TTY stdin;
   ``main.py::run`` installs ``is_unattended=True`` ContextVar.
C. R4-7: ``channels/gateway.py::process_message`` calls
   ``apply_classification_to_session`` after ``session_manager.get_session``.
D. R4-6: ``api/routes/chat.py`` registers ``POST /api/chat/sync`` and
   catches ``DeferredApprovalRequired`` → 202 with ``approval_id``.
E. R4-5: ``cli/stream_renderer.py::_handle_security_confirm_interactive``
   short-circuits when ``sys.stdin.isatty()`` is False.
F. Regression: prior milestones unchanged (C12+C9c / C13 audit scripts).
G. Tests: new C14 test file present + neighbor tests still green.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "openakita"


def _read(rel_path: str) -> str:
    p = REPO / rel_path
    if not p.exists():
        raise FileNotFoundError(rel_path)
    return p.read_text(encoding="utf-8")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    raise SystemExit(1)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# A. classifier module
# ---------------------------------------------------------------------------


def audit_a_classifier_module() -> None:
    section("A. entry_point classifier module")

    src = _read("src/openakita/core/policy_v2/entry_point.py")
    for symbol in (
        "def classify_entry(",
        "def apply_classification_to_session(",
        "EntryClassification",
        "IM_WEBHOOK_CHANNELS",
        "SSE_INTERACTIVE_CHANNELS",
        '"telegram"',
        '"feishu"',
        '"dingtalk"',
        '"api-sync"',
        "force_unattended",
    ):
        if symbol not in src:
            fail(f"entry_point.py missing {symbol!r}")
    ok("entry_point.py exports classify_entry / apply_classification_to_session / 5 channels")

    init_src = _read("src/openakita/core/policy_v2/__init__.py")
    for symbol in (
        "from .entry_point import",
        "classify_entry",
        "apply_classification_to_session",
        "EntryClassification",
    ):
        if symbol not in init_src:
            fail(f"policy_v2/__init__.py missing re-export of {symbol!r}")
    ok("policy_v2/__init__.py re-exports classifier API")


# ---------------------------------------------------------------------------
# B. main.py R4-8
# ---------------------------------------------------------------------------


def audit_b_main_isatty_and_run() -> None:
    section("B. R4-8: main.py stdin isatty + `openakita run` unattended")

    src = _read("src/openakita/main.py")

    # main callback gates run_interactive on non-TTY
    if "classify_entry(\"cli\")" not in src:
        fail("main.py does not consult classify_entry('cli') before run_interactive")
    if "raise typer.Exit(1)" not in src:
        fail("main.py does not exit on non-TTY interactive attempt")
    ok("main() callback gates run_interactive via classifier")

    # run command sets up PolicyContext with is_unattended=True
    run_match = re.search(
        r"@app\.command\(\)\s*\ndef run\(.*?\bdef status\b",
        src,
        re.DOTALL,
    )
    if run_match is None:
        fail("run command body not found")
    run_body = run_match.group(0)
    for required in (
        "build_policy_context",
        "is_unattended=True",
        "set_current_context",
        "reset_current_context",
        "channel=\"cli\"",
    ):
        if required not in run_body:
            fail(f"run command missing {required!r}")
    ok("`openakita run` installs is_unattended=True PolicyContext (set+reset symmetric)")


# ---------------------------------------------------------------------------
# C. gateway.py R4-7
# ---------------------------------------------------------------------------


def audit_c_gateway_apply_classifier() -> None:
    section("C. R4-7: gateway.py IM webhook marks session unattended")

    src = _read("src/openakita/channels/gateway.py")

    # Find the process_message block where session is fetched
    block = re.search(
        r"4\.0\.2.*?apply_classification_to_session\(\s*session,\s*classify_entry\(message\.channel\)",
        src,
        re.DOTALL,
    )
    if block is None:
        fail("gateway.process_message does not apply classifier after get_session")
    ok("gateway.process_message applies entry classifier to IM/webhook session")

    # Import is local (lazy) — fine, just verify it's there
    if "from ..core.policy_v2 import" not in src or "apply_classification_to_session" not in src:
        fail("gateway.py missing policy_v2 import for classifier")
    ok("classifier import present in gateway.py")


# ---------------------------------------------------------------------------
# D. /api/chat/sync R4-6
# ---------------------------------------------------------------------------


def audit_d_chat_sync_endpoint() -> None:
    section("D. R4-6: /api/chat/sync endpoint")

    src = _read("src/openakita/api/routes/chat.py")

    if '@router.post("/api/chat/sync")' not in src:
        fail("/api/chat/sync route not registered")
    ok("/api/chat/sync route registered")

    # Capture the function body
    body_match = re.search(
        r'@router\.post\("/api/chat/sync"\).*?(?=@router\.|\Z)',
        src,
        re.DOTALL,
    )
    body = body_match.group(0) if body_match else ""
    for required in (
        "DeferredApprovalRequired",
        "channel=\"api-sync\"",
        "apply_classification_to_session",
        "status_code=202",
        '"approval_id"',
        '"approval_url"',
        '"resolve_url"',
        '"unattended_strategy"',
        "Location",
    ):
        if required not in body:
            fail(f"/api/chat/sync body missing {required!r}")
    ok("/api/chat/sync catches DeferredApprovalRequired → 202 + approval_url + Location header")

    # Belt-and-suspenders: also handles empty message and no endpoint
    for required in ('"empty_message"', '"no_chat_endpoints_configured"'):
        if required not in body:
            fail(f"/api/chat/sync missing validation: {required!r}")
    ok("/api/chat/sync validates empty message + endpoint availability")


# ---------------------------------------------------------------------------
# E. stream_renderer R4-5
# ---------------------------------------------------------------------------


def audit_e_stream_renderer_isatty_gate() -> None:
    section("E. R4-5: stream_renderer CLI confirm isatty gating")

    src = _read("src/openakita/cli/stream_renderer.py")

    # Find the function
    func_match = re.search(
        r"def _handle_security_confirm_interactive\(.*?\n(?=def |\Z)",
        src,
        re.DOTALL,
    )
    if func_match is None:
        fail("_handle_security_confirm_interactive not found")
    body = func_match.group(0)

    for required in (
        "stdin.isatty",
        "C14 / R4-5",
        "非交互终端",
    ):
        if required not in body:
            fail(f"_handle_security_confirm_interactive missing {required!r}")

    # The isatty check must happen BEFORE the actual Prompt.ask call site
    # (otherwise it hangs on non-TTY). Use re.search to find the function-call
    # form ``Prompt.ask(`` rather than docstring mentions like
    # `````Prompt.ask````` which would give a false positive.
    isatty_pos = body.find("isatty")
    prompt_call_match = re.search(r"\bPrompt\.ask\(", body)
    if isatty_pos == -1 or prompt_call_match is None:
        fail("isatty check or Prompt.ask call not found in stream_renderer")
    if isatty_pos > prompt_call_match.start():
        fail(
            "isatty check must precede Prompt.ask( CALL in stream_renderer "
            f"(isatty@{isatty_pos}, Prompt.ask(@{prompt_call_match.start()})"
        )
    ok("stream_renderer security_confirm short-circuits on non-TTY BEFORE Prompt.ask(")


# ---------------------------------------------------------------------------
# F. Prior-milestone regression smoke
# ---------------------------------------------------------------------------


def audit_f_prior_milestone_regression() -> None:
    section("F. Prior-milestone regression smoke (C12+C9c + C13)")

    scripts = [REPO / "scripts" / "c12_c9c_audit.py", REPO / "scripts" / "c13_audit.py"]
    for script in scripts:
        if not script.exists():
            print(f"  ⚠ {script.name} not found (skipping)")
            continue
        r = subprocess.run(
            [sys.executable, str(script)],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0:
            print(r.stdout)
            print(r.stderr)
            fail(f"{script.name} regression failed")
        ok(f"{script.name} still green (no regression)")


# ---------------------------------------------------------------------------
# G. Tests presence + neighbor green
# ---------------------------------------------------------------------------


def audit_g_tests_present_and_green() -> None:
    section("G. Tests presence + green")

    if not (REPO / "tests" / "unit" / "test_policy_v2_c14_entry_point.py").exists():
        fail("tests/unit/test_policy_v2_c14_entry_point.py missing")
    ok("C14 unit test file present")

    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_policy_v2_c14_entry_point.py",
            "tests/integration/test_api_chat.py::TestChatSyncEndpoint",
            "-q",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        fail("C14 test suite failed")
    last = r.stdout.strip().splitlines()[-1]
    ok(f"C14 + /api/chat/sync tests green: {last}")


def main() -> None:
    audit_a_classifier_module()
    audit_b_main_isatty_and_run()
    audit_c_gateway_apply_classifier()
    audit_d_chat_sync_endpoint()
    audit_e_stream_renderer_isatty_gate()
    audit_f_prior_milestone_regression()
    audit_g_tests_present_and_green()
    print("\nALL C14 AUDIT CHECKS PASSED (A–G)")


if __name__ == "__main__":
    main()
