import json

from openakita.api.routes import bug_report
from openakita.utils.redaction import REDACTION


def test_sanitized_config_redacts_runtime_state_bot_credentials(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    runtime_state = {
        "im_bots": [
            {
                "id": "feishu-bot",
                "type": "feishu",
                "credentials": {
                    "app_id": "cli_public",
                    "app_secret": "should-not-leak",
                    "streaming_enabled": "true",
                },
            }
        ]
    }
    (data_dir / "runtime_state.json").write_text(
        json.dumps(runtime_state),
        encoding="utf-8",
    )

    monkeypatch.setattr(bug_report, "_resolve_data_dir", lambda: data_dir)
    monkeypatch.setattr(bug_report, "_collect_endpoint_summary", lambda: {})

    sanitized = bug_report._collect_sanitized_config()

    credentials = sanitized["_runtime_state"]["im_bots"][0]["credentials"]
    assert credentials["app_id"] == "cli_public"
    assert credentials["app_secret"] == REDACTION
    assert credentials["streaming_enabled"] == "true"
