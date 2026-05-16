import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import bug_report
from openakita.api.routes import config as config_routes
from openakita.llm.capabilities import infer_capabilities, is_image_generation_model
from openakita.llm.runtime_config import apply_llm_runtime_config


class _FakeEndpointManager:
    def __init__(self) -> None:
        self.saved_api_key = "unset"
        self.saved_endpoint = None
        self.enabled = False
        self.endpoints = []
        self.deleted_endpoint = None

    def save_endpoint(
        self,
        *,
        endpoint: dict,
        api_key: str | None = None,
        endpoint_type: str = "endpoints",
        expected_version: str | None = None,
        original_name: str | None = None,
    ) -> dict:
        self.saved_api_key = api_key
        self.saved_endpoint = dict(endpoint)
        saved = dict(endpoint)
        saved.setdefault("api_key_env", "OPENAI_API_KEY")
        saved["endpoint_type"] = endpoint_type
        return saved

    def save_endpoints(
        self,
        *,
        endpoints: list[dict],
        api_key: str | None = None,
        endpoint_type: str = "endpoints",
        expected_version: str | None = None,
    ) -> list[dict]:
        self.saved_api_key = api_key
        saved = []
        for endpoint in endpoints:
            item = dict(endpoint)
            item.setdefault("api_key_env", "OPENAI_API_KEY")
            item["endpoint_type"] = endpoint_type
            saved.append(item)
        self.endpoints = saved
        return saved

    def list_endpoints(self, endpoint_type: str = "endpoints") -> list[dict]:
        return list(self.endpoints)

    def get_version(self) -> str:
        return "test-version"

    def toggle_endpoint(self, name: str, endpoint_type: str = "endpoints") -> dict:
        self.enabled = not self.enabled
        return {"name": name, "endpoint_type": endpoint_type, "enabled": self.enabled}

    def delete_endpoint(
        self,
        name: str,
        endpoint_type: str = "endpoints",
        clean_env: bool = True,
    ) -> dict | None:
        self.deleted_endpoint = {
            "name": name,
            "endpoint_type": endpoint_type,
            "clean_env": clean_env,
        }
        return {"name": name, "api_key_env": "STT_API_KEY"}


@pytest.mark.asyncio
async def test_save_endpoint_returns_ok_when_runtime_reload_fails(monkeypatch):
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(
        config_routes,
        "_trigger_reload",
        lambda request: {"status": "failed", "reloaded": False, "reason": "boom"},
    )

    response = await config_routes.save_endpoint(
        config_routes.SaveEndpointRequest(
            endpoint={"name": "primary", "provider": "openai", "model": "gpt-4"},
            api_key="sk-real",
        ),
        SimpleNamespace(),
    )

    assert response["status"] == "ok"
    assert response["saved"] is True
    assert response["reload"]["status"] == "failed"
    assert "配置已保存" in response["warning"]
    assert manager.saved_api_key == "sk-real"


@pytest.mark.asyncio
async def test_path_policy_writes_v2_fields_without_legacy_zones(monkeypatch):
    state = {"security": {"zones": {"workspace": ["old"], "default_zone": "controlled"}}}
    written = {}
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(config_routes, "_write_policies_yaml", lambda data: written.update(data) or True)

    response = await config_routes.write_security_path_policy(
        config_routes.SecurityPathPolicyUpdate(
            workspace_paths=["C:/Users/me/Desktop"],
            safety_immune_paths=["C:/Users/me/.ssh"],
        )
    )

    assert response["status"] == "ok"
    assert written["security"]["workspace"]["paths"] == ["C:/Users/me/Desktop"]
    assert written["security"]["safety_immune"]["paths"] == ["C:/Users/me/.ssh"]
    assert "zones" not in written["security"]
    assert written["security"]["profile"]["current"] == "custom"


@pytest.mark.asyncio
async def test_security_profile_off_requires_exact_ack(monkeypatch):
    state = {"security": {"profile": {"current": "protect"}}}
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))

    response = await config_routes.write_security_profile(
        config_routes.SecurityProfileUpdate(profile="off", ack_phrase="我知道风险")
    )

    assert response["status"] == "error"


@pytest.mark.asyncio
async def test_security_profile_off_disables_security_enabled(monkeypatch):
    """切到 off 时 security.enabled 必须同步置 false（避免双总开关漂移）。"""
    state = {"security": {"profile": {"current": "protect"}, "enabled": True}}
    written = {}
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(config_routes, "_write_policies_yaml", lambda data: written.update(data) or True)

    response = await config_routes.write_security_profile(
        config_routes.SecurityProfileUpdate(profile="off", ack_phrase=config_routes._SECURITY_PROFILE_OFF_ACK)
    )

    assert response["status"] == "ok"
    assert written["security"]["profile"]["current"] == "off"
    assert written["security"]["enabled"] is False


@pytest.mark.asyncio
async def test_security_profile_trust_reenables_security_enabled(monkeypatch):
    """从 off 切到 trust 时 security.enabled 必须复位 true。"""
    state = {"security": {"profile": {"current": "off", "base": "protect"}, "enabled": False}}
    written = {}
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(config_routes, "_write_policies_yaml", lambda data: written.update(data) or True)

    response = await config_routes.write_security_profile(
        config_routes.SecurityProfileUpdate(profile="trust")
    )

    assert response["status"] == "ok"
    assert written["security"]["profile"]["current"] == "trust"
    assert written["security"]["enabled"] is True


@pytest.mark.asyncio
async def test_security_confirmation_read_normalizes_legacy_mode(monkeypatch):
    """GET must return v2 values so the setup-center Select does not render blank."""
    state = {"security": {"profile": {"current": "trust"}, "confirmation": {"mode": "yolo"}}}
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))

    response = await config_routes.read_security_confirmation()

    assert response["mode"] == "trust"


@pytest.mark.asyncio
async def test_security_preview_uses_current_yaml_not_stale_global_engine(monkeypatch):
    """Dry-run should reflect the YAML that SecurityView just read.

    This catches the UI mismatch where the card showed trust but the preview
    still reported protect/default from a stale global engine cache.
    """
    state = {"security": {"profile": {"current": "trust"}, "confirmation": {"mode": "trust"}}}
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))

    response = await config_routes.preview_security_config({})

    assert response["preview_uses_proposed"] is False
    run_shell = next(item for item in response["decisions"] if item["tool"] == "run_shell" and "ls" in item["params_preview"])
    assert run_shell["effective_confirmation_mode"] == "trust"
    assert run_shell["security_profile"] == "trust"
    assert run_shell["decision"] == "allow"


@pytest.mark.asyncio
async def test_commands_api_writes_shell_risk_not_legacy(monkeypatch):
    """write_security_commands 必须写到 security.shell_risk，并彻底清理 legacy command_patterns。"""
    state = {
        "security": {
            "command_patterns": {"custom_critical": ["legacy-only"], "blocked_commands": ["legacy"]},
        }
    }
    written = {}
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(config_routes, "_write_policies_yaml", lambda data: written.update(data) or True)

    response = await config_routes.write_security_commands(
        config_routes.SecurityCommandsUpdate(
            custom_critical=["rm -rf /"],
            custom_high=["sudo rm"],
            excluded_patterns=[],
            blocked_commands=["bcdedit"],
        )
    )

    assert response["status"] == "ok"
    assert written["security"]["shell_risk"]["custom_critical"] == ["rm -rf /"]
    assert written["security"]["shell_risk"]["blocked_commands"] == ["bcdedit"]
    # legacy 子树彻底丢弃
    assert "command_patterns" not in written["security"]
    # 触发 custom profile
    assert written["security"]["profile"]["current"] == "custom"


@pytest.mark.asyncio
async def test_commands_api_reads_legacy_fallback(monkeypatch):
    """老 YAML 只有 command_patterns 时，GET 仍能读出来（read fallback）。"""
    state = {
        "security": {
            "command_patterns": {
                "custom_critical": ["legacy-c"],
                "custom_high": ["legacy-h"],
                "excluded_patterns": [],
                "blocked_commands": ["legacy-bc"],
            }
        }
    }
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))

    response = await config_routes.read_security_commands()

    assert response["custom_critical"] == ["legacy-c"]
    assert response["custom_high"] == ["legacy-h"]
    assert response["blocked_commands"] == ["legacy-bc"]


@pytest.mark.asyncio
async def test_self_protection_api_writes_v2_blocks(monkeypatch):
    """write_self_protection 必须分发到 death_switch/audit/safety_immune，丢弃 legacy self_protection。"""
    state = {
        "security": {
            "self_protection": {"protected_dirs": ["data/"], "death_switch_threshold": 5},
        }
    }
    written = {}
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(config_routes, "_write_policies_yaml", lambda data: written.update(data) or True)

    response = await config_routes.write_self_protection(
        config_routes._SelfProtectionUpdate(
            enabled=True,
            protected_dirs=["data/", "identity/"],
            death_switch_threshold=4,
            death_switch_total_multiplier=2,
            audit_to_file=True,
            audit_path="data/audit/custom.jsonl",
        )
    )

    assert response["status"] == "ok"
    sec_w = written["security"]
    assert sec_w["death_switch"]["enabled"] is True
    assert sec_w["death_switch"]["threshold"] == 4
    assert sec_w["death_switch"]["total_multiplier"] == 2
    assert sec_w["safety_immune"]["paths"] == ["data/", "identity/"]
    assert sec_w["audit"]["enabled"] is True
    assert sec_w["audit"]["log_path"] == "data/audit/custom.jsonl"
    assert "self_protection" not in sec_w
    assert sec_w["profile"]["current"] == "custom"


@pytest.mark.asyncio
async def test_granular_write_during_off_leaves_audit_event(monkeypatch):
    """off 状态下任何细粒度写入都会被提升为 custom 并留下审计事件。"""
    state = {
        "security": {
            "profile": {"current": "off", "base": "protect"},
            "enabled": False,
        }
    }
    written = {}
    audit_calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(config_routes, "_write_policies_yaml", lambda data: written.update(data) or True)
    monkeypatch.setattr(
        config_routes,
        "_write_profile_event",
        lambda profile, previous=None: audit_calls.append((profile, previous)),
    )

    response = await config_routes.write_security_commands(
        config_routes.SecurityCommandsUpdate(
            custom_critical=["dd if=/dev/zero"],
            custom_high=[],
            excluded_patterns=[],
            blocked_commands=[],
        )
    )

    assert response["status"] == "ok"
    assert written["security"]["profile"]["current"] == "custom"
    assert written["security"]["enabled"] is True
    assert ("custom", "off") in audit_calls, (
        f"off → custom 必须写一条 profile_change 审计事件, got {audit_calls}"
    )


@pytest.mark.asyncio
async def test_permission_mode_escape_from_off_is_audited(monkeypatch):
    """老 chat /api/config/permission-mode 把用户从 off 拽到 baked profile 时必须留审计。"""
    state = {
        "security": {
            "profile": {"current": "off", "base": "protect"},
            "enabled": False,
        }
    }
    written = {}
    audit_calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(config_routes, "_write_policies_yaml", lambda data: written.update(data) or True)
    monkeypatch.setattr(
        config_routes,
        "_write_profile_event",
        lambda profile, previous=None: audit_calls.append((profile, previous)),
    )
    monkeypatch.setattr(
        config_routes,
        "reset_policy_v2_layer",
        lambda **kwargs: None,
        raising=False,
    )

    result = await config_routes.write_permission_mode(config_routes._PermissionModeBody(mode="smart"))

    assert result["status"] == "ok"
    # 状态被强行拉到 protect（=smart 的 v2 等价）
    assert written["security"]["profile"]["current"] == "protect"
    assert written["security"]["enabled"] is True
    assert any(
        target == "protect" and prev == "off" for (target, prev) in audit_calls
    ), f"off → protect 必须有 profile_change 事件, got {audit_calls}"


@pytest.mark.asyncio
async def test_self_protection_api_reads_legacy_fallback(monkeypatch):
    state = {
        "security": {
            "self_protection": {
                "enabled": True,
                "protected_dirs": ["legacy-dir"],
                "death_switch_threshold": 7,
                "death_switch_total_multiplier": 8,
                "audit_to_file": False,
                "audit_path": "legacy.jsonl",
            }
        }
    }
    monkeypatch.setattr(config_routes, "_read_policies_yaml", lambda: json.loads(json.dumps(state)))

    response = await config_routes.read_self_protection()

    assert response["protected_dirs"] == ["legacy-dir"]
    assert response["death_switch_threshold"] == 7
    assert response["death_switch_total_multiplier"] == 8
    assert response["audit_to_file"] is False
    assert response["audit_path"] == "legacy.jsonl"


@pytest.mark.asyncio
async def test_save_endpoint_ignores_masked_api_key(monkeypatch):
    manager = _FakeEndpointManager()
    manager.endpoints = [
        {"name": "primary", "provider": "openai", "model": "gpt-4", "api_key_env": "OPENAI_API_KEY"}
    ]
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-existing")
    monkeypatch.setattr(
        config_routes,
        "_trigger_reload",
        lambda request: {"status": "ok", "reloaded": True},
    )

    response = await config_routes.save_endpoint(
        config_routes.SaveEndpointRequest(
            endpoint={"name": "primary", "provider": "openai", "model": "gpt-4"},
            api_key="sk-****abcd",
        ),
        SimpleNamespace(),
    )

    assert response["status"] == "ok"
    assert "warning" not in response
    assert manager.saved_api_key is None


@pytest.mark.asyncio
async def test_save_endpoint_rejects_openrouter_without_api_key(monkeypatch):
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)

    response = await config_routes.save_endpoint(
        config_routes.SaveEndpointRequest(
            endpoint={
                "name": "openrouter-free",
                "provider": "openrouter",
                "api_type": "openai",
                "base_url": "https://openrouter.ai/api/v1",
                "model": "openrouter/free",
            },
            api_key=None,
        ),
        SimpleNamespace(),
    )

    assert response["status"] == "error"
    assert "OpenRouter" in response["error"]
    assert "API Key" in response["error"]
    assert manager.saved_api_key == "unset"
    assert manager.saved_endpoint is None


@pytest.mark.asyncio
async def test_save_endpoint_accepts_openrouter_router_and_free_model_with_key(monkeypatch):
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(
        config_routes,
        "_trigger_reload",
        lambda request: {"status": "ok", "reloaded": True},
    )

    for model in ("openrouter/auto", "mistralai/mistral-small-3.2-24b-instruct:free"):
        response = await config_routes.save_endpoint(
            config_routes.SaveEndpointRequest(
                endpoint={
                    "name": f"openrouter-{model.split('/')[-1][:12]}",
                    "provider": "openrouter",
                    "api_type": "openai",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": model,
                },
                api_key="sk-openrouter",
            ),
            SimpleNamespace(),
        )

        assert response["status"] == "ok"
        assert response["endpoint"]["model"] == model
        assert manager.saved_api_key == "sk-openrouter"


def test_qwen_image_is_not_inferred_as_chat_or_vision_model():
    caps = infer_capabilities("qwen-image-max", provider_slug="dashscope")

    assert is_image_generation_model("qwen-image-2.0")
    assert caps["image_generation"] is True
    assert caps["text"] is False
    assert caps["vision"] is False
    assert caps["tools"] is False


@pytest.mark.asyncio
async def test_save_endpoint_rejects_qwen_image_as_chat_endpoint(monkeypatch):
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)

    response = await config_routes.save_endpoint(
        config_routes.SaveEndpointRequest(
            endpoint={
                "name": "dashscope-qwen-image",
                "provider": "dashscope",
                "api_type": "openai",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-image-max",
                "capabilities": ["text", "vision", "tools"],
            },
            api_key="sk-dashscope",
        ),
        SimpleNamespace(),
    )

    assert response["status"] == "error"
    assert "图片生成模型" in response["error"]
    assert "generate_image" in response["error"]
    assert manager.saved_api_key == "unset"
    assert manager.saved_endpoint is None


@pytest.mark.asyncio
async def test_save_endpoints_batch_returns_saved_endpoints(monkeypatch):
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(
        config_routes,
        "_trigger_reload",
        lambda request: {"status": "ok", "reloaded": True},
    )

    response = await config_routes.save_endpoints(
        config_routes.SaveEndpointsRequest(
            endpoints=[
                {"name": "openai-gpt-4o", "provider": "openai", "model": "gpt-4o"},
                {"name": "openai-gpt-4o-mini", "provider": "openai", "model": "gpt-4o-mini"},
            ],
            api_key="sk-batch",
        ),
        SimpleNamespace(),
    )

    assert response["status"] == "ok"
    assert response["count"] == 2
    assert [ep["model"] for ep in response["endpoints"]] == ["gpt-4o", "gpt-4o-mini"]
    assert manager.saved_api_key == "sk-batch"


@pytest.mark.asyncio
async def test_save_endpoints_rejects_below_two_chat_endpoints_when_flag(monkeypatch):
    """P1-5: batch save 至少需要 2 条启用的 chat endpoint（可被 feature flag 关闭）。"""
    from openakita.core.feature_flags import clear_overrides, set_flag

    clear_overrides()
    set_flag("llm_view_min_endpoints_v1", True)
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(
        config_routes,
        "_trigger_reload",
        lambda request: {"status": "ok", "reloaded": True},
    )
    try:
        resp = await config_routes.save_endpoints(
            config_routes.SaveEndpointsRequest(
                endpoints=[
                    {"name": "solo", "provider": "openai", "model": "gpt-4o"},
                ],
                api_key="sk-x",
            ),
            SimpleNamespace(),
        )
        assert resp["status"] == "error"
        assert "2" in resp.get("error", "")
        assert manager.endpoints == []
    finally:
        clear_overrides()


@pytest.mark.asyncio
async def test_toggle_endpoint_returns_runtime_reload_result(monkeypatch):
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(
        config_routes,
        "_trigger_reload",
        lambda request: {
            "status": "ok",
            "reloaded": True,
            "main_reloaded": True,
            "pool_invalidated": True,
        },
    )

    response = await config_routes.toggle_endpoint(
        config_routes.ToggleEndpointRequest(name="primary"),
        SimpleNamespace(),
    )

    assert response["status"] == "ok"
    assert response["endpoint"]["enabled"] is True
    assert response["reload"]["main_reloaded"] is True
    assert response["reload"]["pool_invalidated"] is True


def test_delete_stt_endpoint_accepts_slash_in_name(monkeypatch):
    manager = _FakeEndpointManager()
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(config_routes, "_trigger_reload", lambda request: {"status": "ok"})

    app = FastAPI()
    app.include_router(config_routes.router)
    client = TestClient(app)

    endpoint_name = "stt-openrouter-openrouter/free"
    response = client.delete(
        f"/api/config/endpoint/{endpoint_name.replace('/', '%2F')}?endpoint_type=stt_endpoints"
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert manager.deleted_endpoint == {
        "name": endpoint_name,
        "endpoint_type": "stt_endpoints",
        "clean_env": True,
    }


def test_delete_last_chat_endpoint_is_allowed_when_min_endpoint_flag_is_on(monkeypatch):
    from openakita.core.feature_flags import clear_overrides, set_flag

    clear_overrides()
    set_flag("llm_view_min_endpoints_v1", True)
    manager = _FakeEndpointManager()
    manager.endpoints = [{"name": "solo", "provider": "dashscope", "model": "qwen3"}]
    monkeypatch.setattr(config_routes, "_get_endpoint_manager", lambda: manager)
    monkeypatch.setattr(config_routes, "_trigger_reload", lambda request: {"status": "ok"})

    app = FastAPI()
    app.include_router(config_routes.router)
    client = TestClient(app)

    try:
        response = client.delete("/api/config/endpoint/solo?endpoint_type=endpoints")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert manager.deleted_endpoint == {
            "name": "solo",
            "endpoint_type": "endpoints",
            "clean_env": True,
        }
    finally:
        clear_overrides()


def test_apply_llm_runtime_config_refreshes_all_runtime_components(tmp_path, monkeypatch):
    config_path = tmp_path / "data" / "llm_endpoints.json"
    config_path.parent.mkdir()
    config_path.write_text('{"endpoints": [], "stt_endpoints": []}', encoding="utf-8")

    class FakeClient:
        def __init__(self) -> None:
            self._config_path = None
            self.endpoints = [object()]
            self.reload_called = False

        def reload(self) -> bool:
            self.reload_called = True
            return True

    class FakeBrain:
        def __init__(self) -> None:
            self._llm_client = FakeClient()
            self.compiler_reloaded = False

        def reload_compiler_client(self) -> bool:
            self.compiler_reloaded = True
            return True

    class FakeSttClient:
        def __init__(self) -> None:
            self.reloaded_with = None

        def reload(self, endpoints) -> None:
            self.reloaded_with = endpoints

    class FakePool:
        def __init__(self) -> None:
            self.reason = None

        def notify_runtime_config_changed(self, reason: str) -> None:
            self.reason = reason

    brain = FakeBrain()
    gateway = SimpleNamespace(stt_client=FakeSttClient())
    pool = FakePool()

    monkeypatch.setattr(
        "openakita.llm.config.load_endpoints_config",
        lambda path=None: ([], [], ["stt"], {}),
    )

    result = apply_llm_runtime_config(
        agent=SimpleNamespace(brain=brain),
        gateway=gateway,
        pool=pool,
        config_path=config_path,
        reason="llm_config:test",
    )

    assert result["status"] == "ok"
    assert result["main_reloaded"] is True
    assert result["compiler_reloaded"] is True
    assert result["stt_reloaded"] is True
    assert result["pool_invalidated"] is True
    assert brain._llm_client.reload_called is True
    assert brain._llm_client._config_path == config_path
    assert brain.compiler_reloaded is True
    assert gateway.stt_client.reloaded_with == ["stt"]
    assert pool.reason == "llm_config:test"


def test_collect_endpoint_summary_redacts_keys_and_keeps_diagnostic_fields(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_path = data_dir / "llm_endpoints.json"
    config_path.write_text(
        json.dumps(
            {
                "endpoints": [
                    {
                        "name": "primary",
                        "provider": "openai",
                        "api_type": "openai",
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4",
                        "api_key_env": "OPENAI_API_KEY",
                        "context_window": 128000,
                    }
                ],
                "compiler_endpoints": [],
                "stt_endpoints": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-real-secret\n", encoding="utf-8")

    import openakita.llm.config as llm_config

    monkeypatch.setattr(llm_config, "get_default_config_path", lambda: config_path)

    summary = bug_report._collect_endpoint_summary()

    assert summary["counts"] == {"endpoints": 1, "compiler_endpoints": 0, "stt_endpoints": 0}
    endpoint = summary["endpoints"][0]
    assert endpoint["base_url_host"] == "api.openai.com"
    assert endpoint["key_present"] is True
    assert "sk-real-secret" not in json.dumps(summary)
