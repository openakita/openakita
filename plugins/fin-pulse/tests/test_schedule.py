"""Phase 4c tests — on_schedule prompt plumbing.

The scheduler integration itself is hard to unit test (live
:class:`TaskScheduler` needs the full host bootstrap) so we constrain
this suite to the pieces we *can* exercise in isolation:

* Prompt parsing (``[fin-pulse] <json>``).
* Match predicate (``_is_finpulse_schedule``).
* Serializer (handles missing attributes / enum status / None next_run).
* Radar key hash stability.

The live-fire integration test runs in the ``test_smoke`` ui contract
and in the VALIDATION manual probe (§0.1 in the plan).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

# Load plugin.py as a module even though Python 3.9 in the CI runner
# refuses to import ``openakita.plugins.api`` (StrEnum). We stub the
# symbols we reach for so the parse helpers import cleanly.


def _load_plugin_module() -> types.ModuleType:
    plugin_path = Path(__file__).resolve().parent.parent / "plugin.py"
    mod_name = "fin_pulse_plugin_test_isolated"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    # Stub openakita.plugins.api so ``from openakita.plugins.api import
    # PluginAPI, PluginBase`` succeeds.
    openakita_pkg = types.ModuleType("openakita")
    plugins_pkg = types.ModuleType("openakita.plugins")
    api_mod = types.ModuleType("openakita.plugins.api")

    class _PluginAPI:  # pragma: no cover — marker only
        pass

    class _PluginBase:  # pragma: no cover
        pass

    api_mod.PluginAPI = _PluginAPI
    api_mod.PluginBase = _PluginBase
    plugins_pkg.api = api_mod
    openakita_pkg.plugins = plugins_pkg

    sys.modules.setdefault("openakita", openakita_pkg)
    sys.modules.setdefault("openakita.plugins", plugins_pkg)
    sys.modules.setdefault("openakita.plugins.api", api_mod)

    spec = importlib.util.spec_from_file_location(mod_name, plugin_path)
    assert spec and spec.loader, "plugin.py must be importable as a module"
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


plugin_mod = _load_plugin_module()


# ── Match predicate ──────────────────────────────────────────────────


class _Task:
    def __init__(self, *, name: str = "", prompt: str = "") -> None:
        self.name = name
        self.prompt = prompt


def test_match_rejects_non_finpulse_tasks() -> None:
    assert plugin_mod._is_finpulse_schedule() is False
    assert plugin_mod._is_finpulse_schedule(task=None) is False
    assert plugin_mod._is_finpulse_schedule(task=_Task(name="system:daily_memory")) is False
    assert (
        plugin_mod._is_finpulse_schedule(task=_Task(name="other-plugin:morning", prompt="whatever"))
        is False
    )


def test_match_accepts_finpulse_prefix() -> None:
    task = _Task(name="fin-pulse:morning", prompt="[fin-pulse] {}")
    assert plugin_mod._is_finpulse_schedule(task=task) is True


def test_match_accepts_prompt_prefix_even_without_name() -> None:
    task = _Task(name="", prompt='[fin-pulse] {"mode":"daily_brief"}')
    assert plugin_mod._is_finpulse_schedule(task=task) is True


# ── Prompt parser ────────────────────────────────────────────────────


def test_parse_prompt_strips_prefix_and_decodes_json() -> None:
    data = plugin_mod._parse_schedule_prompt(
        '[fin-pulse] {"mode":"daily_brief","session":"morning"}'
    )
    assert data["mode"] == "daily_brief"
    assert data["session"] == "morning"


def test_parse_prompt_accepts_bare_json() -> None:
    data = plugin_mod._parse_schedule_prompt('{"mode":"hot_radar"}')
    assert data == {"mode": "hot_radar"}


def test_parse_prompt_rejects_empty() -> None:
    with pytest.raises(ValueError):
        plugin_mod._parse_schedule_prompt("")
    with pytest.raises(ValueError):
        plugin_mod._parse_schedule_prompt("[fin-pulse] ")


def test_parse_prompt_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        plugin_mod._parse_schedule_prompt('[fin-pulse] "just-a-string"')
    with pytest.raises(ValueError):
        plugin_mod._parse_schedule_prompt("[fin-pulse] [1, 2, 3]")


def test_parse_prompt_rejects_broken_json() -> None:
    with pytest.raises(ValueError):
        plugin_mod._parse_schedule_prompt("[fin-pulse] {not valid")


# ── Serializer ───────────────────────────────────────────────────────


class _FullTask:
    def __init__(self) -> None:
        self.id = "task_abc"
        self.name = "fin-pulse:morning"
        self.description = "morning brief"
        self.prompt = '[fin-pulse] {"mode":"daily_brief","session":"morning"}'
        self.enabled = True
        self.status = "scheduled"
        self.next_run = None
        self.run_count = 3
        self.fail_count = 0
        self.channel_id = "feishu"
        self.chat_id = "oc_xxx"
        self.trigger_config = {"cron": "0 9 * * 1-5"}


def test_serialize_schedule_shape() -> None:
    out = plugin_mod._serialize_schedule(_FullTask())
    assert out["id"] == "task_abc"
    assert out["name"].startswith("fin-pulse:")
    assert out["cron"] == "0 9 * * 1-5"
    assert out["channel"] == "feishu"
    assert out["chat_id"] == "oc_xxx"
    assert out["mode"] == "daily_brief"
    assert out["session"] == "morning"
    assert out["enabled"] is True
    assert out["next_run"] is None


def test_serialize_schedule_handles_missing_attrs() -> None:
    class _Sparse:
        name = "fin-pulse:radar:abc"

    out = plugin_mod._serialize_schedule(_Sparse())
    assert out["id"] == ""
    assert out["cron"] == ""
    assert out["mode"] is None
    assert out["session"] is None


# ── Radar key hashing ────────────────────────────────────────────────


def test_radar_key_is_stable_and_short() -> None:
    key = plugin_mod._radar_key("+美联储\n+欧央行\n")
    assert len(key) == 8
    assert plugin_mod._radar_key("+美联储\n+欧央行\n") == key
    different = plugin_mod._radar_key("+美联储\n")
    assert different != key


def test_radar_key_handles_empty_input() -> None:
    key = plugin_mod._radar_key("")
    assert len(key) == 8
