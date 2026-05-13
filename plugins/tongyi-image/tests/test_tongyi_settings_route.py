"""Settings helpers for Tongyi Image plugin."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from plugin import _normalize_base_url


def test_normalize_base_url_trims_and_strips_trailing_slash() -> None:
    assert (
        _normalize_base_url("  https://relay.example.com/api/v1/  ")
        == "https://relay.example.com/api/v1"
    )


def test_normalize_base_url_allows_empty_value() -> None:
    assert _normalize_base_url("   ") == ""


def test_normalize_base_url_rejects_invalid_protocol() -> None:
    with pytest.raises(HTTPException) as exc:
        _normalize_base_url("relay.example.com/api/v1")

    assert exc.value.status_code == 400
    assert "http:// 或 https://" in exc.value.detail
