"""Tests for image-edit providers (offline — uses stub provider)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from providers import (  # noqa: E402
    DashScopeWanxProvider,
    OpenAIGptImageProvider,
    StubLocalProvider,
    select_provider,
)
from openakita_plugin_sdk.contrib import VendorError  # noqa: E402


def test_select_provider_auto_falls_back_to_stub(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    p = select_provider("auto")
    assert isinstance(p, StubLocalProvider)


def test_select_provider_explicit_openai_without_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    with pytest.raises(VendorError):
        select_provider("openai")


def test_select_provider_openai_with_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    p = select_provider("openai")
    assert isinstance(p, OpenAIGptImageProvider)


def test_stub_provider_copies_image(tmp_path) -> None:
    src = tmp_path / "in.png"
    src.write_bytes(b"fake png bytes")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    p = StubLocalProvider()
    result = asyncio.run(p.edit(image_path=src, mask_path=None,
                                prompt="x", n=2, output_dir=out_dir))
    assert len(result.output_paths) == 2
    for out in result.output_paths:
        assert out.exists()
        assert out.read_bytes() == b"fake png bytes"
    assert result.provider == "stub-local"


def test_dashscope_provider_raises_on_local_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    p = select_provider("dashscope")
    src = tmp_path / "in.png"; src.write_bytes(b"x")
    with pytest.raises(VendorError) as ei:
        asyncio.run(p.edit(image_path=src, mask_path=None, prompt="x",
                           output_dir=tmp_path))
    assert "publicly accessible" in str(ei.value)
