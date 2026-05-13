"""C10 audit: Hook 来源分层 + Trusted Tool Policy + plugin manifest 桥接.

审计 5 个维度（与 docs §3.2 R2-12 + R5-7 对齐）：

D1 — Skill 层 lookup wire-up 完整性：
    - SkillMetadata / SkillEntry 持有 ``approval_class`` 字段
    - SkillRegistry.get_tool_class 存在并签名正确
    - rebuild_engine_v2 接受 ``skill_lookup`` kwarg
    - agent.py 在 _initialize 末段调用 rebuild_engine_v2(skill_lookup=...)

D2 — Plugin 层 lookup + mutates_params：
    - PluginManifest 有 ``tool_classes`` + ``mutates_params`` 字段
    - PluginManager.get_tool_class / plugin_allows_param_mutation 存在
    - rebuild_engine_v2 接受 ``plugin_lookup`` kwarg + agent.py wire

D3 — MCP 层 lookup：
    - MCPTool dataclass 有 ``annotations`` 字段
    - MCPClient.get_tool_class 存在 + format_tool_name 与 schema 一致
    - rebuild_engine_v2 接受 ``mcp_lookup`` kwarg + agent.py wire

D4 — mutates_params 强制审计（R2-12）：
    - param_mutation_audit.py 模块存在 + ParamMutationAuditor 类
    - tool_executor._dispatch_hook 对 ``on_before_tool_use`` 走专门路径
    - jsonl 默认路径为 ``data/audit/plugin_param_modifications.jsonl``

D5 — R5-7 plugin/PolicyEngine 解耦锁死：
    - plugins/api.py 不依赖 core.policy（已删）也不直接 import
      policy_v2.PolicyEngineV2 / get_engine_v2 等内核类
    - plugins/manager.py 对 PolicyEngine 的访问只走 hook 间接调用
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "openakita"


def _read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def _read_repo(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _fail(dim: str, msg: str) -> None:
    print(f"[FAIL] {dim}: {msg}")
    sys.exit(1)


def _ok(dim: str, msg: str) -> None:
    print(f"[OK]   {dim}: {msg}")


# --------------------------------------------------------------------------- D1

def d1_skill_lookup() -> None:
    parser_src = _read("skills/parser.py")
    if "approval_class: str | None = None" not in parser_src:
        _fail("D1", "SkillMetadata.approval_class field missing")
    if "_parse_approval_class" not in parser_src:
        _fail("D1", "parser._parse_approval_class missing")
    if "deprecated 'risk_class'" not in parser_src:
        _fail("D1", "risk_class deprecation alias not implemented")

    registry_src = _read("skills/registry.py")
    if "approval_class: str | None = None" not in registry_src:
        _fail("D1", "SkillEntry.approval_class field missing")
    if "def get_tool_class(" not in registry_src:
        _fail("D1", "SkillRegistry.get_tool_class missing")
    if "DecisionSource.SKILL_METADATA" not in registry_src:
        _fail("D1", "SkillRegistry.get_tool_class must return SKILL_METADATA")
    if "def get_exposed_tool_name" not in registry_src:
        _fail("D1", "SkillEntry.get_exposed_tool_name (centralised tool-name rule) missing")

    ge_src = _read("core/policy_v2/global_engine.py")
    if "skill_lookup: SkillLookup | None = None" not in ge_src:
        _fail("D1", "rebuild_engine_v2 missing skill_lookup kwarg")
    if "_skill_lookup: SkillLookup | None = None" not in ge_src:
        _fail("D1", "_skill_lookup module cache missing")

    agent_src = _read("core/agent.py")
    if "skill_lookup=self.skill_registry.get_tool_class" not in agent_src:
        _fail("D1", "agent.py late-stage rebuild missing skill_lookup wire")

    _ok("D1", "Skill 层 lookup wire-up 完整")


# --------------------------------------------------------------------------- D2

def d2_plugin_lookup() -> None:
    manifest_src = _read("plugins/manifest.py")
    if "tool_classes: dict[str, str]" not in manifest_src:
        _fail("D2", "PluginManifest.tool_classes field missing")
    if "mutates_params: list[str]" not in manifest_src:
        _fail("D2", "PluginManifest.mutates_params field missing")
    if "_normalize_tool_classes" not in manifest_src:
        _fail("D2", "tool_classes validator missing")
    if "_normalize_mutates_params" not in manifest_src:
        _fail("D2", "mutates_params validator missing")

    manager_src = _read("plugins/manager.py")
    if "def get_tool_class(" not in manager_src:
        _fail("D2", "PluginManager.get_tool_class missing")
    if "def plugin_allows_param_mutation" not in manager_src:
        _fail("D2", "PluginManager.plugin_allows_param_mutation missing")
    if "DecisionSource.PLUGIN_PREFIX" not in manager_src:
        _fail("D2", "PluginManager.get_tool_class must return PLUGIN_PREFIX source")

    ge_src = _read("core/policy_v2/global_engine.py")
    if "plugin_lookup: PluginLookup | None = None" not in ge_src:
        _fail("D2", "rebuild_engine_v2 missing plugin_lookup kwarg")

    agent_src = _read("core/agent.py")
    if "plugin_lookup=" not in agent_src:
        _fail("D2", "agent.py late-stage rebuild missing plugin_lookup wire")

    _ok("D2", "Plugin 层 lookup + mutates_params 字段就位")


# --------------------------------------------------------------------------- D3

def d3_mcp_lookup() -> None:
    mcp_src = _read("tools/mcp.py")
    if "annotations: dict = field" not in mcp_src:
        _fail("D3", "MCPTool.annotations field missing")
    if "def get_tool_class(" not in mcp_src:
        _fail("D3", "MCPClient.get_tool_class missing")
    if "def _format_tool_name" not in mcp_src:
        _fail("D3", "MCPClient._format_tool_name (single source of tool-name rule) missing")
    if "DecisionSource.MCP_ANNOTATION" not in mcp_src:
        _fail("D3", "MCPClient.get_tool_class must return MCP_ANNOTATION source")
    if "destructiveHint" not in mcp_src or "openWorldHint" not in mcp_src:
        _fail("D3", "MCPClient.get_tool_class must honour MCP 2024-11 hints")
    # Single source of truth: the formatter must be used by both
    # get_tool_schemas and get_tool_class
    if mcp_src.count("self._format_tool_name") < 2 and mcp_src.count(
        "_format_tool_name"
    ) < 2:
        _fail("D3", "_format_tool_name must be used by both schemas + lookup")

    ge_src = _read("core/policy_v2/global_engine.py")
    if "mcp_lookup: McpLookup | None = None" not in ge_src:
        _fail("D3", "rebuild_engine_v2 missing mcp_lookup kwarg")

    agent_src = _read("core/agent.py")
    if "mcp_lookup=self.mcp_client.get_tool_class" not in agent_src:
        _fail("D3", "agent.py late-stage rebuild missing mcp_lookup wire")

    _ok("D3", "MCP 层 lookup wire-up + tool-name 一致性")


# --------------------------------------------------------------------------- D4

def d4_mutates_params_audit() -> None:
    audit_src = _read("core/policy_v2/param_mutation_audit.py")
    if "class ParamMutationAuditor" not in audit_src:
        _fail("D4", "ParamMutationAuditor class missing")
    if "DEFAULT_AUDIT_FILENAME" not in audit_src:
        _fail("D4", "DEFAULT_AUDIT_FILENAME missing")
    if 'plugin_param_modifications.jsonl' not in audit_src:
        _fail("D4", "default audit filename mismatch (must be plugin_param_modifications.jsonl)")
    if "def _diff_recursive" not in audit_src:
        _fail("D4", "_diff_recursive helper missing")
    if "def evaluate" not in audit_src:
        _fail("D4", "ParamMutationAuditor.evaluate missing")
    if "def write" not in audit_src:
        _fail("D4", "ParamMutationAuditor.write missing")
    # Defensive: write must use a lock
    if "self._lock" not in audit_src or "threading.Lock" not in audit_src:
        _fail("D4", "ParamMutationAuditor.write must use threading.Lock for jsonl append")

    te_src = _read("core/tool_executor.py")
    if "_dispatch_before_tool_use_hook" not in te_src:
        _fail("D4", "tool_executor._dispatch_before_tool_use_hook missing")
    if "from .policy_v2.param_mutation_audit import" not in te_src:
        _fail("D4", "tool_executor must import param_mutation_audit")
    if "tool_input.clear()" not in te_src or "tool_input.update(before_snapshot)" not in te_src:
        _fail("D4", "tool_executor must revert mutations in-place when not authorized")
    if "_plugin_manager: Any = None" not in te_src:
        _fail("D4", "tool_executor missing _plugin_manager slot")

    agent_src = _read("core/agent.py")
    if "self.tool_executor._plugin_manager = self._plugin_manager" not in agent_src:
        _fail("D4", "agent must wire plugin_manager into tool_executor")

    _ok("D4", "mutates_params 强制审计 + revert + jsonl 路径")


# --------------------------------------------------------------------------- D5

# 'core.policy' (the v1 PolicyEngine module) was deleted in C8b-6b. The
# plugin layer must never re-introduce a hard import of any internal v2
# kernel either — only manifest-level permission checks are allowed.
_FORBIDDEN_PLUGIN_IMPORTS = (
    re.compile(r"^\s*from\s+(?:openakita\.)?core\.policy(?:\s|$)", re.MULTILINE),
    re.compile(
        r"^\s*from\s+(?:openakita\.)?core\.policy_v2\.engine\s+import",
        re.MULTILINE,
    ),
    re.compile(
        r"^\s*from\s+(?:openakita\.)?core\.policy_v2\.global_engine\s+import",
        re.MULTILINE,
    ),
    re.compile(
        r"^\s*from\s+\.\.core\.policy_v2\.engine\s+import", re.MULTILINE
    ),
    re.compile(
        r"^\s*from\s+\.\.core\.policy_v2\.global_engine\s+import",
        re.MULTILINE,
    ),
    re.compile(r"\bget_engine_v2\s*\(", re.MULTILINE),
    re.compile(r"\bset_engine_v2\s*\(", re.MULTILINE),
    re.compile(r"\bPolicyEngineV2\b"),
)


def _strip_comments_and_docstrings(source: str) -> str:
    """Remove ``#`` comments and string literals using ``tokenize``.

    Same trick C8b audits use to avoid false positives from prose mentioning
    forbidden symbols inside docstrings or comments. Returns a code-only
    skeleton suitable for grep-style pattern matching.
    """
    import io
    import tokenize

    out: list[str] = []
    last_end = (1, 0)
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            tok_type, tok_str, start, end, _ = tok
            if start > last_end:
                if start[0] > last_end[0]:
                    out.append("\n" * (start[0] - last_end[0]))
                    out.append(" " * start[1])
                else:
                    out.append(" " * (start[1] - last_end[1]))
            if tok_type == tokenize.COMMENT:
                pass
            elif tok_type == tokenize.STRING:
                # Replace string literals with empty marker preserving line shape
                out.append('""')
            else:
                out.append(tok_str)
            last_end = end
    except tokenize.TokenizeError:
        return source  # fall back to raw on syntactically odd files
    return "".join(out)


def d5_plugin_policy_decoupled() -> None:
    plugins_dir = SRC / "plugins"
    offenders: list[tuple[str, str]] = []
    for py in plugins_dir.rglob("*.py"):
        if py.name == "__init__.py":
            continue
        body = _strip_comments_and_docstrings(py.read_text(encoding="utf-8"))
        for pattern in _FORBIDDEN_PLUGIN_IMPORTS:
            for m in pattern.finditer(body):
                snippet = body[max(0, m.start() - 20) : m.end() + 20].replace(
                    "\n", "\\n"
                )
                offenders.append((str(py.relative_to(ROOT)), snippet))

    if offenders:
        for fname, snip in offenders:
            print(f"  - {fname}: ...{snip}...")
        _fail("D5", f"plugins/ has {len(offenders)} forbidden PolicyEngine references")

    # Sanity: confirm v1 module truly gone
    v1_path = SRC / "core" / "policy.py"
    if v1_path.exists():
        _fail("D5", f"v1 PolicyEngine still on disk: {v1_path}")
    try:
        import openakita.core.policy  # noqa: F401

        _fail("D5", "openakita.core.policy still importable")
    except ModuleNotFoundError:
        pass

    _ok("D5", "plugins/ 与 PolicyEngine 完全解耦（R5-7 锁死）")


# --------------------------------------------------------------------------- main

def main() -> int:
    print("=" * 60)
    print("C10 Audit — Hook 来源分层 + Trusted Tool Policy + plugin 桥接")
    print("=" * 60)
    d1_skill_lookup()
    d2_plugin_lookup()
    d3_mcp_lookup()
    d4_mutates_params_audit()
    d5_plugin_policy_decoupled()
    print("=" * 60)
    print("[PASS] 5 dimensions all green")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
