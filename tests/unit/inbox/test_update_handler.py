from __future__ import annotations

from openakita.inbox.update_handler import (
    compare_versions,
    find_update_available,
    update_payload_for_message,
)


def test_compare_versions_handles_v_prefix_and_padding() -> None:
    assert compare_versions("v1.2", "1.2.0") == 0
    assert compare_versions("1.3.0", "1.2.9") == 1
    assert compare_versions("1.2.0", "1.2.1") == -1


def test_update_payload_skips_current_or_older_target() -> None:
    message = {"id": "m1", "type": "update", "raw": {"target_version": "1.2.0"}}
    assert update_payload_for_message(message, current_version="1.2.0") is None


def test_update_payload_detects_forced_now_policy() -> None:
    message = {
        "id": "m1",
        "title": "Update",
        "type": "update",
        "raw": {
            "target_version": "1.4.0",
            "manifest_url": "https://example.com/latest.json",
            "force_upgrade": True,
            "min_supported_version": "1.3.0",
        },
    }

    payload = update_payload_for_message(message, current_version="1.2.0")

    assert payload is not None
    assert payload["version"] == "1.4.0"
    assert payload["manifest_url"] == "https://example.com/latest.json"
    assert payload["policy"] == "forced_now"


def test_find_update_available_returns_first_matching_update() -> None:
    payload = find_update_available(
        [
            {"id": "notice", "type": "notice"},
            {"id": "update", "type": "update", "raw": {"target_version": "2.0.0"}},
        ],
        current_version="1.0.0",
    )

    assert payload is not None
    assert payload["message_id"] == "update"
