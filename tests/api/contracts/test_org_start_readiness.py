"""Start-readiness contract for plugin-backed organizations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.api.contracts.conftest import _async_return


def _node(*, plugin_id: str | None, tools: list[str]) -> SimpleNamespace:
    origin = {"plugin_id": plugin_id} if plugin_id else None
    return SimpleNamespace(
        id="workbench",
        plugin_origin=origin,
        external_tools=tools,
    )


def test_start_readiness_allows_org_without_workbench_plugins(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_manager.get.return_value = SimpleNamespace(
        nodes=[_node(plugin_id=None, tools=["research"])]
    )

    response = mint_client.get("/api/v2/orgs/o1/start-readiness")

    assert response.status_code == 200
    assert response.json() == {"ready": True, "issues": []}


def test_start_rejects_missing_workbench_plugin_without_mutating_state(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_manager.get.return_value = SimpleNamespace(
        nodes=[_node(plugin_id="happyhorse-video", tools=["hh_t2v"])]
    )
    plugin_manager = MagicMock()
    plugin_manager.get_loaded.return_value = None
    mint_app.state.agent = SimpleNamespace(_plugin_manager=plugin_manager)
    mint_app.state.org_runtime.start_org = _async_return({"status": "active"})

    response = mint_client.post("/api/v2/orgs/o1/start")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "org_start_not_ready"
    assert detail["issues"] == [
        {
            "code": "plugin_not_loaded",
            "plugin_id": "happyhorse-video",
            "node_ids": ["workbench"],
        }
    ]
    mint_app.state.org_runtime.start_org.assert_not_called()


def test_start_readiness_reports_missing_tools_and_plugin_requirements(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_manager.get.return_value = SimpleNamespace(
        nodes=[
            _node(
                plugin_id="happyhorse-video",
                tools=["hh_t2v", "hh_video_concat"],
            )
        ]
    )
    instance = SimpleNamespace(
        check_org_readiness=lambda: {
            "ready": False,
            "missing_requirements": ["dashscope_api_key", "oss"],
        }
    )
    loaded = SimpleNamespace(
        manifest=SimpleNamespace(provides={"tools": ["hh_t2v", {"name": "hh_video_concat"}]}),
        api=SimpleNamespace(_registered_tools=["hh_t2v"]),
        instance=instance,
    )
    plugin_manager = MagicMock()
    plugin_manager.get_loaded.return_value = loaded
    mint_app.state.agent = SimpleNamespace(_plugin_manager=plugin_manager)

    response = mint_client.get("/api/v2/orgs/o1/start-readiness")

    assert response.status_code == 200
    assert response.json() == {
        "ready": False,
        "issues": [
            {
                "code": "plugin_tools_missing",
                "plugin_id": "happyhorse-video",
                "missing_tools": ["hh_video_concat"],
            },
            {
                "code": "plugin_requirements_missing",
                "plugin_id": "happyhorse-video",
                "missing_requirements": ["dashscope_api_key", "oss"],
            },
        ],
    }


def test_start_proceeds_when_workbench_plugin_is_ready(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_manager.get.return_value = SimpleNamespace(
        nodes=[_node(plugin_id="happyhorse-video", tools=["hh_t2v"])]
    )
    loaded = SimpleNamespace(
        manifest=SimpleNamespace(provides={"tools": ["hh_t2v"]}),
        api=SimpleNamespace(_registered_tools=["hh_t2v"]),
        instance=SimpleNamespace(
            check_org_readiness=lambda: {"ready": True, "missing_requirements": []}
        ),
    )
    plugin_manager = MagicMock()
    plugin_manager.get_loaded.return_value = loaded
    mint_app.state.agent = SimpleNamespace(_plugin_manager=plugin_manager)
    mint_app.state.org_runtime.start_org = _async_return({"status": "active"})

    response = mint_client.post("/api/v2/orgs/o1/start")

    assert response.status_code == 200
    assert response.json() == {"status": "active"}
    mint_app.state.org_runtime.start_org.assert_called_once_with("o1")


def test_command_rechecks_readiness_after_plugin_is_unloaded(
    mint_app: FastAPI, mint_client: TestClient
) -> None:
    mint_app.state.org_manager.get.return_value = SimpleNamespace(
        nodes=[_node(plugin_id="happyhorse-video", tools=["hh_t2v"])]
    )
    plugin_manager = MagicMock()
    plugin_manager.get_loaded.return_value = None
    mint_app.state.agent = SimpleNamespace(_plugin_manager=plugin_manager)

    response = mint_client.post(
        "/api/v2/orgs/o1/command",
        json={"content": "make a video"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "org_start_not_ready"
    mint_app.state.org_command_service.submit.assert_not_called()
