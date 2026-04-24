"""Vendored helpers for idea-research.

These modules are intentionally bundled inside the plugin so we never
take a runtime dependency on ``openakita_plugin_sdk.contrib`` (removed
in SDK 0.7.0) nor on any cross-plugin ``_shared`` import.
"""  # noqa: N999  -- parent package directory uses kebab-case

from .mdrm_adapter import (
    HookRecord,
    MdrmAdapter,
    MdrmCapabilities,
)
from .vendor_client import (
    VendorAuthError,
    VendorClient,
    VendorError,
    VendorQuotaError,
    VendorRateLimitError,
    VendorTimeoutError,
)

__all__ = [
    "HookRecord",
    "MdrmAdapter",
    "MdrmCapabilities",
    "VendorAuthError",
    "VendorClient",
    "VendorError",
    "VendorQuotaError",
    "VendorRateLimitError",
    "VendorTimeoutError",
]
