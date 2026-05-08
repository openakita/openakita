from openakita.utils.redaction import REDACTION, redact_text, redact_value


def test_redact_value_recursively_masks_sensitive_keys():
    payload = {
        "name": "demo",
        "credentials": {
            "app_secret": "plain-secret",
            "bot_token": "plain-token",
            "nested": [{"access_key": "plain-key"}],
        },
    }

    redacted = redact_value(payload)

    assert redacted["name"] == "demo"
    assert redacted["credentials"]["app_secret"] == REDACTION
    assert redacted["credentials"]["bot_token"] == REDACTION
    assert redacted["credentials"]["nested"][0]["access_key"] == REDACTION


def test_redact_text_masks_key_values_authorization_and_url_query():
    text = (
        "app_secret=abc123 token: xyz Authorization: Bearer sk-test "
        "https://example.com/hook?ticket=t-1&ok=1"
    )

    redacted = redact_text(text)

    assert "abc123" not in redacted
    assert "xyz" not in redacted
    assert "sk-test" not in redacted
    assert "ticket=%5BREDACTED%5D" in redacted
    assert "ok=1" in redacted


def test_redact_text_is_idempotent_for_existing_markers():
    text = "app_secret=[REDACTED] bot_token=[REDACTED]"

    assert redact_text(redact_text(text)) == text


def test_bug_report_sanitized_config_redacts_runtime_im_credentials(tmp_path, monkeypatch):
    from openakita.api.routes import bug_report

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "runtime_state.json").write_text(
        """
{
  "im_bots": [
    {
      "id": "qqbot-demo",
      "type": "qqbot",
      "credentials": {
        "app_id": "public-id",
        "app_secret": "plain-secret"
      }
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(bug_report, "_resolve_data_dir", lambda: data_dir)
    monkeypatch.setattr(bug_report, "_collect_endpoint_summary", lambda: {})
    monkeypatch.setenv("QQBOT_APP_SECRET", "env-secret")

    snapshot = bug_report._collect_sanitized_config()

    dumped = str(snapshot)
    assert "plain-secret" not in dumped
    assert "env-secret" not in dumped
    assert snapshot["QQBOT_APP_SECRET"] == "***"
    credentials = snapshot["_runtime_state"]["im_bots"][0]["credentials"]
    assert credentials["app_id"] == "public-id"
    assert credentials["app_secret"] == REDACTION
