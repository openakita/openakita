"""DashScope temporary OSS upload for models that require public file_urls (e.g. Paraformer).

See: https://www.alibabacloud.com/help/en/model-studio/get-temporary-file-url
Upload credential is bound to the target model — use the same model name as in ASR calls.
"""

from __future__ import annotations

import ipaddress
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Must match ``model`` in clip_asr_client.ClipAsrClient.transcribe (paraformer-v2).
PARAFORMER_UPLOAD_MODEL = "paraformer-v2"


class DashScopeUploadError(Exception):
    """Temporary file upload to DashScope OSS failed."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.kind = "network" if retryable else "config"


def paraformer_file_url_is_public(url: str) -> bool:
    """Return True if ``url`` can be fetched by DashScope servers without OSS resolve header.

    Relative paths, localhost, loopback, and RFC1918 / link-local hosts are not public.
    ``oss://`` URLs are produced by DashScope upload and are valid for Paraformer when the
    resolve header is set (handled in ``clip_asr_client``) — treat as already upload-backed.
    """
    u = (url or "").strip()
    if not u:
        return False
    if u.startswith("oss://"):
        return True
    if u.startswith("/") or "://" not in u:
        return False
    try:
        parsed = urlparse(u)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    except ValueError:
        pass
    return True


def _policy_field(data: dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = data.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


async def upload_local_file_for_paraformer(
    client: httpx.AsyncClient,
    api_key: str,
    *,
    base_url: str,
    local_path: Path,
    model_name: str = PARAFORMER_UPLOAD_MODEL,
) -> str:
    """Upload a local file to DashScope temporary OSS; return ``oss://...`` URL for file_urls.

    Args:
        client: Shared httpx async client (same timeout policy as ASR).
        api_key: DashScope API key.
        base_url: DashScope API origin (default https://dashscope.aliyuncs.com).
        local_path: Readable file on disk (video/audio).
        model_name: Must match the Paraformer model id used in transcription.
    """
    if not api_key:
        raise DashScopeUploadError("DashScope API key not configured")
    path = Path(local_path)
    if not path.is_file():
        raise DashScopeUploadError(f"Local file not found: {path}")

    origin = base_url.rstrip("/")
    policy_url = f"{origin}/api/v1/uploads"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        pr = await client.get(
            policy_url,
            headers=headers,
            params={"action": "getPolicy", "model": model_name},
            timeout=httpx.Timeout(180.0, connect=60.0),
        )
    except httpx.HTTPError as exc:
        raise DashScopeUploadError(
            f"getPolicy network error: {exc}", retryable=True,
        ) from exc

    if pr.status_code >= 400:
        retryable = pr.status_code in (429, 500, 502, 503, 504)
        raise DashScopeUploadError(
            f"getPolicy HTTP {pr.status_code}: {pr.text[:400]}",
            retryable=retryable,
        )

    body = pr.json()
    data = body.get("data") or body
    if not isinstance(data, dict):
        raise DashScopeUploadError("getPolicy returned invalid JSON (no data object)")

    upload_host = _policy_field(data, "upload_host", "uploadHost")
    upload_dir = _policy_field(data, "upload_dir", "uploadDir")
    oss_access_key_id = _policy_field(data, "oss_access_key_id", "ossAccessKeyId")
    signature = _policy_field(data, "signature", "Signature")
    policy = _policy_field(data, "policy", "Policy")
    x_oss_object_acl = _policy_field(data, "x_oss_object_acl", "x-oss-object-acl", "xOssObjectAcl")
    x_oss_forbid_overwrite = _policy_field(
        data, "x_oss_forbid_overwrite", "x-oss-forbid-overwrite", "xOssForbidOverwrite",
    )

    if not all([upload_host, upload_dir, oss_access_key_id, signature, policy]):
        raise DashScopeUploadError(
            f"getPolicy missing fields: {list(data.keys())[:20]}",
        )

    file_name = path.name
    object_key = f"{upload_dir.rstrip('/')}/{file_name}"

    data_fields: dict[str, str] = {
        "OSSAccessKeyId": oss_access_key_id,
        "Signature": signature,
        "policy": policy,
        "key": object_key,
        "success_action_status": "200",
    }
    if x_oss_object_acl:
        data_fields["x-oss-object-acl"] = x_oss_object_acl
    if x_oss_forbid_overwrite:
        data_fields["x-oss-forbid-overwrite"] = x_oss_forbid_overwrite

    nbytes = path.stat().st_size
    logger.info(
        "DashScope temp upload starting bytes=%s path=%s",
        nbytes, path.name,
    )
    try:
        with path.open("rb") as file_obj:
            files_part = {"file": (file_name, file_obj, "application/octet-stream")}
            up = await client.post(
                upload_host,
                data=data_fields,
                files=files_part,
                timeout=httpx.Timeout(3600.0, connect=120.0),
            )
    except httpx.HTTPError as exc:
        raise DashScopeUploadError(
            f"OSS upload network error: {exc}", retryable=True,
        ) from exc

    if up.status_code < 200 or up.status_code >= 300:
        raise DashScopeUploadError(
            f"OSS upload HTTP {up.status_code}: {up.text[:400]}",
            retryable=up.status_code in (429, 500, 502, 503, 504),
        )

    oss_url = f"oss://{object_key}"
    logger.info(
        "DashScope temp upload ok model=%s key=%s bytes=%s",
        model_name, object_key[:80], nbytes,
    )
    return oss_url
