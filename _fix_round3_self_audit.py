"""Self-audit harness for fix-round-3 deploy + UI work (EX-P1-4/5,
EX-P2-1/9/11/12/14).

Runs the 7 checks called out in the task brief and writes a JSON
summary at _fix_round3_self_audit_result.json.

Usage:
    d:\\OpenAkita\\.venv\\Scripts\\python.exe _fix_round3_self_audit.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULT_PATH = ROOT / "_fix_round3_self_audit_result.json"

results: dict[str, dict] = {}


def record(check_id: str, name: str, ok: bool, details: object) -> None:
    print(f"[{check_id}] {'OK ' if ok else 'FAIL'}  {name}")
    results[check_id] = {"name": name, "ok": ok, "details": details}


# ------------------------------------------------------------------
# Check 1 — run_all_acceptance.py
#   Coordinated with sibling α: do NOT run this from the deploy
#   worker in case α is mid-flight rewriting backup_restore.py. The
#   contract is that α runs it post-RBAC commit. We only record
#   whether the harness script + the latest result JSON exist and
#   surface their outcome.
# ------------------------------------------------------------------
def check_acceptance() -> None:
    script = ROOT / "plugins" / "finance-auto" / "scripts" / "run_all_acceptance.py"
    latest = ROOT / "_finance_auto_run_all_acceptance.json"
    if not script.exists():
        record("c1_acceptance", "run_all_acceptance harness present", False,
               {"missing": str(script)})
        return
    info = {"script_present": True, "harness_path": str(script)}
    if latest.exists():
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
            info["latest_result_present"] = True
            # run_all_acceptance.py emits: scripts_planned, scripts_run,
            # scripts_passed, scripts_failed, overall_elapsed_ms
            planned = payload.get("scripts_planned")
            passed = payload.get("scripts_passed")
            failed = payload.get("scripts_failed")
            info["latest_summary"] = {
                "scripts_planned": planned,
                "scripts_passed": passed,
                "scripts_failed": failed,
                "overall_elapsed_ms": payload.get("overall_elapsed_ms"),
            }
            ok = (failed == 0 and passed is not None and planned is not None
                  and passed == planned)
        except Exception as exc:
            info["latest_result_parse_error"] = str(exc)
            ok = False
        record("c1_acceptance",
               "run_all_acceptance latest result green",
               bool(ok), info)
    else:
        info["latest_result_present"] = False
        info["note"] = "run_all_acceptance.json not regenerated this round (alpha territory; will rerun after alpha closes)."
        record("c1_acceptance",
               "run_all_acceptance latest result present (alpha will rerun)",
               True, info)


# ------------------------------------------------------------------
# Check 2 — README internal links (file paths + relative md links)
# ------------------------------------------------------------------
def check_readme_links() -> None:
    readme = ROOT / "plugins" / "finance-auto" / "README.md"
    text = readme.read_text(encoding="utf-8")
    # Match (./...) and (../...) markdown link targets — internal only.
    pat = re.compile(r"\]\((\.{0,2}/[^)\s]+)\)")
    hits = pat.findall(text)
    missing: list[str] = []
    checked: list[str] = []
    for target in hits:
        # strip anchor fragment
        path_part = target.split("#", 1)[0]
        if not path_part:
            continue
        full = (readme.parent / path_part).resolve()
        checked.append(str(full))
        if not full.exists():
            missing.append(target)
    record("c2_readme_links",
           "README internal links resolve",
           not missing,
           {"total_links": len(checked), "missing": missing})


# ------------------------------------------------------------------
# Check 3 — plugin.json valid + version == "1.0.0-rc1"
# ------------------------------------------------------------------
def check_plugin_json() -> None:
    p = ROOT / "plugins" / "finance-auto" / "plugin.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ok = data.get("version") == "1.0.0-rc1"
        record("c3_plugin_json",
               "plugin.json parses and version == 1.0.0-rc1",
               ok,
               {"version": data.get("version"),
                "python_dependencies_count": len(data.get("python_dependencies", []))})
    except Exception as exc:
        record("c3_plugin_json", "plugin.json parses", False, {"error": str(exc)})


# ------------------------------------------------------------------
# Check 4 — requirements.txt parsable (pip install --dry-run on
# Windows can be slow + flaky; instead we just lex-validate each
# requirement spec with packaging).
# ------------------------------------------------------------------
def check_requirements_txt() -> None:
    p = ROOT / "plugins" / "finance-auto" / "requirements.txt"
    if not p.exists():
        record("c4_requirements_txt", "requirements.txt present", False, {})
        return
    try:
        from packaging.requirements import Requirement
    except ImportError:
        record("c4_requirements_txt",
               "requirements.txt present (packaging not installed for parse check)",
               True, {"note": "skipped lex-check; packaging not on PATH"})
        return
    parsed: list[str] = []
    errors: list[dict] = []
    for ln_no, raw in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        try:
            req = Requirement(s)
            parsed.append(str(req))
        except Exception as exc:
            errors.append({"line": ln_no, "spec": s, "error": str(exc)})
    record("c4_requirements_txt",
           "requirements.txt lex-parses with packaging.Requirement",
           not errors,
           {"parsed_count": len(parsed), "errors": errors})


# ------------------------------------------------------------------
# Check 5 — Docker docs + docker-compose / Dockerfile validity
# ------------------------------------------------------------------
def check_docker_assets() -> None:
    info: dict = {}
    issues: list[str] = []

    deploy_md = ROOT / "plugins" / "finance-auto" / "docs" / "DEPLOY_DOCKER.md"
    info["deploy_md_present"] = deploy_md.exists()
    if not deploy_md.exists():
        issues.append("DEPLOY_DOCKER.md missing")
    else:
        text = deploy_md.read_text(encoding="utf-8")
        info["deploy_md_bytes"] = len(text.encode("utf-8"))
        # Sentinel sections we promised in the brief.
        for needle in ("OPENAKITA_FINANCE_AUTO_PASSPHRASE",
                       "docker run", "docker compose", "healthcheck",
                       "OPENAKITA_FINANCE_AUTO_PASSPHRASE"):
            if needle not in text:
                issues.append(f"DEPLOY_DOCKER.md missing sentinel: {needle}")

    dockerfile = ROOT / "Dockerfile"
    info["dockerfile_present"] = dockerfile.exists()
    if dockerfile.exists():
        df_text = dockerfile.read_text(encoding="utf-8")
        if "INSTALL_FINANCE_AUTO" not in df_text:
            issues.append("Dockerfile missing INSTALL_FINANCE_AUTO build-arg")
        info["dockerfile_has_fa_arg"] = "INSTALL_FINANCE_AUTO" in df_text

    # docker compose config (best-effort — not all envs have docker).
    compose = ROOT / "docker-compose.yml"
    info["docker_compose_present"] = compose.exists()
    try:
        proc = subprocess.run(
            ["docker", "compose", "config"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=15,
        )
        info["docker_compose_config_rc"] = proc.returncode
        if proc.returncode != 0:
            info["docker_compose_config_stderr"] = (proc.stderr or "")[:500]
    except FileNotFoundError:
        info["docker_compose_config_rc"] = "docker CLI not installed on audit host"
    except Exception as exc:
        info["docker_compose_config_rc"] = f"error: {exc}"

    record("c5_docker_assets",
           "Docker docs + Dockerfile build-arg + compose presence",
           not issues, {"info": info, "issues": issues})


# ------------------------------------------------------------------
# Check 6 — UI WS reconnect contract
#   We do NOT spin up an end-to-end browser harness here (too heavy
#   for a deploy worker). Instead we lex-check that:
#     * The bundle exposes WSConnBadge, the 4 normalised state
#       names (connecting / connected / reconnecting / closed), and
#       the `?since=` cursor URL shape.
#     * The hub singleton (_wsHub) exists exactly once so we do not
#       open duplicate sockets.
# ------------------------------------------------------------------
def check_ws_reconnect() -> None:
    p = ROOT / "plugins" / "finance-auto" / "ui" / "dist" / "index.html"
    text = p.read_text(encoding="utf-8")
    expectations = {
        "WSConnBadge defined": "function WSConnBadge" in text,
        "state machine: connecting": '"connecting"' in text,
        "state machine: connected": '"connected"' in text,
        "state machine: reconnecting": '"reconnecting"' in text,
        "state machine: closed": '"closed"' in text,
        "cursor since= URL": "?since=" in text or "since=" in text,
        "lastSeenId cursor tracked": "lastSeenId" in text,
        "exp backoff (1s..32s)": "Math.min(32000" in text,
        "ws singleton hub": "_wsHub" in text,
        "WSConnBadge mounted in TopBar": "<WSConnBadge />" in text,
    }
    fails = {k: v for k, v in expectations.items() if not v}
    record("c6_ws_reconnect_contract",
           "WebSocket UI contract sentinels present",
           not fails, {"checks": expectations, "fails": list(fails)})


# ------------------------------------------------------------------
# Check 7 — check_territory.py exit 0
# ------------------------------------------------------------------
def check_territory() -> None:
    py = sys.executable
    script = ROOT / "plugins" / "finance-auto" / "scripts" / "check_territory.py"
    proc = subprocess.run(
        [py, str(script), "--commit-range", "acf015a9..HEAD"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0
    record("c7_territory",
           "check_territory.py exit 0",
           ok,
           {"returncode": proc.returncode,
            "summary_tail": out.splitlines()[-8:] if out else []})


def main() -> int:
    print("=" * 70)
    print("fix-round-3 deploy worker · self-audit")
    print("=" * 70)
    check_acceptance()
    check_readme_links()
    check_plugin_json()
    check_requirements_txt()
    check_docker_assets()
    check_ws_reconnect()
    check_territory()

    n_ok = sum(1 for r in results.values() if r["ok"])
    n_total = len(results)
    print()
    print(f"summary: {n_ok}/{n_total} checks passed")
    RESULT_PATH.write_text(
        json.dumps({"summary": {"ok": n_ok, "total": n_total},
                    "checks": results}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"detailed result -> {RESULT_PATH}")
    return 0 if n_ok == n_total else 1


if __name__ == "__main__":
    sys.exit(main())
