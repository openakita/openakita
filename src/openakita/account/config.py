"""Distribution-level configuration for the optional account integration."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

ACCOUNT_MODES = {"openakita", "custom", "disabled"}
DEFAULT_ACCOUNT_BASE_URL = "https://account.fzstack.com"
DEFAULT_ACCOUNT_CLIENT_ID = "openakita-desktop"
DEFAULT_CREDENTIAL_USERNAME = "openakita-desktop-refresh-token"

_PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_NAMESPACE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def _validated_base_url(value: str, *, variable: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{variable} must be an absolute HTTP(S) origin")
    return normalized


def _derived_custom_namespace(base_url: str, client_id: str) -> str:
    digest = hashlib.sha256(f"{base_url}\0{client_id}".encode()).hexdigest()[:12]
    return f"custom-{digest}"


def _credential_username(namespace: str) -> str:
    if namespace == "openakita":
        return DEFAULT_CREDENTIAL_USERNAME
    return f"{namespace}-desktop-refresh-token"


@dataclass(frozen=True)
class AccountFeatureConfig:
    """Resolved account provider policy for one OpenAkita distribution."""

    mode: str
    enabled: bool
    provider: str | None
    display_name: str | None
    base_url: str | None
    client_id: str | None
    credential_username: str | None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AccountFeatureConfig:
        values = os.environ if environ is None else environ
        mode = values.get("OPENAKITA_ACCOUNT_MODE", "openakita").strip().lower()
        if mode not in ACCOUNT_MODES:
            allowed = ", ".join(sorted(ACCOUNT_MODES))
            raise ValueError(f"OPENAKITA_ACCOUNT_MODE must be one of: {allowed}")
        if mode == "disabled":
            return cls(
                mode=mode,
                enabled=False,
                provider=None,
                display_name=None,
                base_url=None,
                client_id=None,
                credential_username=None,
            )

        if mode == "openakita":
            base_url = _validated_base_url(
                values.get("OPENAKITA_ACCOUNT_BASE_URL", DEFAULT_ACCOUNT_BASE_URL),
                variable="OPENAKITA_ACCOUNT_BASE_URL",
            )
            client_id = values.get("OPENAKITA_ACCOUNT_CLIENT_ID", DEFAULT_ACCOUNT_CLIENT_ID).strip()
            provider = "openakita"
            display_name = values.get("OPENAKITA_ACCOUNT_DISPLAY_NAME", "OpenAkita Account").strip()
            namespace = values.get("OPENAKITA_ACCOUNT_CREDENTIAL_NAMESPACE", "openakita").strip()
        else:
            raw_base_url = values.get("OPENAKITA_ACCOUNT_BASE_URL", "").strip()
            client_id = values.get("OPENAKITA_ACCOUNT_CLIENT_ID", "").strip()
            if not raw_base_url:
                raise ValueError(
                    "OPENAKITA_ACCOUNT_BASE_URL is required when OPENAKITA_ACCOUNT_MODE=custom"
                )
            if not client_id:
                raise ValueError(
                    "OPENAKITA_ACCOUNT_CLIENT_ID is required when OPENAKITA_ACCOUNT_MODE=custom"
                )
            base_url = _validated_base_url(
                raw_base_url,
                variable="OPENAKITA_ACCOUNT_BASE_URL",
            )
            provider = values.get("OPENAKITA_ACCOUNT_PROVIDER", "custom").strip().lower()
            display_name = values.get("OPENAKITA_ACCOUNT_DISPLAY_NAME", "Account").strip()
            namespace = values.get("OPENAKITA_ACCOUNT_CREDENTIAL_NAMESPACE", "").strip()
            if not namespace:
                namespace = _derived_custom_namespace(base_url, client_id)

        if not client_id:
            raise ValueError("OPENAKITA_ACCOUNT_CLIENT_ID must not be empty")
        if not display_name:
            raise ValueError("OPENAKITA_ACCOUNT_DISPLAY_NAME must not be empty")
        if not _PROVIDER_PATTERN.fullmatch(provider):
            raise ValueError("OPENAKITA_ACCOUNT_PROVIDER contains unsupported characters")
        if not _NAMESPACE_PATTERN.fullmatch(namespace):
            raise ValueError(
                "OPENAKITA_ACCOUNT_CREDENTIAL_NAMESPACE contains unsupported characters"
            )

        return cls(
            mode=mode,
            enabled=True,
            provider=provider,
            display_name=display_name,
            base_url=base_url,
            client_id=client_id,
            credential_username=_credential_username(namespace),
        )

    def capability(self) -> dict[str, str | bool | None]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "provider": self.provider,
            "display_name": self.display_name,
            "supports_entitlements": self.enabled,
        }


def disabled_credential_usernames(
    environ: Mapping[str, str] | None = None,
) -> set[str]:
    """Return credential slots that a disabled distribution must clear."""

    values = os.environ if environ is None else environ
    usernames = {DEFAULT_CREDENTIAL_USERNAME}
    namespace = values.get("OPENAKITA_ACCOUNT_CREDENTIAL_NAMESPACE", "").strip()
    if namespace and _NAMESPACE_PATTERN.fullmatch(namespace):
        usernames.add(_credential_username(namespace))
    base_url = values.get("OPENAKITA_ACCOUNT_BASE_URL", "").strip()
    client_id = values.get("OPENAKITA_ACCOUNT_CLIENT_ID", "").strip()
    if base_url and client_id:
        try:
            normalized = _validated_base_url(base_url, variable="OPENAKITA_ACCOUNT_BASE_URL")
        except ValueError:
            pass
        else:
            usernames.add(_credential_username(_derived_custom_namespace(normalized, client_id)))
    return usernames
