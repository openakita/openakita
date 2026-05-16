"""Aliyun OSS uploader for happyhorse-video.

Why this exists
---------------

DashScope's video-generation models (wan2.2-s2v / wan2.2-animate-mix /
videoretalk / wan2.2-s2v-detect) all consume **publicly fetchable** URLs
for the input image / video / audio.  The plugin's local
``/api/plugins/happyhorse-video/uploads/...`` route is a relative path with
no host and is unreachable from DashScope's servers.

Aliyun OSS is the path Pixelle / official samples use:

1. User uploads file via the plugin's ``POST /upload`` route.
2. Backend pushes the file to OSS under a bucket the user owns.
3. We hand DashScope a **signed URL** (HTTPS, time-limited).  No bucket
   ACL changes required — the signature carries its own auth.

Settings shape
--------------

```jsonc
{
  "oss_endpoint": "https://oss-cn-beijing.aliyuncs.com",  // or just the host
  "oss_bucket": "my-bucket",
  "oss_access_key_id": "LTAI…",
  "oss_access_key_secret": "…",
  "oss_path_prefix": "happyhorse-video"  // optional, default "happyhorse-video"
}
```

Errors raised
-------------

- ``OssNotConfigured``: any required field is missing.  Caller should
  surface this as a 400 with a setup hint, not a 500.
- ``OssUploadError``: oss2 raised; wraps the underlying exception so the
  caller doesn't need to ``import oss2`` to catch it.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Aliyun OSS bucket naming rules (verified against console error text):
# 3–63 chars, lowercase letters / digits / hyphens, must start+end with
# letter or digit. We reject upper-case / underscores / dots / spaces /
# the user accidentally pasting the full endpoint URL.
_BUCKET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")

logger = logging.getLogger(__name__)

# Sign URLs for 6 hours by default — long enough for any s2v/animate-mix
# poll loop (DashScope upper bound is ~10 min) yet short enough that a
# leaked URL stops working before end of day.
DEFAULT_SIGN_TTL_SEC = 6 * 3600

# Aliyun's OSS hostnames.  We accept either form in settings:
#   "oss-cn-beijing.aliyuncs.com"          (host only)
#   "https://oss-cn-beijing.aliyuncs.com"  (with scheme)
# and normalise internally.
_DEFAULT_PREFIX = "happyhorse-video"


class OssNotConfigured(Exception):
    """Raised when any required OSS credential / bucket field is empty."""


class OssUploadError(Exception):
    """Wraps any oss2 / network error so callers don't import oss2."""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.cause = cause


@dataclass(frozen=True)
class OssConfig:
    """Frozen view of the OSS-related settings.  Built by ``from_settings``."""

    endpoint: str
    bucket: str
    access_key_id: str
    access_key_secret: str
    path_prefix: str = _DEFAULT_PREFIX

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> OssConfig:
        """Read OSS fields from a settings dict.  Raises ``OssNotConfigured``.

        We treat empty strings as missing — Pydantic emits ``""`` for
        missing string fields and we don't want a half-configured OSS
        section to silently produce 500s downstream.
        """
        missing: list[str] = []
        endpoint = str(settings.get("oss_endpoint") or "").strip()
        bucket = str(settings.get("oss_bucket") or "").strip()
        ak = str(settings.get("oss_access_key_id") or "").strip()
        sk = str(settings.get("oss_access_key_secret") or "").strip()
        prefix = str(settings.get("oss_path_prefix") or _DEFAULT_PREFIX).strip().strip("/")
        if not endpoint:
            missing.append("oss_endpoint")
        if not bucket:
            missing.append("oss_bucket")
        if not ak:
            missing.append("oss_access_key_id")
        if not sk:
            missing.append("oss_access_key_secret")
        if missing:
            raise OssNotConfigured(
                "Aliyun OSS not configured (missing: " + ", ".join(missing) + "). "
                "Open Settings → OSS to fill them in."
            )
        # Cheap format check — catches the most common UX mistakes
        # (bucket field gets the full endpoint URL pasted in, or a
        # capital-letter project name) at config-build time so the
        # error surfaces in Settings rather than 5 seconds into a
        # task. We only validate ``bucket`` for now: endpoint mistakes
        # produce equally clear oss2 errors and we don't want a regex
        # to lag behind Aliyun's ever-growing list of regions.
        if not _BUCKET_NAME_RE.match(bucket):
            raise OssNotConfigured(
                f"OSS bucket 名 {bucket!r} 不合法。"
                "应当只含小写字母 / 数字 / 连字符 (-)，长度 3-63，"
                "首尾必须是字母或数字。请不要把 endpoint URL 粘到 bucket 字段里。"
            )
        # Normalise endpoint: oss2 wants the *full* endpoint URL incl scheme.
        if not endpoint.startswith(("http://", "https://")):
            endpoint = "https://" + endpoint
        return cls(
            endpoint=endpoint.rstrip("/"),
            bucket=bucket,
            access_key_id=ak,
            access_key_secret=sk,
            path_prefix=prefix or _DEFAULT_PREFIX,
        )


class OssUploader:
    """Thin wrapper over ``oss2.Bucket`` with sign-URL helpers.

    Reads credentials lazily through a ``read_settings`` callable so the
    user can rotate keys without restarting the plugin (Pixelle A10).
    A new ``oss2.Bucket`` is built per upload because oss2 internally
    caches per-bucket connection pools — that's fine for our low-QPS
    workload and avoids a stale-credential window after a Settings save.
    """

    def __init__(
        self,
        read_settings: Any,
        *,
        sign_ttl_sec: int = DEFAULT_SIGN_TTL_SEC,
        plugin_dir: Path | None = None,
    ) -> None:
        self._read_settings = read_settings
        self._sign_ttl_sec = int(sign_ttl_sec)
        # Used by ``dep_bootstrap.ensure_importable`` to first look in
        # ``<plugin_dir>/deps/`` (host-managed, populated by
        # ``install_pip_deps`` from ``requires.pip``) before falling back
        # to ``~/.openakita/modules/happyhorse-video/site-packages/``.
        self._plugin_dir = plugin_dir

    # ── lifecycle ─────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """Cheap probe — does the current Settings dict have all OSS fields?

        Used by the plugin to gate UI hints (yellow banner) and to skip
        upload when the user is supplying a CDN URL directly.
        """
        try:
            OssConfig.from_settings(self._read_settings() or {})
            return True
        except OssNotConfigured:
            return False

    def _config(self) -> OssConfig:
        return OssConfig.from_settings(self._read_settings() or {})

    def _bucket(self, cfg: OssConfig) -> Any:
        # Lazy-import + auto-bootstrap: oss2 pulls in cryptography + a
        # slow native crc module, so we keep it out of the import-time
        # path of plugin.py. If it's missing we ask the bootstrapper to
        # find it under the host's optional-modules dir / install it
        # privately — neither the host's ``inject_module_paths`` nor the
        # docs route are reliable enough to count on (see
        # ``dep_bootstrap.py`` module docstring).
        try:
            from .dep_bootstrap import DepInstallFailed, ensure_importable

            oss2 = ensure_importable(
                "oss2",
                "oss2>=2.18.0",
                plugin_dir=self._plugin_dir,
                friendly_name="阿里云 OSS SDK (oss2)",
            )
        except DepInstallFailed as e:
            raise OssUploadError(str(e), cause=e) from e
        except Exception as e:  # noqa: BLE001 — defensive: bootstrap should never crash uploads
            raise OssUploadError(
                f"oss2 自举失败：{type(e).__name__}: {e}", cause=e
            ) from e
        # ``oss2.Bucket(...)`` validates the bucket name eagerly and
        # raises ``ClientError`` for things like wrong length, illegal
        # characters, or the user pasting the full endpoint URL into
        # the bucket field by mistake. Wrapping the constructor here
        # means the upload routes only need to catch ``OssUploadError``
        # — they never see a raw oss2 exception leak through and turn
        # into a 500 (which the UI then renders as the opaque
        # 「上传失败：SyntaxError: Unexpected token 'I'」 you've seen).
        try:
            auth = oss2.Auth(cfg.access_key_id, cfg.access_key_secret)
            return oss2.Bucket(auth, cfg.endpoint, cfg.bucket)
        except Exception as e:  # noqa: BLE001 - re-raise as our type
            raise OssUploadError(
                f"OSS bucket 配置无效（{cfg.bucket!r} @ {cfg.endpoint}）："
                f"{e}。请检查「设置 → 阿里云 OSS」中 bucket 名是否只含"
                "小写字母 / 数字 / 连字符，长度 3-63，且 endpoint 不要"
                "和 bucket 名混填。",
                cause=e,
            ) from e

    # ── core ──────────────────────────────────────────────────────────

    def build_object_key(self, *, scope: str, filename: str) -> str:
        """Produce a deterministic OSS object key.

        ``scope`` examples: ``uploads/images``, ``tasks/<task_id>``,
        ``voices/<voice_id>``.  We slash-join them under the configured
        ``path_prefix`` so several plugins / environments can share one
        bucket without colliding.
        """
        cfg = self._config()
        scope = str(scope).strip("/")
        filename = str(filename).lstrip("/")
        if scope:
            return f"{cfg.path_prefix}/{scope}/{filename}"
        return f"{cfg.path_prefix}/{filename}"

    def upload_file(
        self,
        local_path: Path | str,
        *,
        key: str,
        content_type: str | None = None,
    ) -> str:
        """Push a local file to OSS and return a signed HTTPS URL.

        The signed URL has a fixed TTL (``sign_ttl_sec``) and is the
        ONLY URL we hand to DashScope — bucket can stay private.
        """
        local = Path(local_path)
        if not local.is_file():
            raise OssUploadError(f"local file does not exist: {local}")
        ct = content_type or _guess_content_type(local.name)
        cfg = self._config()
        bucket = self._bucket(cfg)
        try:
            bucket.put_object_from_file(
                key,
                str(local),
                headers={"Content-Type": ct} if ct else None,
            )
        except Exception as e:  # noqa: BLE001 - re-raise as our type
            raise OssUploadError(
                f"OSS put_object_from_file failed: {e}", cause=e
            ) from e
        return self._signed_url(bucket, key)

    def upload_bytes(
        self,
        data: bytes,
        *,
        key: str,
        content_type: str | None = None,
    ) -> str:
        """Push an in-memory blob (e.g. TTS audio) and return a signed URL."""
        ct = content_type or _guess_content_type(key)
        cfg = self._config()
        bucket = self._bucket(cfg)
        try:
            bucket.put_object(
                key,
                data,
                headers={"Content-Type": ct} if ct else None,
            )
        except Exception as e:  # noqa: BLE001
            raise OssUploadError(
                f"OSS put_object failed: {e}", cause=e
            ) from e
        return self._signed_url(bucket, key)

    def delete(self, key: str) -> bool:
        """Best-effort delete — never raises so callers can ignore the result."""
        try:
            cfg = self._config()
            bucket = self._bucket(cfg)
            bucket.delete_object(key)
            return True
        except Exception as e:  # noqa: BLE001
            logger.info("OSS delete %s failed (non-fatal): %s", key, e)
            return False

    # ── helpers ───────────────────────────────────────────────────────

    def _signed_url(self, bucket: Any, key: str) -> str:
        try:
            # ``slash_safe=True`` keeps our `/`-separated keys readable
            # (default behaviour percent-encodes them, which DashScope
            # tolerates but makes log lines harder to grep).
            return bucket.sign_url(
                "GET", key, self._sign_ttl_sec, slash_safe=True
            )
        except TypeError:
            # Older oss2 versions (<2.13) don't support slash_safe; retry
            # without it so we don't crash on legacy environments.
            return bucket.sign_url("GET", key, self._sign_ttl_sec)


def _guess_content_type(name: str) -> str:
    """Best-effort MIME for OSS Content-Type header.

    Falls back to ``application/octet-stream`` so DashScope at least
    knows it's a binary blob (some endpoints care, especially the
    audio_url path on s2v).
    """
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"

