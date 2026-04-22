from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openakita.agents.cli_providers import PROVIDERS, ProviderAdapter
from openakita.agents.cli_providers._common import stream_cli_subprocess


def test_providers_registry_is_dict():
    assert isinstance(PROVIDERS, dict)


def test_provider_adapter_protocol_is_runtime_checkable():
    class _MinAdapter:
        def build_argv(self, request): return []
        def build_env(self, request): return {}
        async def run(self, request, argv, env, *, on_spawn): return None
        async def cleanup(self): pass

    assert isinstance(_MinAdapter(), ProviderAdapter)


def test_protocol_rejects_missing_methods():
    class _BadAdapter:
        def build_argv(self, request): return []

    assert not isinstance(_BadAdapter(), ProviderAdapter)


@pytest.mark.asyncio
async def test_stream_cli_subprocess_yields_lines_from_echo(tmp_path):
    cancelled = asyncio.Event()
    tracked = {"proc": None}

    def track(proc):
        tracked["proc"] = proc

    lines = []
    async for line in stream_cli_subprocess(
        ["sh", "-c", "printf 'one\\ntwo\\nthree\\n'"],
        env={},
        cwd=tmp_path,
        cancelled=cancelled,
        on_spawn=track,
    ):
        lines.append(line.rstrip(b"\n"))

    assert lines == [b"one", b"two", b"three"]
    assert tracked["proc"] is not None


@pytest.mark.asyncio
async def test_stream_cli_subprocess_honors_cancellation(tmp_path):
    cancelled = asyncio.Event()
    lines = []

    async def consume():
        async for line in stream_cli_subprocess(
            ["sh", "-c", "for i in $(seq 1 100); do echo $i; sleep 0.05; done"],
            env={}, cwd=tmp_path, cancelled=cancelled, on_spawn=lambda _: None,
        ):
            lines.append(line)
            if len(lines) == 2:
                cancelled.set()

    await consume()
    assert len(lines) < 50


def test_autoload_registers_well_formed_providers(tmp_path, monkeypatch):
    import sys

    from openakita.agents import cli_providers
    from openakita.agents.cli_detector import CliProviderId

    pkg_path = Path(cli_providers.__file__).parent
    target = pkg_path / "testfakeprovider.py"
    target.write_text(
        "from openakita.agents.cli_detector import CliProviderId\n"
        "class _FakeAdapter:\n"
        "    def build_argv(self, request): return []\n"
        "    def build_env(self, request): return {}\n"
        "    async def run(self, request, argv, env, *, on_spawn): return None\n"
        "    async def cleanup(self): pass\n"
        "PROVIDER = _FakeAdapter()\n"
        "CLI_PROVIDER_ID = CliProviderId.GOOSE\n"
    )
    try:
        sys.modules.pop("openakita.agents.cli_providers.testfakeprovider", None)
        cli_providers._autoload()
        assert CliProviderId.GOOSE in cli_providers.PROVIDERS
    finally:
        target.unlink(missing_ok=True)
        cli_providers.PROVIDERS.pop(CliProviderId.GOOSE, None)
        sys.modules.pop("openakita.agents.cli_providers.testfakeprovider", None)
