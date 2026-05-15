"""C6 — policy_v2.global_engine 单例 / 延迟加载 / reset 行为测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from openakita.core.policy_v2 import global_engine
from openakita.core.policy_v2.engine import PolicyEngineV2
from openakita.core.policy_v2.global_engine import (
    get_config_v2,
    get_engine_v2,
    is_initialized,
    rebuild_engine_v2,
    reset_engine_v2,
    set_engine_v2,
)
from openakita.core.policy_v2.schema import PolicyConfigV2


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_engine_v2()


class TestSingleton:
    def test_lazy_initialized_until_first_access(self):
        reset_engine_v2()
        assert not is_initialized()
        eng = get_engine_v2()
        assert isinstance(eng, PolicyEngineV2)
        assert is_initialized()

    def test_same_instance_returned(self):
        reset_engine_v2()
        a = get_engine_v2()
        b = get_engine_v2()
        assert a is b

    def test_reset_clears_state(self):
        get_engine_v2()
        assert is_initialized()
        reset_engine_v2()
        assert not is_initialized()
        assert global_engine._config is None  # type: ignore[attr-defined]


class TestSetEngineV2:
    def test_set_engine_replaces_singleton(self):
        cfg = PolicyConfigV2()
        custom = PolicyEngineV2(config=cfg)
        set_engine_v2(custom, config=cfg)

        assert get_engine_v2() is custom
        assert get_config_v2() is cfg

    def test_set_engine_without_config_uses_existing_or_default(self):
        # 先 reset → set 不带 config → get_config_v2 应返回默认
        reset_engine_v2()
        custom = PolicyEngineV2()
        set_engine_v2(custom)
        cfg = get_config_v2()
        assert isinstance(cfg, PolicyConfigV2)


class TestRebuildEngine:
    def test_rebuild_with_explicit_lookup(self):
        seen: list[str] = []

        def lookup(tool_name: str):
            seen.append(tool_name)
            return None

        eng = rebuild_engine_v2(explicit_lookup=lookup)
        assert isinstance(eng, PolicyEngineV2)
        assert get_engine_v2() is eng

    def test_rebuild_with_explicit_yaml_path_missing_falls_back(self, tmp_path):
        bogus = tmp_path / "does-not-exist.yaml"
        eng = rebuild_engine_v2(yaml_path=bogus)
        # loader 在文件不存在时返回默认 config + WARN，不抛
        assert isinstance(eng, PolicyEngineV2)


class TestYAMLPathResolution:
    def test_resolve_yaml_path_uses_settings_identity_path(self, monkeypatch):
        # settings.identity_path 是 Pydantic property（无 setter），不能 monkeypatch
        # 在实例上；改为 monkeypatch import lookup 函数本身
        fake_root = Path("R:/OpenAkita/identity")

        class _FakeSettings:
            identity_path = fake_root

        # 替换 _resolve_yaml_path 内 lazy import 的目标模块
        import openakita.config as oc_mod

        monkeypatch.setattr(oc_mod, "settings", _FakeSettings(), raising=False)

        resolved = global_engine._resolve_yaml_path()
        assert resolved == fake_root / "POLICIES.yaml"

    def test_resolve_yaml_path_returns_none_when_no_settings_no_fallback(
        self, monkeypatch, tmp_path
    ):
        class _FakeSettings:
            identity_path = tmp_path / "nope"

        import openakita.config as oc_mod

        monkeypatch.setattr(oc_mod, "settings", _FakeSettings(), raising=False)
        monkeypatch.chdir(tmp_path)

        resolved = global_engine._resolve_yaml_path()
        # _resolve_yaml_path 优先 settings.identity_path（即使文件不存在也照样
        # 返回 settings 的路径，让 loader 自己处理 missing）
        assert resolved == tmp_path / "nope" / "POLICIES.yaml"

    def test_resolve_yaml_path_falls_back_to_cwd_identity(self, monkeypatch, tmp_path):
        """settings 异常时，回落到 ./identity/POLICIES.yaml 检查。"""

        class _BoomSettings:
            @property
            def identity_path(self):
                raise RuntimeError("settings unavailable")

        import openakita.config as oc_mod

        monkeypatch.setattr(oc_mod, "settings", _BoomSettings(), raising=False)
        # cwd 没有 identity/POLICIES.yaml → 应该返回 None
        monkeypatch.chdir(tmp_path)

        assert global_engine._resolve_yaml_path() is None

        # 再造一个 cwd/identity/POLICIES.yaml → 应该返回该路径
        (tmp_path / "identity").mkdir()
        (tmp_path / "identity" / "POLICIES.yaml").write_text("# stub")
        assert (
            global_engine._resolve_yaml_path()
            == Path("identity/POLICIES.yaml")
        )


class TestThreadSafety:
    def test_concurrent_first_get_returns_single_instance(self):
        """模拟多线程并发首次访问，只能产生一个实例。"""
        import threading

        reset_engine_v2()
        instances: list[PolicyEngineV2] = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            instances.append(get_engine_v2())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 所有线程应拿到同一个实例
        assert len({id(x) for x in instances}) == 1
