"""Tests for the `settings.orgs_v2_backend` field + factory dispatch (P3.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openakita.config import Settings, settings
from openakita.runtime.orgs import (
    JsonOrgStore,
    SqliteOrgStore,
    get_default_store,
    reset_default_store,
)


def test_default_backend_is_json() -> None:
    s = Settings()
    assert s.orgs_v2_backend == "json"


def test_env_backend_sqlite_opts_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORGS_V2_BACKEND", "sqlite")
    s = Settings()
    assert s.orgs_v2_backend == "sqlite"


def test_unknown_backend_value_is_rejected_by_pydantic() -> None:
    """`Literal["json", "sqlite"]` rejects anything else at construction."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(orgs_v2_backend="postgres")  # type: ignore[arg-type]


def test_get_default_store_returns_json_store_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "orgs_v2_backend", "json", raising=False)
    store = reset_default_store(path=tmp_path / "orgs.json")
    assert isinstance(store, JsonOrgStore)
    # The lazily-constructed singleton picks the same backend.
    assert isinstance(get_default_store(), JsonOrgStore)


def test_reset_default_store_with_sqlite_backend(tmp_path: Path) -> None:
    """Explicit `backend="sqlite"` overrides settings for that store."""
    store = reset_default_store(path=tmp_path / "orgs.sqlite", backend="sqlite")
    try:
        assert isinstance(store, SqliteOrgStore)
        assert get_default_store() is store
    finally:
        store.close()
    # Restore JSON default for following tests.
    reset_default_store(backend="json")


def test_reset_default_store_via_settings_dispatches_to_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "orgs_v2_backend", "sqlite", raising=False)
    store = reset_default_store(path=tmp_path / "orgs.sqlite")
    try:
        assert isinstance(store, SqliteOrgStore)
    finally:
        store.close()
    # Restore JSON default.
    reset_default_store(backend="json")
