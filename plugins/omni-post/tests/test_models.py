"""Unit tests for omni_post_models -- no network / no playwright."""

from __future__ import annotations

import pytest
from omni_post_models import (
    ALL_ERROR_KINDS,
    DEFAULT_SETTINGS,
    ERROR_HINTS,
    PLATFORMS,
    PLATFORMS_BY_ID,
    AccountCreateRequest,
    ErrorKind,
    OmniPostError,
    PublishPayload,
    PublishRequest,
    ScheduleRequest,
    SettingsUpdateRequest,
    build_catalog,
)
from pydantic import ValidationError


def test_error_kinds_cover_all_hints() -> None:
    """Every ErrorKind must have a matching entry in ERROR_HINTS."""

    for kind in ErrorKind:
        assert kind.value in ERROR_HINTS, f"missing hint for {kind.value!r}"


def test_error_hints_bilingual() -> None:
    """Each hint must supply both zh and en variants."""

    for key, hint in ERROR_HINTS.items():
        for field in ("title_zh", "title_en"):
            assert hint.get(field), f"{key}: missing {field}"
        for field in ("hints_zh", "hints_en"):
            assert isinstance(hint.get(field), list) and hint[field], (
                f"{key}: {field} must be a non-empty list"
            )


def test_all_error_kinds_total_13() -> None:
    assert len(ALL_ERROR_KINDS) == 13


def test_omni_post_error_defaults_hint() -> None:
    err = OmniPostError(ErrorKind.COOKIE_EXPIRED, "test")
    assert err.hint["title_zh"] == ERROR_HINTS["cookie_expired"]["title_zh"]


def test_platforms_unique_ids() -> None:
    ids = [p.id for p in PLATFORMS]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(PLATFORMS_BY_ID.keys())


def test_build_catalog_keys() -> None:
    cat = build_catalog()
    assert {"platforms", "post_kinds", "engines", "asset_kinds", "error_kinds"} <= set(cat.keys())
    assert len(cat["platforms"]) == len(PLATFORMS)


def test_publish_request_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        PublishRequest.model_validate(
            {
                "asset_id": "ast_1",
                "payload": {"title": "t"},
                "platforms": ["douyin"],
                "account_ids": ["acc_1"],
                "client_trace_id": "trace-1",
                "bogus": True,
            }
        )


def test_publish_request_minimal() -> None:
    body = PublishRequest.model_validate(
        {
            "asset_id": "ast_1",
            "payload": {"title": "t"},
            "platforms": ["douyin"],
            "account_ids": ["acc_1"],
            "client_trace_id": "trace-1",
        }
    )
    assert body.engine == "auto"
    assert body.auto_submit is True


def test_publish_request_allows_mp_without_asset_or_accounts() -> None:
    body = PublishRequest.model_validate(
        {
            "payload": {"title": "t", "description": "body"},
            "platforms": ["wechat_mp"],
            "account_ids": [],
            "client_trace_id": "trace-mp",
            "engine": "mp",
        }
    )
    assert body.asset_id is None
    assert body.account_ids == []


def test_schedule_request_requires_scheduled_at() -> None:
    with pytest.raises(ValidationError):
        ScheduleRequest.model_validate(
            {
                "asset_id": "ast_1",
                "payload": {"title": "t"},
                "platforms": ["douyin"],
                "account_ids": ["acc_1"],
                "client_trace_id": "trace-1",
            }
        )


def test_account_create_limits() -> None:
    body = AccountCreateRequest.model_validate(
        {
            "platform": "douyin",
            "nickname": "alice",
            "cookie_raw": "a=b",
        }
    )
    assert body.daily_limit == 5
    assert body.weekly_limit == 30
    assert body.monthly_limit == 100


def test_settings_update_partial() -> None:
    body = SettingsUpdateRequest.model_validate({"concurrency_per_platform": 4})
    dumped = body.model_dump(exclude_none=True)
    assert dumped == {"concurrency_per_platform": 4}


def test_default_settings_complete() -> None:
    required_keys = {
        "engine",
        "concurrency_per_platform",
        "cooldown_seconds_per_account",
        "retry_max_attempts",
        "health_threshold",
    }
    assert required_keys <= set(DEFAULT_SETTINGS.keys())


def test_publish_payload_tag_cap() -> None:
    with pytest.raises(ValidationError):
        PublishPayload.model_validate({"title": "t", "tags": [str(i) for i in range(25)]})
