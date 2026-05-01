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
