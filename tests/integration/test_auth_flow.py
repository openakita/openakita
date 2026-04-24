"""认证流程端到端集成测试。

覆盖 v1.25.9 修复的多个认证相关 bug：
1. 本地直连请求免认证（127.0.0.1 无 X-Forwarded-For）
2. 反向代理模式（TRUST_PROXY=true）+ 有 X-Forwarded-For → 需要认证
3. 反向代理模式 + 无 X-Forwarded-For（Tauri 桌面端直连）→ 仍免认证
4. trust_proxy 动态读取（改环境变量后无需重启即生效）
5. WebSocket 认证与 HTTP 一致（/ws/events）
6. /web/ 路径始终免认证
7. /api/health 免认证
8. /api/config 需要认证
9. 记忆管理 API 需要认证（曾有 401 bug）
10. token 刷新流程
"""

from __future__ import annotations

import os

import httpx
import pytest

from openakita.api.server import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest.fixture
def access_token(app) -> str:
    config = app.state.web_access_config
    return config.create_access_token()


@pytest.fixture
def refresh_token(app) -> str:
    config = app.state.web_access_config
    return config.create_refresh_token()


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. 本地直连请求免认证
# ---------------------------------------------------------------------------

class TestLocalBypassAuth:
    async def test_local_request_no_auth_needed(self, client):
        resp = await client.get("/api/config/workspace-info")
        assert resp.status_code == 200

    async def test_local_request_to_protected_endpoint(self, client):
        resp = await client.get("/api/memories")
        assert resp.status_code != 401


# ---------------------------------------------------------------------------
# 2. 反向代理模式 + X-Forwarded-For → 需要认证
# ---------------------------------------------------------------------------

class TestReverseProxyRequiresAuth:
    async def test_proxy_with_forwarded_for_requires_auth(self, client, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/config/workspace-info",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 401

    async def test_proxy_with_forwarded_for_and_token_succeeds(
        self, client, access_token, monkeypatch
    ):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/config/workspace-info",
            headers={
                "X-Forwarded-For": "203.0.113.50",
                **auth_header(access_token),
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. 反向代理模式 + 无 X-Forwarded-For（Tauri 桌面端直连）→ 仍免认证
# ---------------------------------------------------------------------------

class TestReverseProxyLocalDirect:
    async def test_proxy_mode_local_no_forwarded_for_bypasses_auth(
        self, client, monkeypatch
    ):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get("/api/config/workspace-info")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 4. trust_proxy 动态读取
# ---------------------------------------------------------------------------

class TestTrustProxyDynamic:
    async def test_trust_proxy_off_then_on(self, client, access_token, monkeypatch):
        monkeypatch.delenv("TRUST_PROXY", raising=False)
        resp = await client.get(
            "/api/config/workspace-info",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 200

        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/config/workspace-info",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 401

    async def test_trust_proxy_on_then_off(self, client, access_token, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/config/workspace-info",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 401

        monkeypatch.delenv("TRUST_PROXY", raising=False)
        resp = await client.get(
            "/api/config/workspace-info",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 5. WebSocket 认证与 HTTP 一致
# ---------------------------------------------------------------------------

class TestWebSocketAuth:
    async def test_ws_local_direct_connects(self, app, monkeypatch):
        from openakita.api.routes import websocket as ws_mod

        monkeypatch.setattr(ws_mod, "_is_local_ws", lambda ws: True)
        from starlette.testclient import TestClient

        with TestClient(app) as tc:
            with tc.websocket_connect("/ws/events") as ws:
                data = ws.receive_json()
                assert data["event"] == "connected"

    async def test_ws_proxy_without_token_rejected(self, app, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        from openakita.api.routes import websocket as ws_mod

        monkeypatch.setattr(ws_mod, "_is_local_ws", lambda ws: True)
        from starlette.testclient import TestClient

        with TestClient(app) as tc:
            with pytest.raises(Exception):
                with tc.websocket_connect(
                    "/ws/events",
                    headers={"X-Forwarded-For": "203.0.113.50"},
                ):
                    pass

    async def test_ws_proxy_with_valid_token_connects(self, app, access_token, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        from starlette.testclient import TestClient

        with TestClient(app) as tc:
            with tc.websocket_connect(
                f"/ws/events?token={access_token}",
                headers={"X-Forwarded-For": "203.0.113.50"},
            ) as ws:
                data = ws.receive_json()
                assert data["event"] == "connected"


# ---------------------------------------------------------------------------
# 6. /web/ 路径始终免认证
# ---------------------------------------------------------------------------

class TestWebPathExempt:
    async def test_web_path_no_auth_with_proxy(self, client, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/web/",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code != 401


# ---------------------------------------------------------------------------
# 7. /api/health 免认证
# ---------------------------------------------------------------------------

class TestHealthExempt:
    async def test_health_no_auth(self, client, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/health",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 8. /api/config 需要认证
# ---------------------------------------------------------------------------

class TestConfigRequiresAuth:
    async def test_config_rejected_without_token(self, client, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/config/workspace-info",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 401

    async def test_config_allowed_with_token(self, client, access_token, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/config/workspace-info",
            headers={
                "X-Forwarded-For": "203.0.113.50",
                **auth_header(access_token),
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 9. 记忆管理 API 需要认证
# ---------------------------------------------------------------------------

class TestMemoryRequiresAuth:
    async def test_memories_rejected_without_token(self, client, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/memories",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        assert resp.status_code == 401

    async def test_memories_allowed_with_token(self, client, access_token, monkeypatch):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.get(
            "/api/memories",
            headers={
                "X-Forwarded-For": "203.0.113.50",
                **auth_header(access_token),
            },
        )
        assert resp.status_code != 401


# ---------------------------------------------------------------------------
# 10. token 刷新流程
# ---------------------------------------------------------------------------

class TestTokenRefresh:
    async def test_refresh_returns_new_access_token(self, client, refresh_token):
        resp = await client.post(
            "/api/auth/refresh",
            cookies={"openakita_refresh": refresh_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data

    async def test_refresh_with_invalid_token_fails(self, client):
        resp = await client.post(
            "/api/auth/refresh",
            cookies={"openakita_refresh": "invalid.token.here"},
        )
        assert resp.status_code == 401

    async def test_refreshed_token_can_access_protected_endpoint(
        self, client, refresh_token, monkeypatch
    ):
        monkeypatch.setenv("TRUST_PROXY", "true")
        resp = await client.post(
            "/api/auth/refresh",
            cookies={"openakita_refresh": refresh_token},
        )
        assert resp.status_code == 200
        new_token = resp.json()["access_token"]

        resp = await client.get(
            "/api/config/workspace-info",
            headers={
                "X-Forwarded-For": "203.0.113.50",
                **auth_header(new_token),
            },
        )
        assert resp.status_code == 200

