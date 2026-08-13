import pytest

from openakita.account.config import (
    DEFAULT_ACCOUNT_BASE_URL,
    DEFAULT_ACCOUNT_CLIENT_ID,
    DEFAULT_CREDENTIAL_USERNAME,
    AccountFeatureConfig,
    disabled_credential_usernames,
)


def test_account_feature_defaults_to_official_provider() -> None:
    config = AccountFeatureConfig.from_env({})

    assert config.mode == "openakita"
    assert config.enabled is True
    assert config.base_url == DEFAULT_ACCOUNT_BASE_URL
    assert config.client_id == DEFAULT_ACCOUNT_CLIENT_ID
    assert config.credential_username == DEFAULT_CREDENTIAL_USERNAME
    assert config.capability()["provider"] == "openakita"


def test_disabled_mode_ignores_provider_configuration() -> None:
    config = AccountFeatureConfig.from_env(
        {
            "OPENAKITA_ACCOUNT_MODE": "disabled",
            "OPENAKITA_ACCOUNT_BASE_URL": "not-a-url",
        }
    )

    assert config.capability() == {
        "enabled": False,
        "mode": "disabled",
        "provider": None,
        "display_name": None,
        "supports_entitlements": False,
    }


def test_custom_mode_requires_an_explicit_origin_and_client() -> None:
    with pytest.raises(ValueError, match="BASE_URL is required"):
        AccountFeatureConfig.from_env({"OPENAKITA_ACCOUNT_MODE": "custom"})

    with pytest.raises(ValueError, match="CLIENT_ID is required"):
        AccountFeatureConfig.from_env(
            {
                "OPENAKITA_ACCOUNT_MODE": "custom",
                "OPENAKITA_ACCOUNT_BASE_URL": "https://accounts.vendor.example",
            }
        )


def test_custom_mode_isolated_credentials_and_branding() -> None:
    config = AccountFeatureConfig.from_env(
        {
            "OPENAKITA_ACCOUNT_MODE": "custom",
            "OPENAKITA_ACCOUNT_BASE_URL": "https://accounts.vendor.example/",
            "OPENAKITA_ACCOUNT_CLIENT_ID": "vendor-desktop",
            "OPENAKITA_ACCOUNT_PROVIDER": "vendor-id",
            "OPENAKITA_ACCOUNT_DISPLAY_NAME": "Vendor Account",
        }
    )

    assert config.enabled is True
    assert config.provider == "vendor-id"
    assert config.display_name == "Vendor Account"
    assert config.base_url == "https://accounts.vendor.example"
    assert config.client_id == "vendor-desktop"
    assert config.credential_username is not None
    assert config.credential_username.startswith("custom-")
    assert config.credential_username != DEFAULT_CREDENTIAL_USERNAME


def test_invalid_mode_and_provider_url_fail_closed() -> None:
    with pytest.raises(ValueError, match="OPENAKITA_ACCOUNT_MODE"):
        AccountFeatureConfig.from_env({"OPENAKITA_ACCOUNT_MODE": "off"})

    with pytest.raises(ValueError, match="absolute HTTP"):
        AccountFeatureConfig.from_env(
            {
                "OPENAKITA_ACCOUNT_MODE": "custom",
                "OPENAKITA_ACCOUNT_BASE_URL": "file:///tmp/account",
                "OPENAKITA_ACCOUNT_CLIENT_ID": "vendor-desktop",
            }
        )


def test_disabled_mode_clears_official_and_configured_custom_slots() -> None:
    usernames = disabled_credential_usernames(
        {
            "OPENAKITA_ACCOUNT_BASE_URL": "https://accounts.vendor.example",
            "OPENAKITA_ACCOUNT_CLIENT_ID": "vendor-desktop",
            "OPENAKITA_ACCOUNT_CREDENTIAL_NAMESPACE": "vendor-release",
        }
    )

    assert DEFAULT_CREDENTIAL_USERNAME in usernames
    assert "vendor-release-desktop-refresh-token" in usernames
    assert any(username.startswith("custom-") for username in usernames)
