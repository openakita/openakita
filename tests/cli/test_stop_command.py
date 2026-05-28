"""Sprint 14 / v31 Phase A + Sprint 15 / v32 Phase B regression:
``openakita stop`` CLI subcommand must POST ``/api/shutdown`` reliably
so operators don't have to hand-craft PowerShell / curl every
regression.

Forensic background — see ``_v32_biz/_phase_b_cli_trust_env.md``
and ``_v31_biz_e2e/v31_regression_report.md`` §6 CLI-1:

* v29 graceful-restart audit attempted ``python -m openakita.api.cli stop``
  and got ``No module named openakita.api.cli`` (Phase B = fail). The CLI
  surface had no programmatic shutdown entrypoint at all.
* v31 Sprint 14 added the ``stop`` subcommand but used the
  module-level ``httpx.post(...)`` shortcut whose Client defaults to
  ``trust_env=True``. With v2ray / clash on the operator's PC
  (``HTTPS_PROXY=http://127.0.0.1:10808``), the proxy intercepts
  127.0.0.1:18900 too and returns 503 for the dead port. The CLI
  printed ``unexpected status 503`` and exited 1 instead of the correct
  ``no backend listening`` + exit 2. Workaround at the time was to set
  ``NO_PROXY=127.0.0.1``.
* v32 (this commit) forces ``trust_env=False`` on an explicit
  ``httpx.Client`` so proxy env vars cannot rewrite the route.

Tests pin five guarantees:
1. Default invocation POSTs to ``http://127.0.0.1:18900/api/shutdown``.
2. ``ConnectError`` (no backend listening) → exit code 2 + clear message.
3. Non-200 HTTP responses → exit code 1 (so wrapper scripts can branch).
4. ``--host`` / ``--port`` / ``--timeout`` reach the underlying request.
5. The CLI's httpx Client has ``trust_env=False`` AND dead-backend
   detection still works even with ``HTTPS_PROXY`` set in the
   environment (the v32 regression guard).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from openakita.main import app


@pytest.fixture
def runner() -> CliRunner:
    # Newer click drops the `mix_stderr` kwarg and merges streams by
    # default. We assert against ``result.output`` (combined) so the
    # tests are version-tolerant.
    return CliRunner()


def _make_response(status_code: int, json_body: dict | None = None) -> MagicMock:
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.text = "" if json_body is None else str(json_body)
    if json_body is None:
        mock_resp.json.side_effect = ValueError("no body")
    else:
        mock_resp.json.return_value = json_body
    return mock_resp


class _FakeClient:
    """Minimal httpx.Client stand-in usable as a context manager.

    Tracks ``trust_env`` / ``timeout`` kwargs the CLI passed and the URL
    + kwargs of every ``post()`` call so tests can assert against them.
    The post behaviour is configurable: either return ``response`` or
    raise ``raise_exc``.
    """

    def __init__(self, *, trust_env: bool = True, timeout: float | None = None):
        self.trust_env = trust_env
        self.timeout = timeout
        self.posted_url: str | None = None
        self.posted_kwargs: dict | None = None
        # Filled in by the test before invocation:
        self.response: httpx.Response | None = None
        self.raise_exc: BaseException | None = None

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def post(self, url: str, **kwargs):
        self.posted_url = url
        self.posted_kwargs = kwargs
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.response


def _patched_client(**configured_attrs) -> tuple[_FakeClient, object]:
    """Return (fake_client, patcher_ctx) wired so ``httpx.Client(...)``
    yields the same instance the test pre-configured.
    """
    fake = _FakeClient()
    for k, v in configured_attrs.items():
        setattr(fake, k, v)

    def _factory(*, trust_env=True, timeout=None, **_ignored):
        # Honour the kwargs the CLI passed so the trust_env assertion
        # below catches a regression where the CLI accidentally drops
        # the flag.
        fake.trust_env = trust_env
        fake.timeout = timeout
        return fake

    return fake, patch("httpx.Client", side_effect=_factory)


def test_stop_command_posts_shutdown(runner: CliRunner) -> None:
    """Default invocation should POST to the well-known shutdown URL.

    We don't care about the underlying HTTP transport here — only that
    the CLI argument plumbing matches the documented operator behavior.
    """
    fake_resp = _make_response(200, {"status": "shutting_down"})
    fake, ctx = _patched_client(response=fake_resp)
    with ctx:
        result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0, result.output
    assert fake.posted_url == "http://127.0.0.1:18900/api/shutdown"
    # Default timeout should be reasonable (the route returns ~30ms in
    # production, 5s is plenty of slack for slow startup races). v32
    # moved the timeout off the call onto the Client constructor.
    assert fake.timeout == 5.0
    assert "shutdown signal accepted" in result.output


def test_stop_command_handles_no_backend(runner: CliRunner) -> None:
    """``ConnectError`` (port not listening) must exit 2 with operator-facing message."""
    fake, ctx = _patched_client(
        raise_exc=httpx.ConnectError("Connection refused", request=MagicMock())
    )
    with ctx:
        result = runner.invoke(app, ["stop"])
    assert result.exit_code == 2, result.output
    # Stderr/stdout are merged in newer click runners; we keep the
    # message short + actionable so it parses cleanly in monitoring scripts.
    assert "no backend listening" in result.output


def test_stop_command_reports_unexpected_status(runner: CliRunner) -> None:
    """Non-200 (auth failure, server error) should exit 1 + show the body."""
    fake_resp = _make_response(401, None)
    fake_resp.text = '{"detail": "Authentication required"}'
    fake, ctx = _patched_client(response=fake_resp)
    with ctx:
        result = runner.invoke(app, ["stop"])
    assert result.exit_code == 1, result.output
    assert "401" in result.output
    assert "Authentication required" in result.output


def test_stop_command_honors_custom_host_port(runner: CliRunner) -> None:
    """``--host`` / ``--port`` / ``--timeout`` flags must reach the request."""
    fake_resp = _make_response(200, {"status": "shutting_down"})
    fake, ctx = _patched_client(response=fake_resp)
    with ctx:
        result = runner.invoke(
            app,
            ["stop", "--host", "10.0.0.5", "--port", "28900", "--timeout", "2.5"],
        )
    assert result.exit_code == 0, result.output
    assert fake.posted_url == "http://10.0.0.5:28900/api/shutdown"
    assert fake.timeout == 2.5


def test_stop_command_disables_env_proxy(runner: CliRunner) -> None:
    """v32 regression: the CLI's httpx.Client MUST have ``trust_env=False``.

    With ``trust_env=True`` (the httpx default), an environment variable
    like ``HTTPS_PROXY=http://127.0.0.1:10808`` (v2ray / clash on the
    operator's PC) rewrites the 127.0.0.1:18900 request through the
    proxy, which returns 503 for the dead backend port — the CLI then
    misreports ``unexpected status 503`` (exit 1) instead of the correct
    ``no backend listening`` (exit 2).

    This test sets a representative proxy env var and asserts:
    1. The CLI still creates the client with ``trust_env=False`` so the
       env var is ignored.
    2. With the proxy ignored, a ``ConnectError`` (the real outcome on
       a dead port) yields the correct exit 2 + ``no backend`` message.
    """
    proxy_envs = {
        "HTTP_PROXY": "http://127.0.0.1:10808",
        "HTTPS_PROXY": "http://127.0.0.1:10808",
        "ALL_PROXY": "socks5://127.0.0.1:10808",
    }
    fake, ctx = _patched_client(
        raise_exc=httpx.ConnectError("Connection refused", request=MagicMock())
    )
    with patch.dict("os.environ", proxy_envs, clear=False), ctx:
        result = runner.invoke(app, ["stop"])

    assert fake.trust_env is False, (
        "CLI must construct httpx.Client(trust_env=False) so proxy env "
        "vars cannot rewrite 127.0.0.1:18900 through v2ray/clash. "
        "This is the v32 Phase B regression fix."
    )
    assert result.exit_code == 2, result.output
    assert "no backend listening" in result.output
