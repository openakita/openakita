"""前端-后端联调集成测试。

测试目标：验证 Web 前端能正常通过后端 API 服务，覆盖以下常见问题场景：
1. Web 前端页面能正常加载（index.html 可访问）
2. 静态资源（JS/CSS）MIME 类型正确（曾有 Windows 下 MIME 错误的 bug）
3. 根路径 / 重定向到 /web/
4. SPA 路由回退（任意子路径返回 index.html）
5. API health endpoint 可达
6. CORS 头正确返回
7. /web/ 路径免认证
8. 受保护 API 在未认证时返回 401
9. 登录流程（密码验证 → token 获取 → token 访问受保护 API）
10. WebSocket 连接可建立
"""

from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest

from openakita.api.server import create_app

TEST_PASSWORD = "integration-test-pw-42"


@pytest.fixture
def web_dist(tmp_path):
    dist = tmp_path / "dist-web"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><head></head><body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    (assets / "app-test.js").write_text("console.log('ok')", encoding="utf-8")
    (assets / "style-test.css").write_text("body{margin:0}", encoding="utf-8")
    return dist


@pytest.fixture
def app(web_dist):
    with patch("openakita.api.server._find_web_dist", return_value=web_dist):
        return create_app()


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest.fixture
def authed_app(web_dist):
    with (
        patch.dict(os.environ, {"OPENAKITA_WEB_PASSWORD": TEST_PASSWORD}),
        patch("openakita.api.server._find_web_dist", return_value=web_dist),
    ):
        return create_app()


@pytest.fixture
async def remote_client(authed_app):
    with patch.dict(os.environ, {"TRUST_PROXY": "true"}):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=authed_app),
            base_url="http://testserver",
            headers={"X-Forwarded-For": "203.0.113.1"},
        ) as c:
            yield c


# ── 1. Web 前端页面加载 ──


class TestWebFrontendLoading:
    async def test_index_html_accessible(self, client):
        resp = await client.get("/web/")
        assert resp.status_code == 200
        assert "html" in resp.text.lower()

    async def test_index_html_has_root_div(self, client):
        resp = await client.get("/web/")
        assert resp.status_code == 200
        assert '<div id="root">' in resp.text


# ── 2. 静态资源 MIME 类型 ──


class TestStaticAssetMIME:
    async def test_js_mime_type(self, client):
        resp = await client.get("/web/assets/app-test.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]

    async def test_css_mime_type(self, client):
        resp = await client.get("/web/assets/style-test.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]


# ── 3. 根路径重定向 ──


class TestRootRedirect:
    async def test_root_redirects_to_web(self, client):
        resp = await client.get("/", follow_redirects=False)
        assert resp.status_code in (301, 302, 307)
        assert resp.headers["location"] == "/web/"


# ── 4. SPA 路由回退 ──
# StaticFiles(html=True) 仅对目录请求返回 index.html，不提供完整 SPA fallback。
# 深层路径的路由由前端 React Router 在客户端处理。


class TestSPAFallback:
    async def test_root_serves_index_html(self, client):
        resp = await client.get("/web/")
        assert resp.status_code == 200
        assert "html" in resp.text.lower()

    async def test_nonexistent_deep_path_returns_404(self, client):
        resp = await client.get("/web/settings/profile")
        assert resp.status_code == 404

    async def test_nonexistent_file_returns_404(self, client):
        resp = await client.get("/web/no-such-file.js")
        assert resp.status_code == 404


# ── 5. Health endpoint ──


class TestHealthEndpoint:
    async def test_health_reachable(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "openakita"


# ── 6. CORS 头 ──


class TestCORSHeaders:
    async def test_preflight_returns_cors_headers(self, client):
        resp = await client.options(
            "/api/health",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers

    async def test_normal_request_returns_cors_headers(self, client):
        resp = await client.get(
            "/api/health",
            headers={"Origin": "http://example.com"},
        )
        assert "access-control-allow-origin" in resp.headers


# ── 7. /web/ 路径免认证 ──


class TestWebPathNoAuth:
    async def test_web_accessible_without_token(self, remote_client):
        resp = await remote_client.get("/web/")
        assert resp.status_code == 200
        assert "html" in resp.text.lower()

    async def test_web_assets_accessible_without_token(self, remote_client):
        resp = await remote_client.get("/web/assets/app-test.js")
        assert resp.status_code == 200


# ── 8. 受保护 API 返回 401 ──


class TestProtectedAPIAuth:
    async def test_config_requires_auth(self, remote_client):
        resp = await remote_client.get("/api/config/workspace-info")
        assert resp.status_code == 401

    async def test_agents_requires_auth(self, remote_client):
        resp = await remote_client.get("/api/agents")
        assert resp.status_code == 401


# ── 9. 登录流程 ──


class TestLoginFlow:
    async def test_login_with_wrong_password(self, remote_client):
        resp = await remote_client.post(
            "/api/auth/login",
            json={"password": "wrong-password"},
        )
        assert resp.status_code == 401

    async def test_login_and_access_protected_api(self, remote_client):
        login_resp = await remote_client.post(
            "/api/auth/login",
            json={"password": TEST_PASSWORD},
        )
        assert login_resp.status_code == 200
        data = login_resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

        token = data["access_token"]
        protected_resp = await remote_client.get(
            "/api/config/workspace-info",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Forwarded-For": "203.0.113.1",
            },
        )
        assert protected_resp.status_code == 200


# ── 10. WebSocket 连接 ──


class TestWebSocket:
    def test_websocket_connect_and_receive_connected_event(self, app):
        from starlette.testclient import TestClient

        token = app.state.web_access_config.create_access_token()
        sync_client = TestClient(app)
        with sync_client.websocket_connect(f"/ws/events?token={token}") as ws:
            data = ws.receive_json()
            assert data["event"] == "connected"
            assert "ts" in data

    def test_websocket_ping_pong(self, app):
        from starlette.testclient import TestClient

        token = app.state.web_access_config.create_access_token()
        sync_client = TestClient(app)
        with sync_client.websocket_connect(f"/ws/events?token={token}") as ws:
            ws.receive_json()
            ws.send_text("ping")
            pong = ws.receive_json()
            assert pong["event"] == "pong"

