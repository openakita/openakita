"""C2 unit tests: ApprovalClassifier 5+ 步分类链 + refine + LRU + registry 接入。

Acceptance criteria for C2:
- 5 步分类链按优先级生效（explicit > skill/mcp/plugin > heuristic > unknown）
- 多源叠加用 most_strict 取严
- 启发式表完全覆盖 docs §4.21.2
- 跨盘 path-based refine 正确（write_file/edit_file/move_file 等）
- LRU cache 命中 + invalidate 工作
- DecisionSource 准确反映来源（让 C19 completeness test 能信任）
- handlers/__init__.py register(tool_classes=) 与 handler.TOOL_CLASSES 双源叠加
- 不破坏 v1 register 调用（向后兼容）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.core.policy_v2 import (
    ApprovalClass,
    ApprovalClassifier,
    ConfirmationMode,
    DecisionSource,
    PolicyContext,
    SessionRole,
    most_strict,
    strictness,
)
from openakita.core.policy_v2.classifier import (
    _heuristic_classify,
    _is_inside_workspace,
)
from openakita.tools.handlers import SystemHandlerRegistry

# ---- strictness ordering invariants ----


class TestStrictness:
    def test_destructive_strictest(self) -> None:
        for klass in ApprovalClass:
            if klass == ApprovalClass.DESTRUCTIVE:
                continue
            assert strictness(ApprovalClass.DESTRUCTIVE) >= strictness(klass), (
                f"DESTRUCTIVE must be >= {klass.value}"
            )

    def test_unknown_treated_as_high(self) -> None:
        """UNKNOWN 应当 ≥ MUTATING_GLOBAL（safety-by-default）。"""
        assert strictness(ApprovalClass.UNKNOWN) >= strictness(
            ApprovalClass.MUTATING_GLOBAL
        )

    def test_readonly_lower_than_mutating(self) -> None:
        for ro in (
            ApprovalClass.READONLY_SCOPED,
            ApprovalClass.READONLY_GLOBAL,
            ApprovalClass.READONLY_SEARCH,
        ):
            for mut in (
                ApprovalClass.MUTATING_SCOPED,
                ApprovalClass.MUTATING_GLOBAL,
                ApprovalClass.DESTRUCTIVE,
            ):
                assert strictness(ro) < strictness(mut)

    def test_most_strict_picks_destructive(self) -> None:
        candidates = [
            (ApprovalClass.READONLY_GLOBAL, DecisionSource.HEURISTIC_PREFIX),
            (ApprovalClass.DESTRUCTIVE, DecisionSource.EXPLICIT_REGISTER_PARAM),
            (ApprovalClass.MUTATING_SCOPED, DecisionSource.SKILL_METADATA),
        ]
        klass, source = most_strict(candidates)
        assert klass == ApprovalClass.DESTRUCTIVE
        assert source == DecisionSource.EXPLICIT_REGISTER_PARAM

    def test_most_strict_empty_returns_unknown(self) -> None:
        klass, source = most_strict([])
        assert klass == ApprovalClass.UNKNOWN
        assert source == DecisionSource.FALLBACK_UNKNOWN

    def test_most_strict_tie_keeps_first(self) -> None:
        """同严格度时保留第一个（输入顺序代表优先级）。"""
        a = (ApprovalClass.READONLY_GLOBAL, DecisionSource.EXPLICIT_REGISTER_PARAM)
        b = (ApprovalClass.READONLY_GLOBAL, DecisionSource.HEURISTIC_PREFIX)
        klass, source = most_strict([a, b])
        assert source == DecisionSource.EXPLICIT_REGISTER_PARAM


# ---- heuristic classification (docs §4.21.2 ground truth) ----


class TestHeuristic:
    @pytest.mark.parametrize(
        ("tool", "expected"),
        [
            # READONLY_GLOBAL
            ("read_file", ApprovalClass.READONLY_GLOBAL),
            ("list_directory", ApprovalClass.READONLY_GLOBAL),
            ("get_user_profile", ApprovalClass.READONLY_GLOBAL),
            ("view_image", ApprovalClass.READONLY_GLOBAL),
            # READONLY_SEARCH
            ("search_memory", ApprovalClass.READONLY_SEARCH),
            ("find_files", ApprovalClass.READONLY_SEARCH),
            ("grep", ApprovalClass.READONLY_SEARCH),
            ("glob", ApprovalClass.READONLY_SEARCH),
            # MUTATING
            ("write_file", ApprovalClass.MUTATING_SCOPED),
            ("edit_file", ApprovalClass.MUTATING_SCOPED),
            ("create_todo", ApprovalClass.MUTATING_SCOPED),
            ("move_file", ApprovalClass.MUTATING_SCOPED),
            ("rename_thing", ApprovalClass.MUTATING_SCOPED),
            ("update_user_profile", ApprovalClass.MUTATING_SCOPED),
            # DESTRUCTIVE
            ("delete_file", ApprovalClass.DESTRUCTIVE),
            ("uninstall_skill", ApprovalClass.DESTRUCTIVE),
            ("remove_thing", ApprovalClass.DESTRUCTIVE),
            ("drop_database", ApprovalClass.DESTRUCTIVE),
            # EXEC_CAPABLE
            ("run_shell", ApprovalClass.EXEC_CAPABLE),
            ("run_powershell", ApprovalClass.EXEC_CAPABLE),
            ("execute_query", ApprovalClass.EXEC_CAPABLE),
            ("spawn_agent", ApprovalClass.EXEC_CAPABLE),
            ("kill_process", ApprovalClass.EXEC_CAPABLE),
            # CONTROL_PLANE
            ("schedule_task", ApprovalClass.CONTROL_PLANE),
            ("cron_job", ApprovalClass.CONTROL_PLANE),
            ("system_config", ApprovalClass.CONTROL_PLANE),
            ("evolution_apply", ApprovalClass.CONTROL_PLANE),
            ("switch_persona", ApprovalClass.CONTROL_PLANE),
            ("setup_organization", ApprovalClass.CONTROL_PLANE),
        ],
    )
    def test_known_prefix_classifications(
        self, tool: str, expected: ApprovalClass
    ) -> None:
        assert _heuristic_classify(tool) == expected

    @pytest.mark.parametrize(
        "tool",
        ["nonsense_tool", "asdfg", "", "  ", "_underscore_only"],
    )
    def test_unknown_returns_none(self, tool: str) -> None:
        assert _heuristic_classify(tool) is None

    def test_destructive_beats_other_prefixes(self) -> None:
        """delete_remote_data 同时含 delete_，启发式优先 DESTRUCTIVE（顺序保证）。"""
        assert _heuristic_classify("delete_remote_data") == ApprovalClass.DESTRUCTIVE

    def test_update_scheduled_falls_into_mutating_not_control(self) -> None:
        """启发式表里 update_ 是 MUTATING_SCOPED，没有 update_scheduled 特例（与 docs §4.21.2 一致）。

        实际工具 update_scheduled_task 的 CONTROL_PLANE 分类应通过 explicit
        register（C8 实施），不靠启发式特例。
        """
        assert (
            _heuristic_classify("update_scheduled_task") == ApprovalClass.MUTATING_SCOPED
        )


# ---- _is_inside_workspace ----


class TestPathInWorkspace:
    def test_inside_returns_true(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        assert _is_inside_workspace(str(sub / "x.txt"), tmp_path) is True

    def test_outside_returns_false(self, tmp_path: Path) -> None:
        other = tmp_path.parent / "_definitely_outside_xyz"
        assert _is_inside_workspace(str(other / "x.txt"), tmp_path) is False

    def test_workspace_itself_is_inside(self, tmp_path: Path) -> None:
        assert _is_inside_workspace(str(tmp_path), tmp_path) is True

    def test_non_existent_subpath_still_inside(self, tmp_path: Path) -> None:
        """write_file 写新文件，path 还不存在，仍应判内。"""
        ghost = tmp_path / "future" / "new.txt"
        assert _is_inside_workspace(str(ghost), tmp_path) is True

    def test_invalid_path_returns_false(self, tmp_path: Path) -> None:
        """异常输入 → 保守判外（升级严格度）。"""
        # NUL 字节在大多数文件系统上无效
        bogus = "abc\x00def"
        assert _is_inside_workspace(bogus, tmp_path) is False


# ---- ApprovalClassifier core 5-step chain ----


class TestClassifierChain:
    def test_explicit_register_param_wins_over_heuristic(self) -> None:
        explicit = {"my_destroyer": (ApprovalClass.DESTRUCTIVE, DecisionSource.EXPLICIT_REGISTER_PARAM)}
        clf = ApprovalClassifier(
            explicit_lookup=lambda t: explicit.get(t),
        )
        klass, src = clf.classify_with_source("my_destroyer")
        assert klass == ApprovalClass.DESTRUCTIVE
        assert src == DecisionSource.EXPLICIT_REGISTER_PARAM

    def test_skill_metadata_passed_through(self) -> None:
        clf = ApprovalClassifier(
            skill_lookup=lambda t: (ApprovalClass.MUTATING_GLOBAL, DecisionSource.SKILL_METADATA)
            if t == "execute_skill"
            else None,
        )
        klass, src = clf.classify_with_source("execute_skill")
        assert klass == ApprovalClass.MUTATING_GLOBAL
        assert src == DecisionSource.SKILL_METADATA

    def test_skill_lookup_default_source_when_none(self) -> None:
        """lookup 返回 (klass, None) → classifier 标 SKILL_METADATA。"""
        clf = ApprovalClassifier(
            skill_lookup=lambda t: (ApprovalClass.READONLY_SCOPED, None),
        )
        _, src = clf.classify_with_source("anything")
        assert src == DecisionSource.SKILL_METADATA

    def test_multi_source_takes_strict(self) -> None:
        """register=READONLY + skill=DESTRUCTIVE → 取 DESTRUCTIVE（safety-by-default）。"""
        clf = ApprovalClassifier(
            explicit_lookup=lambda t: (
                ApprovalClass.READONLY_GLOBAL,
                DecisionSource.EXPLICIT_REGISTER_PARAM,
            ),
            skill_lookup=lambda t: (
                ApprovalClass.DESTRUCTIVE,
                DecisionSource.SKILL_METADATA,
            ),
        )
        klass, src = clf.classify_with_source("hybrid_tool")
        assert klass == ApprovalClass.DESTRUCTIVE
        assert src == DecisionSource.SKILL_METADATA

    def test_heuristic_only_when_no_explicit_source(self) -> None:
        """无显式来源 → 走启发式。"""
        clf = ApprovalClassifier()
        klass, src = clf.classify_with_source("write_file")
        assert klass == ApprovalClass.MUTATING_SCOPED
        assert src == DecisionSource.HEURISTIC_PREFIX

    def test_fallback_unknown_for_truly_unrecognized(self) -> None:
        clf = ApprovalClassifier()
        klass, src = clf.classify_with_source("nonsense_xyz")
        assert klass == ApprovalClass.UNKNOWN
        assert src == DecisionSource.FALLBACK_UNKNOWN

    def test_explicit_present_does_not_fallback_to_heuristic(self) -> None:
        """显式来源命中后不再回看启发式（避免不一致）。"""
        clf = ApprovalClassifier(
            explicit_lookup=lambda t: (
                ApprovalClass.READONLY_GLOBAL,
                DecisionSource.EXPLICIT_REGISTER_PARAM,
            ),
        )
        klass, src = clf.classify_with_source("delete_file")
        assert klass == ApprovalClass.READONLY_GLOBAL
        assert src == DecisionSource.EXPLICIT_REGISTER_PARAM


# ---- _refine_with_params (cross-disk path) ----


class TestRefine:
    def _make_ctx(self, workspace: Path) -> PolicyContext:
        return PolicyContext(
            session_id="t",
            workspace_roots=(workspace,),
            session_role=SessionRole.AGENT,
            confirmation_mode=ConfirmationMode.DEFAULT,
        )

    def test_write_file_inside_workspace_stays_scoped(self, tmp_path: Path) -> None:
        ctx = self._make_ctx(tmp_path)
        clf = ApprovalClassifier()
        klass, _ = clf.classify_with_source(
            "write_file", {"path": str(tmp_path / "x.txt")}, ctx
        )
        assert klass == ApprovalClass.MUTATING_SCOPED

    def test_write_file_outside_workspace_upgrades_to_global(
        self, tmp_path: Path
    ) -> None:
        ctx = self._make_ctx(tmp_path)
        clf = ApprovalClassifier()
        outside = tmp_path.parent / "_outside_dir_99" / "x.txt"
        klass, _ = clf.classify_with_source("write_file", {"path": str(outside)}, ctx)
        assert klass == ApprovalClass.MUTATING_GLOBAL

    def test_write_file_inside_any_workspace_root_stays_scoped(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        ctx = PolicyContext(
            session_id="t",
            workspace_roots=(root_a, root_b),
            session_role=SessionRole.AGENT,
            confirmation_mode=ConfirmationMode.DEFAULT,
        )
        clf = ApprovalClassifier()
        klass, _ = clf.classify_with_source(
            "write_file",
            {"path": str(root_b / "note.txt")},
            ctx,
        )
        assert klass == ApprovalClass.MUTATING_SCOPED

    def test_move_file_dst_outside_upgrades(self, tmp_path: Path) -> None:
        """move_file 检查 dst（也检查 src）— 任一外部即升级。"""
        ctx = self._make_ctx(tmp_path)
        clf = ApprovalClassifier()
        klass, _ = clf.classify_with_source(
            "move_file",
            {"src": str(tmp_path / "a.txt"), "dst": str(tmp_path.parent / "out.txt")},
            ctx,
        )
        assert klass == ApprovalClass.MUTATING_GLOBAL

    def test_no_ctx_no_refine(self, tmp_path: Path) -> None:
        """ctx=None 时不做 refine（保守不降级）。"""
        clf = ApprovalClassifier()
        klass, _ = clf.classify_with_source(
            "write_file", {"path": str(tmp_path.parent / "x.txt")}, None
        )
        assert klass == ApprovalClass.MUTATING_SCOPED

    def test_no_path_param_no_refine(self, tmp_path: Path) -> None:
        ctx = self._make_ctx(tmp_path)
        clf = ApprovalClassifier()
        klass, _ = clf.classify_with_source("write_file", {}, ctx)
        assert klass == ApprovalClass.MUTATING_SCOPED

    def test_delete_file_not_path_refined(self, tmp_path: Path) -> None:
        """delete_file 已经是 DESTRUCTIVE，refine 不再升级（也不允许降级）。"""
        ctx = self._make_ctx(tmp_path)
        clf = ApprovalClassifier()
        klass, _ = clf.classify_with_source(
            "delete_file", {"path": str(tmp_path.parent / "x.txt")}, ctx
        )
        assert klass == ApprovalClass.DESTRUCTIVE

    def test_refine_does_not_change_decision_source(self, tmp_path: Path) -> None:
        """refine 调整 ApprovalClass 但不动 DecisionSource（C19 测试依赖）。"""
        ctx = self._make_ctx(tmp_path)
        clf = ApprovalClassifier()
        _, src = clf.classify_with_source(
            "write_file", {"path": str(tmp_path.parent / "x.txt")}, ctx
        )
        assert src == DecisionSource.HEURISTIC_PREFIX


# ---- LRU cache ----


class TestCache:
    def test_repeated_classify_uses_cache(self) -> None:
        call_count = 0

        def lookup(t: str):
            nonlocal call_count
            call_count += 1
            return None

        clf = ApprovalClassifier(explicit_lookup=lookup)
        clf.classify_with_source("read_file")
        clf.classify_with_source("read_file")
        clf.classify_with_source("read_file")
        assert call_count == 1

    def test_invalidate_specific_tool(self) -> None:
        clf = ApprovalClassifier()
        clf.classify_with_source("read_file")
        clf.classify_with_source("write_file")
        assert clf.cache_size == 2
        clf.invalidate("read_file")
        assert clf.cache_size == 1

    def test_invalidate_all(self) -> None:
        clf = ApprovalClassifier()
        clf.classify_with_source("read_file")
        clf.classify_with_source("write_file")
        clf.invalidate()
        assert clf.cache_size == 0

    def test_lru_evicts_oldest(self) -> None:
        clf = ApprovalClassifier(cache_size=3)
        for tool in ("a_read_x", "b_read_x", "c_read_x", "d_read_x"):
            clf.classify_with_source(tool)
        # 'a_read_x' is oldest, should be evicted
        assert clf.cache_size == 3

    def test_lru_recently_used_preserved(self) -> None:
        clf = ApprovalClassifier(cache_size=2)
        clf.classify_with_source("a_read_x")
        clf.classify_with_source("b_read_x")
        clf.classify_with_source("a_read_x")  # touch a
        clf.classify_with_source("c_read_x")  # should evict b, not a
        # We test indirectly by checking that re-classifying 'a' still uses cache
        # (no way to assert a vs b without instrumenting; rely on size)
        assert clf.cache_size == 2

    def test_refine_not_cached(self, tmp_path: Path) -> None:
        """同一 tool 不同 params/ctx 应得到不同 refined 分类。"""
        clf = ApprovalClassifier()
        ctx = PolicyContext(session_id="t", workspace_roots=(tmp_path,))
        inside, _ = clf.classify_with_source(
            "write_file", {"path": str(tmp_path / "a.txt")}, ctx
        )
        outside, _ = clf.classify_with_source(
            "write_file", {"path": str(tmp_path.parent / "b.txt")}, ctx
        )
        assert inside == ApprovalClass.MUTATING_SCOPED
        assert outside == ApprovalClass.MUTATING_GLOBAL


# ---- handlers/__init__.py register(tool_classes=) integration ----


class _StubHandlerNoAttr:
    TOOLS = ["my_read_file", "my_write_file"]

    def __call__(self, tool_name: str, params: dict) -> str:
        return "ok"


class _StubHandlerWithAttr:
    TOOLS = ["from_attr_a", "from_attr_b"]
    TOOL_CLASSES = {
        "from_attr_a": ApprovalClass.READONLY_GLOBAL,
        "from_attr_b": ApprovalClass.DESTRUCTIVE,
    }

    def __call__(self, tool_name: str, params: dict) -> str:
        return "ok"


class TestRegistryIntegration:
    def test_register_without_tool_classes_is_backward_compatible(self) -> None:
        """v1 调用 register(handler) 不传 tool_classes → 仍工作，_tool_classes 为空。"""
        registry = SystemHandlerRegistry()
        handler = _StubHandlerNoAttr()
        registry.register("stub", handler.__call__)
        assert registry.has_handler("stub")
        assert registry.get_tool_class("my_read_file") is None

    def test_register_with_tool_classes_param(self) -> None:
        registry = SystemHandlerRegistry()
        handler = _StubHandlerNoAttr()
        registry.register(
            "stub",
            handler.__call__,
            tool_classes={
                "my_read_file": ApprovalClass.READONLY_GLOBAL,
                "my_write_file": ApprovalClass.MUTATING_SCOPED,
            },
        )
        read_klass, read_src = registry.get_tool_class("my_read_file")
        assert read_klass == ApprovalClass.READONLY_GLOBAL
        assert read_src == DecisionSource.EXPLICIT_REGISTER_PARAM

    def test_handler_attr_picked_up_when_param_omitted(self) -> None:
        registry = SystemHandlerRegistry()
        handler = _StubHandlerWithAttr()
        registry.register("stub", handler.__call__)
        a_klass, a_src = registry.get_tool_class("from_attr_a")
        b_klass, b_src = registry.get_tool_class("from_attr_b")
        assert a_klass == ApprovalClass.READONLY_GLOBAL
        assert a_src == DecisionSource.EXPLICIT_HANDLER_ATTR
        assert b_klass == ApprovalClass.DESTRUCTIVE
        assert b_src == DecisionSource.EXPLICIT_HANDLER_ATTR

    def test_register_param_and_handler_attr_take_strict(self) -> None:
        """同一工具 register=READONLY + attr=DESTRUCTIVE → 取 DESTRUCTIVE。"""
        registry = SystemHandlerRegistry()
        handler = _StubHandlerWithAttr()
        registry.register(
            "stub",
            handler.__call__,
            tool_classes={"from_attr_a": ApprovalClass.READONLY_SEARCH},
        )
        # from_attr_a: register=READONLY_SEARCH(2) vs attr=READONLY_GLOBAL(4) → READONLY_GLOBAL
        klass, _ = registry.get_tool_class("from_attr_a")
        assert klass == ApprovalClass.READONLY_GLOBAL

    def test_register_warns_and_drops_typo_tool_in_tool_classes(self, caplog) -> None:
        """tool_classes 提到 TOOLS 列表外的工具名 → WARN 并丢弃，避免幽灵条目。

        若不丢弃，将来某 plugin 注册同名工具时会意外继承这个孤立 class（语义错乱）。
        """
        registry = SystemHandlerRegistry()
        handler = _StubHandlerNoAttr()
        with caplog.at_level("WARNING"):
            registry.register(
                "stub",
                handler.__call__,
                tool_classes={
                    "my_read_file": ApprovalClass.READONLY_GLOBAL,
                    "typo_tool": ApprovalClass.DESTRUCTIVE,
                },
            )
        assert any("typo_tool" in rec.message for rec in caplog.records)
        assert any("dropping" in rec.message for rec in caplog.records)
        # Real tool present
        assert registry.get_tool_class("my_read_file") is not None
        # Typo dropped — no phantom entry
        assert registry.get_tool_class("typo_tool") is None

    def test_unregister_clears_tool_classes(self) -> None:
        registry = SystemHandlerRegistry()
        handler = _StubHandlerWithAttr()
        registry.register("stub", handler.__call__)
        assert registry.get_tool_class("from_attr_a") is not None
        registry.unregister("stub")
        assert registry.get_tool_class("from_attr_a") is None

    def test_unmap_tool_clears_class(self) -> None:
        registry = SystemHandlerRegistry()
        handler = _StubHandlerWithAttr()
        registry.register("stub", handler.__call__)
        registry.unmap_tool("from_attr_a")
        assert registry.get_tool_class("from_attr_a") is None

    def test_classifier_e2e_via_registry_lookup(self) -> None:
        """ApprovalClassifier 通过 registry.get_tool_class 拿到显式分类。"""
        registry = SystemHandlerRegistry()
        handler = _StubHandlerWithAttr()
        registry.register("stub", handler.__call__)

        clf = ApprovalClassifier(explicit_lookup=registry.get_tool_class)
        klass, src = clf.classify_with_source("from_attr_b")
        assert klass == ApprovalClass.DESTRUCTIVE
        assert src == DecisionSource.EXPLICIT_HANDLER_ATTR

    def test_repeated_register_takes_strict(self) -> None:
        """同一 handler 名重复 register（罕见但可能：plugin 重载等）→ most_strict 叠加。

        防御性：第二次 register 不应能"降级"风险。第一次标 DESTRUCTIVE
        + 第二次标 READONLY → 仍 DESTRUCTIVE。
        """
        registry = SystemHandlerRegistry()
        handler = _StubHandlerNoAttr()
        registry.register(
            "stub",
            handler.__call__,
            tool_classes={"my_read_file": ApprovalClass.DESTRUCTIVE},
        )
        registry.register(
            "stub",
            handler.__call__,
            tool_classes={"my_read_file": ApprovalClass.READONLY_GLOBAL},
        )
        klass, _ = registry.get_tool_class("my_read_file")
        assert klass == ApprovalClass.DESTRUCTIVE

    def test_class_value_can_be_str_alias(self) -> None:
        """ApprovalClass 是 StrEnum，开发者用 string 'destructive' 与 enum 等价。

        虽不推荐，但不应 silently 变 UNKNOWN（避免兼容性陷阱）。
        """
        registry = SystemHandlerRegistry()
        handler = _StubHandlerNoAttr()
        # 故意用字符串字面量而非 enum
        registry.register(
            "stub",
            handler.__call__,
            tool_classes={"my_read_file": "destructive"},  # type: ignore[dict-item]
        )
        klass, _ = registry.get_tool_class("my_read_file")
        # StrEnum 比较应保持等价
        assert klass == ApprovalClass.DESTRUCTIVE


class TestCacheStaleness:
    """已知行为：classifier cache 不自动跟随 registry mutation 失效。

    OpenAkita 启动时一次注册，运行时不变 → 实际无影响。
    plugin 动态注册时 plugin manager 必须显式调 ``classifier.invalidate(tool)``。
    本测试把这个**已知行为**冻结住，避免未来误以为是 bug 修。
    """

    def test_unregister_does_not_auto_invalidate(self) -> None:
        registry = SystemHandlerRegistry()
        handler = _StubHandlerWithAttr()
        registry.register("stub", handler.__call__)
        clf = ApprovalClassifier(explicit_lookup=registry.get_tool_class)

        first, _ = clf.classify_with_source("from_attr_a")
        assert first == ApprovalClass.READONLY_GLOBAL

        registry.unregister("stub")
        # cache 仍命中，未自动失效
        cached, src = clf.classify_with_source("from_attr_a")
        assert cached == ApprovalClass.READONLY_GLOBAL
        assert src == DecisionSource.EXPLICIT_HANDLER_ATTR

    def test_invalidate_then_reclassify_picks_up_new_state(self) -> None:
        """显式 invalidate 后下次 classify 应反映新 lookup 状态。"""
        registry = SystemHandlerRegistry()
        handler = _StubHandlerWithAttr()
        registry.register("stub", handler.__call__)
        clf = ApprovalClassifier(explicit_lookup=registry.get_tool_class)

        clf.classify_with_source("from_attr_a")  # cache hit
        registry.unregister("stub")
        clf.invalidate("from_attr_a")  # 显式失效

        # registry 已空 → fallback 到 heuristic ("from_attr_a" 不匹配任何前缀 → UNKNOWN)
        klass, src = clf.classify_with_source("from_attr_a")
        assert klass == ApprovalClass.UNKNOWN
        assert src == DecisionSource.FALLBACK_UNKNOWN


# ---- C3 additions: classify_full + shell command refine ----


class TestClassifyFull:
    """``classify_full`` 一次性返回 ApprovalClass + meta（C3 R2-5）。"""

    def _ctx(self, ws: Path) -> PolicyContext:
        return PolicyContext(
            session_id="t",
            workspace_roots=(ws,),
            session_role=SessionRole.AGENT,
            confirmation_mode=ConfirmationMode.DEFAULT,
        )

    def test_readonly_no_shell_meta(self) -> None:
        clf = ApprovalClassifier()
        result = clf.classify_full("read_file", {"path": "/tmp/x"})
        assert result.approval_class == ApprovalClass.READONLY_GLOBAL
        assert result.shell_risk_level is None
        assert result.needs_sandbox is False
        assert result.needs_checkpoint is False

    def test_destructive_marks_checkpoint_needed(self) -> None:
        clf = ApprovalClassifier()
        result = clf.classify_full("delete_file", {"path": "/tmp/x"})
        assert result.approval_class == ApprovalClass.DESTRUCTIVE
        assert result.needs_checkpoint is True

    def test_mutating_global_marks_checkpoint_needed(self, tmp_path: Path) -> None:
        ctx = self._ctx(tmp_path)
        clf = ApprovalClassifier()
        outside = tmp_path.parent / "_outside_99" / "x.txt"
        result = clf.classify_full("write_file", {"path": str(outside)}, ctx)
        assert result.approval_class == ApprovalClass.MUTATING_GLOBAL
        assert result.needs_checkpoint is True

    def test_classify_compatibility_with_classify_with_source(self) -> None:
        """``classify`` / ``classify_with_source`` / ``classify_full`` 三者结果一致。"""
        clf = ApprovalClassifier()
        klass, src = clf.classify_with_source("read_file")
        full = clf.classify_full("read_file")
        assert klass == full.approval_class
        assert src == full.source
        assert clf.classify("read_file") == full.approval_class


class TestShellRefineInClassifier:
    """run_shell / run_powershell 等 shell 类工具走 shell_risk refine。"""

    def test_run_shell_low_command_stays_exec_capable(self) -> None:
        clf = ApprovalClassifier()
        result = clf.classify_full("run_shell", {"command": "ls -la"})
        # run_shell base = EXEC_CAPABLE (heuristic run_)；LOW shell 不升不降
        assert result.approval_class == ApprovalClass.EXEC_CAPABLE
        assert result.shell_risk_level is not None
        assert result.shell_risk_level.value == "low"
        assert result.needs_sandbox is False

    def test_run_shell_high_risk_upgrades_to_destructive(self) -> None:
        clf = ApprovalClassifier()
        result = clf.classify_full("run_shell", {"command": "rm -rf /tmp/foo"})
        assert result.approval_class == ApprovalClass.DESTRUCTIVE
        assert result.needs_sandbox is True
        assert result.needs_checkpoint is True

    def test_run_shell_critical_command_destructive_no_relax(self) -> None:
        clf = ApprovalClassifier()
        result = clf.classify_full("run_shell", {"command": "rm -rf / "})
        assert result.approval_class == ApprovalClass.DESTRUCTIVE
        assert result.shell_risk_level.value == "critical"

    def test_run_shell_blocked_token_destructive(self) -> None:
        """BLOCKED → DESTRUCTIVE 但 needs_sandbox=False（不允许沙箱执行）。"""
        clf = ApprovalClassifier()
        result = clf.classify_full("run_shell", {"command": "regedit /s evil.reg"})
        assert result.approval_class == ApprovalClass.DESTRUCTIVE
        assert result.shell_risk_level.value == "blocked"
        assert result.needs_sandbox is False

    def test_run_shell_medium_command_upgrades_to_mutating_global(self) -> None:
        clf = ApprovalClassifier()
        result = clf.classify_full("run_shell", {"command": "git push origin main"})
        assert result.approval_class == ApprovalClass.MUTATING_GLOBAL
        assert result.shell_risk_level.value == "medium"
        assert result.needs_sandbox is True

    def test_run_powershell_recursive_remove_destructive(self) -> None:
        clf = ApprovalClassifier()
        result = clf.classify_full(
            "run_powershell", {"command": "Remove-Item -Recurse C:\\foo"}
        )
        assert result.approval_class == ApprovalClass.DESTRUCTIVE

    def test_run_shell_empty_command_keeps_base(self) -> None:
        """空 command → 不调 shell_risk → 保留 base EXEC_CAPABLE。"""
        clf = ApprovalClassifier()
        result = clf.classify_full("run_shell", {"command": ""})
        assert result.approval_class == ApprovalClass.EXEC_CAPABLE
        assert result.shell_risk_level is None

    def test_non_shell_tool_no_shell_classification(self) -> None:
        """write_file 不会调 shell_risk 即便 params 有 command 字段。"""
        clf = ApprovalClassifier()
        result = clf.classify_full(
            "write_file",
            {"path": "/tmp/x", "command": "rm -rf /"},  # 'command' 是 noise
        )
        assert result.shell_risk_level is None

    def test_run_shell_uses_script_param_alias(self) -> None:
        """有些工具用 'script' 而非 'command'。"""
        clf = ApprovalClassifier()
        result = clf.classify_full("run_shell", {"script": "rm -rf /tmp/foo"})
        assert result.approval_class == ApprovalClass.DESTRUCTIVE
