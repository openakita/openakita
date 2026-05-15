"""C21 P0-2: ``POST /api/config/security`` deep-merge regression.

Background
==========

Pre-C21 ``write_security_config`` did ``data["security"] = body.security``
— full replace. The UI typically POSTs only the fields it renders. Any
field the UI doesn't render (``user_allowlist`` custom commands,
``hot_reload``, ``rotation``, ``aggregation_window_seconds``,
``audit.log_path``, etc.) silently disappeared from YAML and got filled
back in by loader defaults. **User-customized values lost on every
save**.

Plan §7.2 had specified deep-merge from day one but it was never
implemented. C21 P0-2 finally lands it, defaulting to merge with an
explicit ``?replace=true`` escape hatch.

Coverage
========

- Unit tests for ``_deep_merge_security`` semantics
- Integration tests via FastAPI ``TestClient`` against the real
  ``/api/config/security`` endpoint, with a monkey-patched project root
  pointing at ``tmp_path``
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import config as config_routes
from openakita.api.routes.config import _deep_merge_security


# ---------------------------------------------------------------------------
# Unit tests for _deep_merge_security
# ---------------------------------------------------------------------------


class TestDeepMergeUnit:
    def test_empty_source_preserves_target(self) -> None:
        target = {"a": 1, "b": {"c": 2}}
        _deep_merge_security(target, {})
        assert target == {"a": 1, "b": {"c": 2}}

    def test_primitive_source_overrides_target(self) -> None:
        target = {"mode": "trust"}
        _deep_merge_security(target, {"mode": "strict"})
        assert target == {"mode": "strict"}

    def test_dict_source_recurses(self) -> None:
        target = {"audit": {"enabled": True, "log_path": "old.jsonl"}}
        _deep_merge_security(target, {"audit": {"enabled": False}})
        assert target == {"audit": {"enabled": False, "log_path": "old.jsonl"}}

    def test_list_replaces_wholesale(self) -> None:
        """user_allowlist.commands semantics: POST new list = new list."""
        target = {"user_allowlist": {"commands": ["old1", "old2"]}}
        _deep_merge_security(target, {"user_allowlist": {"commands": ["new1"]}})
        assert target == {"user_allowlist": {"commands": ["new1"]}}

    def test_unmentioned_keys_preserved(self) -> None:
        """The whole point of the migration: don't drop unmentioned fields."""
        target = {
            "confirmation_mode": "trust",
            "user_allowlist": {"commands": ["my-cmd"]},
            "hot_reload": {"enabled": True},
            "audit": {"rotation_mode": "daily"},
        }
        _deep_merge_security(target, {"confirmation_mode": "strict"})
        assert target == {
            "confirmation_mode": "strict",
            "user_allowlist": {"commands": ["my-cmd"]},
            "hot_reload": {"enabled": True},
            "audit": {"rotation_mode": "daily"},
        }

    def test_type_change_source_wins(self) -> None:
        """If source replaces dict with list, source wins (no recursion)."""
        target = {"thing": {"sub": "x"}}
        _deep_merge_security(target, {"thing": ["a", "b"]})
        assert target == {"thing": ["a", "b"]}

    def test_none_value_replaces(self) -> None:
        """Explicit ``None`` in body overrides existing value."""
        target = {"mode": "trust"}
        _deep_merge_security(target, {"mode": None})
        assert target == {"mode": None}

    def test_nested_three_levels(self) -> None:
        target = {
            "audit": {"rotation": {"mode": "daily", "size_mb": 100}, "enabled": True}
        }
        _deep_merge_security(target, {"audit": {"rotation": {"size_mb": 200}}})
        assert target == {
            "audit": {"rotation": {"mode": "daily", "size_mb": 200}, "enabled": True}
        }

    def test_returns_same_target_reference(self) -> None:
        target: dict = {"a": 1}
        result = _deep_merge_security(target, {"b": 2})
        assert result is target


# ---------------------------------------------------------------------------
# Integration: POST /api/config/security
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``_project_root`` so identity/POLICIES.yaml writes go to tmp."""
    identity_dir = tmp_path / "identity"
    identity_dir.mkdir(parents=True, exist_ok=True)

    def fake_root() -> Path:
        return tmp_path

    monkeypatch.setattr(config_routes, "_project_root", fake_root)
    return identity_dir / "POLICIES.yaml"


@pytest.fixture
def api_client(isolated_yaml: Path, monkeypatch: pytest.MonkeyPatch):
    """Minimal FastAPI app with just config routes — no auth middleware."""
    # Avoid the policy_v2 reset_policy_v2_layer side-effect emitting SSE etc.
    # The endpoint try/excepts that whole block already, so a no-op is fine.
    from openakita.core.policy_v2 import global_engine

    monkeypatch.setattr(global_engine, "reset_policy_v2_layer", lambda scope="all": None)

    app = FastAPI()
    app.include_router(config_routes.router)
    with TestClient(app) as client:
        yield client


def _seed_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class TestEndpointMergeDefault:
    def test_partial_post_preserves_user_allowlist(
        self, api_client: TestClient, isolated_yaml: Path
    ) -> None:
        """The original user complaint: POST {confirmation_mode} wipes
        user_allowlist commands the operator carefully maintained."""
        _seed_yaml(
            isolated_yaml,
            {
                "security": {
                    "confirmation_mode": "trust",
                    "user_allowlist": {
                        "commands": ["custom-cmd-1", "custom-cmd-2"],
                        "tools": ["custom-tool"],
                    },
                    "hot_reload": {"enabled": True, "poll_interval_seconds": 5},
                    "audit": {"rotation_mode": "daily", "rotation_keep_count": 60},
                }
            },
        )
        r = api_client.post(
            "/api/config/security",
            json={"security": {"confirmation_mode": "strict"}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["mode"] == "merge"

        result = _read_yaml(isolated_yaml)
        sec = result["security"]
        assert sec["confirmation_mode"] == "strict"  # updated
        # All these MUST survive a partial POST.
        assert sec["user_allowlist"]["commands"] == ["custom-cmd-1", "custom-cmd-2"]
        assert sec["user_allowlist"]["tools"] == ["custom-tool"]
        assert sec["hot_reload"]["enabled"] is True
        assert sec["hot_reload"]["poll_interval_seconds"] == 5
        assert sec["audit"]["rotation_mode"] == "daily"
        assert sec["audit"]["rotation_keep_count"] == 60

    def test_nested_dict_merges_not_replaces(
        self, api_client: TestClient, isolated_yaml: Path
    ) -> None:
        """POST {audit: {enabled: false}} must not wipe audit.rotation_*."""
        _seed_yaml(
            isolated_yaml,
            {
                "security": {
                    "audit": {
                        "enabled": True,
                        "log_path": "data/audit/x.jsonl",
                        "rotation_mode": "size",
                        "rotation_size_mb": 50,
                    }
                }
            },
        )
        api_client.post(
            "/api/config/security",
            json={"security": {"audit": {"enabled": False}}},
        )
        sec = _read_yaml(isolated_yaml)["security"]
        assert sec["audit"]["enabled"] is False
        assert sec["audit"]["log_path"] == "data/audit/x.jsonl"
        assert sec["audit"]["rotation_mode"] == "size"
        assert sec["audit"]["rotation_size_mb"] == 50

    def test_list_inside_nested_replaces_wholesale(
        self, api_client: TestClient, isolated_yaml: Path
    ) -> None:
        """user_allowlist.commands list should replace, not append."""
        _seed_yaml(
            isolated_yaml,
            {"security": {"user_allowlist": {"commands": ["a", "b", "c"]}}},
        )
        api_client.post(
            "/api/config/security",
            json={"security": {"user_allowlist": {"commands": ["x"]}}},
        )
        sec = _read_yaml(isolated_yaml)["security"]
        assert sec["user_allowlist"]["commands"] == ["x"]

    def test_top_level_unmentioned_section_preserved(
        self, api_client: TestClient, isolated_yaml: Path
    ) -> None:
        """POST only touches ``security`` — other top-level YAML keys must
        not be affected. Sanity check against future regressions."""
        _seed_yaml(
            isolated_yaml,
            {
                "identity": {"name": "Akita"},
                "security": {"confirmation_mode": "trust"},
            },
        )
        api_client.post(
            "/api/config/security",
            json={"security": {"confirmation_mode": "strict"}},
        )
        result = _read_yaml(isolated_yaml)
        assert result["identity"] == {"name": "Akita"}
        assert result["security"]["confirmation_mode"] == "strict"


class TestEndpointReplaceEscape:
    def test_replace_true_wipes_entire_security(
        self, api_client: TestClient, isolated_yaml: Path
    ) -> None:
        """``?replace=true`` opts into legacy full-replace semantics for
        operators who actually want a reset-to-default flow."""
        _seed_yaml(
            isolated_yaml,
            {
                "security": {
                    "confirmation_mode": "trust",
                    "user_allowlist": {"commands": ["a", "b"]},
                    "hot_reload": {"enabled": True},
                }
            },
        )
        r = api_client.post(
            "/api/config/security?replace=true",
            json={"security": {"confirmation_mode": "strict"}},
        )
        assert r.json()["mode"] == "replace"
        sec = _read_yaml(isolated_yaml)["security"]
        assert sec == {"confirmation_mode": "strict"}
        assert "user_allowlist" not in sec
        assert "hot_reload" not in sec


class TestEdgeCases:
    def test_security_was_not_a_dict_treated_as_empty(
        self, api_client: TestClient, isolated_yaml: Path
    ) -> None:
        """Corrupt YAML where ``security`` is somehow a string. Should
        not raise — merge falls back to empty dict, source wins entirely."""
        _seed_yaml(isolated_yaml, {"security": "corrupted"})
        r = api_client.post(
            "/api/config/security",
            json={"security": {"confirmation_mode": "strict"}},
        )
        assert r.status_code == 200
        sec = _read_yaml(isolated_yaml)["security"]
        assert sec == {"confirmation_mode": "strict"}

    def test_security_missing_creates_fresh(
        self, api_client: TestClient, isolated_yaml: Path
    ) -> None:
        """If YAML has no ``security`` key yet, POST seeds it cleanly."""
        _seed_yaml(isolated_yaml, {"identity": {"name": "Akita"}})
        api_client.post(
            "/api/config/security",
            json={"security": {"confirmation_mode": "strict"}},
        )
        result = _read_yaml(isolated_yaml)
        assert result["identity"] == {"name": "Akita"}
        assert result["security"] == {"confirmation_mode": "strict"}

    def test_empty_body_security_no_op(
        self, api_client: TestClient, isolated_yaml: Path
    ) -> None:
        """POST {security: {}} is a no-op on existing fields."""
        _seed_yaml(
            isolated_yaml,
            {"security": {"confirmation_mode": "trust", "audit": {"enabled": True}}},
        )
        api_client.post("/api/config/security", json={"security": {}})
        sec = _read_yaml(isolated_yaml)["security"]
        assert sec["confirmation_mode"] == "trust"
        assert sec["audit"]["enabled"] is True
