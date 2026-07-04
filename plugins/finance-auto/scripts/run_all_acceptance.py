"""Aggregate runner for every finance-auto acceptance script.

Round-2 optimisation #2 (audit §11 / §8 item 2): the M3-closing
acceptance script (``m3_closing_acceptance.py``) was an isolated entry
point — no CI job and no parent gate invoked it.  This module turns the
10 acceptance scripts into a single CI hook so a single ``exit 0`` /
``exit 1`` tells you whether the whole plugin is shippable.

What this runs (in order, all in-process via subprocess)::

    1.  m1_w2_acceptance.py
    2.  m1_w3_acceptance.py
    3.  m2_ai_acceptance.py
    4.  m2_biz_acceptance.py
    5.  m2_closing_acceptance.py     --skip-regression
    6.  m3_raw_ai_acceptance.py
    7.  m3_infra_acceptance.py
    8.  m3_notes_peer_acceptance.py  --skip-regression
    9.  m3_ui_acceptance.py
    10. m3_closing_acceptance.py     --skip-regression

The ``--skip-regression`` flag is passed to every "closing" / "notes_peer"
script that internally re-spawns its sibling scripts; without it we
would explode into an O(N²) run and double-count failures.

Each script is given ``--timeout`` seconds (default 120s, override via
``--per-script-timeout``).  Output is collected into a structured
record::

    {"script_name": "m1_w2_acceptance.py",
     "exit_code": 0,
     "elapsed_ms": 2317,
     "natural_exit": true,
     "stdout_tail": "...",
     "stderr_tail": "..."}

At the end the runner prints a one-line summary table and writes the
full JSON to ``--json`` (default
``_finance_auto_run_all_acceptance.json`` at repo root).  Exit code:

* 0 — every script returned 0 within its timeout
* 1 — at least one script failed, timed out, or did not exit naturally

CI integration
==============
This script is the recommended hook for any future CI gate.  See
``plugins/finance-auto/CHANGELOG.md`` and ``plugins/finance-auto/
CONTRIBUTING.md`` for usage.  At the moment ``.github/workflows/ci.yml``
does NOT call us (it would require modifying ``apps/setup-center``
territory) — wiring the GitHub Action is tracked as
``TODO: 接入 CI`` and is the only thing standing between the current
"run it locally" workflow and "fail-fast on every PR".

Usage::

    d:\\OpenAkita\\.venv\\Scripts\\python.exe ^
        plugins/finance-auto/scripts/run_all_acceptance.py ^
        [--per-script-timeout 120] [--json <path>] [--fail-fast]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS_DIR.parent
REPO_ROOT = PLUGIN_ROOT.parent.parent

# (script_filename, extra_argv) — order matters: M1 → M2 → M3 → closing.
ACCEPTANCE_SCRIPTS: list[tuple[str, list[str]]] = [
    ("m1_w2_acceptance.py", []),
    ("m1_w3_acceptance.py", []),
    ("m2_ai_acceptance.py", []),
    ("m2_biz_acceptance.py", []),
    ("m2_closing_acceptance.py", ["--skip-regression"]),
    ("m3_raw_ai_acceptance.py", []),
    ("m3_infra_acceptance.py", []),
    ("m3_notes_peer_acceptance.py", ["--skip-regression"]),
    ("m3_ui_acceptance.py", []),
    ("m3_closing_acceptance.py", ["--skip-regression"]),
]

_TAIL_LINES = 30


def _tail(text: str, n: int = _TAIL_LINES) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:]) if len(lines) > n else text


def _run_one(
    script: str,
    extra_argv: list[str],
    timeout_s: int,
    python_exe: str,
) -> dict:
    cmd = [python_exe, str(SCRIPTS_DIR / script), *extra_argv]
    started = time.perf_counter()
    timed_out = False
    exit_code: int
    # IMPORTANT: do NOT use ``capture_output=True`` here.  Several
    # acceptance scripts print enough output to fill the OS pipe buffer
    # (typically 64 KB on Windows), at which point the child blocks on
    # write() and never reaches its os._exit() call -- subprocess.run
    # then waits forever for a process that is structurally unable to
    # exit.  We bind stdout/stderr to real temp files so the OS never
    # back-pressures the child.
    stdout_path = Path(tempfile.mkstemp(prefix="run_all_stdout_", suffix=".log")[1])
    stderr_path = Path(tempfile.mkstemp(prefix="run_all_stderr_", suffix=".log")[1])
    try:
        with stdout_path.open("wb") as out_fp, stderr_path.open("wb") as err_fp:
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=out_fp,
                    stderr=err_fp,
                    timeout=timeout_s,
                    # Run from REPO_ROOT so any relative ``--json``
                    # defaults land next to the other acceptance JSON
                    # artefacts.
                    cwd=str(REPO_ROOT),
                    check=False,
                    # Do NOT force PYTHONIOENCODING: a couple of legacy
                    # acceptance scripts (m3_infra_acceptance.py) spawn
                    # their own children with the OS-default decoder
                    # (cp936 on Windows); flipping the inner child to
                    # UTF-8 then makes the inner parent's gbk reader
                    # crash.  Letting the locale stay native keeps the
                    # existing scripts' subprocess plumbing happy; we
                    # decode the captured bytes as UTF-8 with replace
                    # below, which tolerates either encoding.
                    env=os.environ.copy(),
                )
                exit_code = proc.returncode
                natural_exit = True
            except subprocess.TimeoutExpired:
                exit_code = -1
                timed_out = True
                natural_exit = False
        stdout = stdout_path.read_bytes().decode("utf-8", errors="replace")
        stderr = stderr_path.read_bytes().decode("utf-8", errors="replace")
    finally:
        for p in (stdout_path, stderr_path):
            try:
                p.unlink()
            except OSError:
                pass
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return {
        "script_name": script,
        "extra_argv": extra_argv,
        "exit_code": exit_code,
        "elapsed_ms": elapsed_ms,
        "natural_exit": natural_exit,
        "timed_out": timed_out,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
    }


def _format_table(records: list[dict]) -> str:
    headers = ("script", "status", "exit", "elapsed_ms", "natural")
    rows = []
    for r in records:
        status = "PASS" if r["exit_code"] == 0 and not r["timed_out"] else (
            "TIMEOUT" if r["timed_out"] else "FAIL"
        )
        rows.append(
            (
                r["script_name"],
                status,
                str(r["exit_code"]),
                str(r["elapsed_ms"]),
                "yes" if r["natural_exit"] else "no",
            )
        )
    widths = [
        max(len(h), max(len(row[i]) for row in rows)) for i, h in enumerate(headers)
    ]
    sep = "  ".join("-" * w for w in widths)
    head = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    out = [head, sep]
    for row in rows:
        out.append("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--per-script-timeout", type=int, default=120,
        help="Per-script timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--json", dest="json_out", type=Path,
        default=REPO_ROOT / "_finance_auto_run_all_acceptance.json",
        help="Path to write the aggregated JSON report",
    )
    parser.add_argument(
        "--fail-fast", action="store_true",
        help="Stop at the first failing script instead of running everything",
    )
    parser.add_argument(
        "--python", default=sys.executable,
        help="Python interpreter to invoke each script with",
    )
    parser.add_argument(
        "--only", nargs="*", default=None,
        help=(
            "Optional subset of script filenames (without path) to run; "
            "useful for debugging a single acceptance script"
        ),
    )
    args = parser.parse_args()

    scripts = ACCEPTANCE_SCRIPTS
    if args.only:
        wanted = set(args.only)
        scripts = [(s, a) for (s, a) in ACCEPTANCE_SCRIPTS if s in wanted]
        missing = wanted - {s for s, _ in ACCEPTANCE_SCRIPTS}
        if missing:
            print(
                f"WARNING: --only listed unknown scripts: {sorted(missing)}",
                file=sys.stderr,
            )

    print(f"run_all_acceptance: {len(scripts)} scripts, "
          f"per-script timeout={args.per_script_timeout}s, "
          f"python={args.python}")
    print(f"run_all_acceptance: scripts dir = {SCRIPTS_DIR}")
    print(f"run_all_acceptance: cwd        = {REPO_ROOT}")

    records: list[dict] = []
    overall_started = time.perf_counter()
    for idx, (script, extra) in enumerate(scripts, 1):
        argv_str = " " + " ".join(extra) if extra else ""
        print(f"\n[{idx}/{len(scripts)}] {script}{argv_str} ...", flush=True)
        rec = _run_one(
            script=script,
            extra_argv=extra,
            timeout_s=args.per_script_timeout,
            python_exe=args.python,
        )
        records.append(rec)
        status_tag = "PASS" if rec["exit_code"] == 0 and not rec["timed_out"] else (
            "TIMEOUT" if rec["timed_out"] else "FAIL"
        )
        print(
            f"    -> {status_tag}  exit={rec['exit_code']}  "
            f"elapsed_ms={rec['elapsed_ms']}  natural_exit={rec['natural_exit']}",
            flush=True,
        )
        if status_tag != "PASS" and args.fail_fast:
            print(
                f"--fail-fast: stopping after {script} failed",
                file=sys.stderr,
                flush=True,
            )
            break

    overall_elapsed_ms = int((time.perf_counter() - overall_started) * 1000)
    failed = [r for r in records if r["exit_code"] != 0 or r["timed_out"]]
    summary = {
        "scripts_planned": len(scripts),
        "scripts_run": len(records),
        "scripts_passed": len(records) - len(failed),
        "scripts_failed": len(failed),
        "overall_elapsed_ms": overall_elapsed_ms,
        "per_script_timeout_s": args.per_script_timeout,
        "fail_fast": args.fail_fast,
        "records": records,
    }

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 60)
    print("run_all_acceptance summary")
    print("=" * 60)
    print(_format_table(records))
    print(
        f"\nTotal: {summary['scripts_passed']}/{summary['scripts_run']} passed, "
        f"overall {overall_elapsed_ms} ms"
    )
    print(f"JSON written to: {args.json_out}")

    if failed:
        print("\nFAIL -- at least one acceptance script did not pass:")
        for r in failed:
            tag = "TIMEOUT" if r["timed_out"] else "FAIL"
            print(f"  - {r['script_name']}: {tag} (exit={r['exit_code']})")
            if r["stderr_tail"]:
                print("    stderr tail:")
                for line in r["stderr_tail"].splitlines()[-5:]:
                    print(f"      {line}")
        return 1

    print("\nPASS -- every acceptance script returned 0")
    return 0


def _force_utf8_stdio() -> None:
    """Best-effort: switch stdout/stderr to UTF-8 so we can print Chinese
    summaries safely on Windows PowerShell (cp936)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover -- non-TTY or older Python
            pass


if __name__ == "__main__":
    _force_utf8_stdio()
    rc = main()
    # Mirror the os._exit dance the closing acceptance scripts already use
    # so any non-daemon ASGI threads left around by child processes (which
    # we already inherited via subprocess.run) cannot wedge the interpreter
    # on the way out.  All output has already been written.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc)
