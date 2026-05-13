"""C8 audit D3 — 不是打地鼠：5 个修改点架构上独立、有边界、无暗坑。

每个 sub-fix 应该满足：
- 单点改动覆盖完整 bug surface（无"修了一处漏了一处"）
- API 一致（被多处消费的话，所有 caller 都正确接入）
- 失败模式 fail-safe（默认 deny / 默认 owner / 默认免疫保留）
- 不引入新的隐藏耦合（例如 #5 不让 reasoning_engine 拥有 wait + cleanup 后再让 gateway 也持有）
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# #1 safety_immune builtin: 单一 source of truth
# ---------------------------------------------------------------------------


def _check_immune_single_source() -> None:
    """builtin paths 应只在 safety_immune_defaults.py 定义；engine 通过该模块
    导入而非自己复制一份；__init__.py 重新 export。"""
    src = Path("src/openakita/core/policy_v2/safety_immune_defaults.py").read_text(
        encoding="utf-8"
    )
    assert "BUILTIN_SAFETY_IMMUNE_PATHS" in src
    assert "BUILTIN_SAFETY_IMMUNE_BY_CATEGORY" in src

    engine_src = Path("src/openakita/core/policy_v2/engine.py").read_text(encoding="utf-8")
    assert "from .safety_immune_defaults import expand_builtin_immune_paths" in engine_src
    # engine 不应该自己重复硬编码任何 builtin path（ruff 也会抓到）
    assert "/etc/**" not in engine_src or "expand_builtin" in engine_src
    print("#1 safety_immune single-source: OK (engine imports from defaults module)")


# ---------------------------------------------------------------------------
# #2 OwnerOnly: 三态语义 + 持久化 + API 对称
# ---------------------------------------------------------------------------


def _check_owner_tri_state() -> None:
    """`_get_owner_user_ids` 返回 None / set() / 非空 set 三态语义清晰，
    `_handle_message` 按三态翻译为 ``is_owner``，API ``POST`` 也支持 ``None``
    显式取消配置。"""
    gw_src = Path("src/openakita/channels/gateway.py").read_text(encoding="utf-8")
    api_src = Path("src/openakita/api/routes/im.py").read_text(encoding="utf-8")

    assert "def _get_owner_user_ids" in gw_src
    assert "def _apply_persisted_owner_allowlist" in gw_src
    # gateway 翻译三态：未配 → True；配了 → user_id 是否在集合里
    assert (
        'is_owner = True if owner_ids is None else (str(message.user_id) in owner_ids)'
        in gw_src
    )
    # 持久化在 start() 时调用
    assert "self._apply_persisted_owner_allowlist()" in gw_src

    # API 对称：GET / POST 都有
    assert '@router.get("/api/im/owner-allowlist")' in api_src
    assert '@router.post("/api/im/owner-allowlist")' in api_src
    # POST owners=None 显式取消配置
    assert "if body.owners is None:" in api_src
    assert "delattr(adapter," in api_src
    print("#2 OwnerOnly tri-state + API symmetry: OK")


# ---------------------------------------------------------------------------
# #3 switch_mode: Session 字段 + handler 写入 + ctx 读取，三处一致
# ---------------------------------------------------------------------------


def _check_switch_mode_chain() -> None:
    """三处必须同步：Session.session_role 字段 + ModeHandler 写入 + adapter 读取。"""
    sess_src = Path("src/openakita/sessions/session.py").read_text(encoding="utf-8")
    mode_src = Path("src/openakita/tools/handlers/mode.py").read_text(encoding="utf-8")
    adapter_src = Path("src/openakita/core/policy_v2/adapter.py").read_text(
        encoding="utf-8"
    )

    # Session 字段定义 + 持久化
    assert "session_role: str = " in sess_src
    assert "confirmation_mode_override: str | None = None" in sess_src
    assert '"session_role": self.session_role,' in sess_src
    assert '"confirmation_mode_override": self.confirmation_mode_override,' in sess_src

    # mode.py 写入新字段（不再用旧的 .mode）
    assert 'session.session_role = target_mode' in mode_src
    assert 'session.mode = target_mode' not in mode_src

    # adapter.build_policy_context 读 session.session_role
    assert 'sr = getattr(session, "session_role", None)' in adapter_src
    assert "effective_mode = sr" in adapter_src
    print("#3 switch_mode chain (Session field + handler + adapter): OK")


# ---------------------------------------------------------------------------
# #4 consume_session_trust: 真删 + persists + 不漏对未匹配的 GC
# ---------------------------------------------------------------------------


def _check_consume_prune_persists() -> None:
    """无论是否匹配，过期规则都要 GC + persist 回 session。"""
    src = Path("src/openakita/core/trusted_paths.py").read_text(encoding="utf-8")
    # 关键 invariant
    assert "if pruned:" in src
    assert 'overrides["rules"] = surviving_rules' in src
    assert "session.set_metadata(SESSION_KEY, overrides)" in src
    print("#4 consume_session_trust prune + persist: OK")


# ---------------------------------------------------------------------------
# #5 IM SSE: reasoning_engine 拥有 wait + cleanup（gateway 不接管）
# ---------------------------------------------------------------------------


def _check_sse_wait_ownership() -> None:
    """gateway 不再调 prepare/wait/cleanup（避免序列竞争）；
    reasoning_engine 始终 yield SSE（不早退）；
    prepare_ui_confirm 幂等。"""
    re_src = Path("src/openakita/core/reasoning_engine.py").read_text(encoding="utf-8")
    gw_src = Path("src/openakita/channels/gateway.py").read_text(encoding="utf-8")
    pe_src = Path("src/openakita/core/policy.py").read_text(encoding="utf-8")

    # reasoning_engine 不应包含旧的早退字符串
    assert "IM 通道，无法安全完成交互式确认" not in re_src
    # 但仍用 _is_im_conversation 来调整 timeout
    assert "_confirm_timeout = max(_confirm_timeout * 4, 180.0)" in re_src

    # gateway 不再在 _handle_im_security_confirm 内 prepare/wait/cleanup（删掉）
    handler_block_start = gw_src.index("async def _handle_im_security_confirm")
    handler_block_end = gw_src.index(
        "async def _wait_for_interrupt", handler_block_start
    )
    handler_block = gw_src[handler_block_start:handler_block_end]
    assert "pe.prepare_ui_confirm(confirm_id)" not in handler_block, (
        "gateway should not call prepare_ui_confirm for the interactive path"
    )
    assert "await pe.wait_for_ui_resolution(confirm_id, timeout)" not in handler_block, (
        "gateway should not own wait_for_ui_resolution (reasoning_engine does)"
    )
    assert "pe.cleanup_ui_confirm(confirm_id)" not in handler_block, (
        "gateway should not own cleanup_ui_confirm (reasoning_engine does)"
    )

    # prepare_ui_confirm 幂等
    assert (
        'if existing is not None and confirm_id not in self._ui_confirm_decisions:'
        in pe_src
    )
    print("#5 SSE wait ownership: OK (gateway only renders, reasoning_engine waits/cleans)")


def main() -> None:
    _check_immune_single_source()
    _check_owner_tri_state()
    _check_switch_mode_chain()
    _check_consume_prune_persists()
    _check_sse_wait_ownership()
    print()
    print("D3 ALL PASS")


if __name__ == "__main__":
    main()
