"""
Feedback Function — Alibaba Cloud FC 3.0 + OSS Pre-signed URL + GitHub Issues

Runtime: Python 3.10 (FC 3.0 built-in)
Handler: index.handler

This function does NOT receive large files. It only handles lightweight JSON
requests for authentication, rate-limiting, and pre-signed URL generation.
The actual ZIP upload goes directly from the client to OSS via pre-signed URL.

Endpoints:
  POST /prepare         — Validate captcha, rate-limit, return pre-signed upload URL
  POST /complete/{id}   — Verify upload succeeded, create GitHub Issue
  GET  /health          — Health check

Environment variables (set in FC console, never in source code):
  OSS_ENDPOINT           — Internal endpoint, e.g. https://oss-cn-hangzhou-internal.aliyuncs.com
  OSS_PUBLIC_ENDPOINT    — External endpoint for pre-signed URLs,
                           e.g. https://oss-cn-hangzhou.aliyuncs.com
                           If unset, derived from OSS_ENDPOINT by removing '-internal'.
  OSS_BUCKET             — e.g. openakita-feedback
  OSS_ACCESS_KEY_ID      — RAM user AccessKey (also used for CAPTCHA 2.0 verification)
  OSS_ACCESS_KEY_SECRET  — RAM user AccessKey Secret
  GITHUB_TOKEN           — Fine-grained PAT (Issues:Write on target repo)
  GITHUB_REPO            — e.g. openakita/openakita
  CAPTCHA_SCENE_ID       — 人机验证 2.0「场景ID」(optional, skips verification if empty)
  NOTIFY_EMAIL           — (optional) email for notifications
  RESEND_API_KEY         — (optional) Resend API key for email
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

import oss2
import requests

logger = logging.getLogger(__name__)

PRESIGN_EXPIRE_SECONDS = 600  # 10 minutes
IP_DAILY_LIMIT = 10
GLOBAL_DAILY_LIMIT = 1000

# ---------------------------------------------------------------------------
# OSS helpers
# ---------------------------------------------------------------------------

_oss_bucket: oss2.Bucket | None = None
_oss_public_bucket: oss2.Bucket | None = None


def _get_auth() -> oss2.Auth:
    return oss2.Auth(
        os.environ["OSS_ACCESS_KEY_ID"],
        os.environ["OSS_ACCESS_KEY_SECRET"],
    )


def _get_bucket() -> oss2.Bucket:
    """Bucket with internal endpoint — for server-side reads/writes."""
    global _oss_bucket
    if _oss_bucket is None:
        _oss_bucket = oss2.Bucket(
            _get_auth(), os.environ["OSS_ENDPOINT"], os.environ["OSS_BUCKET"],
        )
    return _oss_bucket


def _get_public_endpoint() -> str:
    """External OSS endpoint for pre-signed URLs (accessible from user machines)."""
    public = os.environ.get("OSS_PUBLIC_ENDPOINT", "")
    if public:
        return public
    internal = os.environ["OSS_ENDPOINT"]
    return internal.replace("-internal", "")


def _get_public_bucket() -> oss2.Bucket:
    """Bucket with public endpoint — only for generating pre-signed URLs."""
    global _oss_public_bucket
    if _oss_public_bucket is None:
        _oss_public_bucket = oss2.Bucket(
            _get_auth(), _get_public_endpoint(), os.environ["OSS_BUCKET"],
        )
    return _oss_public_bucket


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Rate limiting (OSS-based counters)
# ---------------------------------------------------------------------------


def _check_rate_limit(ip: str) -> str | None:
    """Return an error message if rate-limited, else None."""
    bucket = _get_bucket()
    date = _today()
    checks = [
        (f"_ratelimit/ip/{ip}/{date}.txt", IP_DAILY_LIMIT, "IP daily limit reached"),
        (f"_ratelimit/global/{date}.txt", GLOBAL_DAILY_LIMIT, "Global daily limit reached"),
    ]
    for key, limit, msg in checks:
        try:
            result = bucket.get_object(key)
            count = int(result.read().decode().strip())
        except oss2.exceptions.NoSuchKey:
            count = 0
        except Exception:
            count = 0
        if count >= limit:
            return msg

    for key, _limit, _msg in checks:
        try:
            result = bucket.get_object(key)
            count = int(result.read().decode().strip())
        except Exception:
            count = 0
        bucket.put_object(key, str(count + 1).encode())

    return None


# ---------------------------------------------------------------------------
# Alibaba Cloud CAPTCHA 2.0 server-side verification
#
# Uses VerifyIntelligentCaptcha OpenAPI with V1 RPC signing (HMAC-SHA1).
# Auth reuses the same AccessKey as OSS — no separate "ekey" needed.
# RAM user must have AliyunYundunAFSFullAccess permission.
# ---------------------------------------------------------------------------

_CAPTCHA_ENDPOINT = "https://captcha.cn-shanghai.aliyuncs.com/"


def _percent_encode(s: str) -> str:
    """Alibaba Cloud percent-encoding (RFC 3986, keep unreserved chars only)."""
    import urllib.parse
    return urllib.parse.quote(str(s), safe="")


def _verify_captcha(verify_param: str) -> bool:
    """Verify CAPTCHA 2.0 token via VerifyIntelligentCaptcha API.

    CaptchaVerifyParam is passed as-is — the official docs explicitly forbid
    any parsing or modification of this value.
    """
    ak_id = os.environ.get("OSS_ACCESS_KEY_ID", "")
    ak_secret = os.environ.get("OSS_ACCESS_KEY_SECRET", "")

    if not ak_id or not ak_secret:
        logger.warning("AccessKey not configured, skipping CAPTCHA verification")
        return True

    import base64
    import hashlib
    import hmac
    import urllib.parse
    import uuid as _uuid

    params: dict[str, str] = {
        "Action": "VerifyIntelligentCaptcha",
        "Version": "2023-03-05",
        "Format": "JSON",
        "AccessKeyId": ak_id,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "SignatureVersion": "1.0",
        "SignatureNonce": _uuid.uuid4().hex,
        "CaptchaVerifyParam": verify_param,
    }

    scene_id = os.environ.get("CAPTCHA_SCENE_ID", "")
    if scene_id:
        params["SceneId"] = scene_id

    sorted_params = sorted(params.items())
    canonicalized = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted_params
    )
    string_to_sign = f"POST&{_percent_encode('/')}&{_percent_encode(canonicalized)}"

    signing_key = f"{ak_secret}&".encode("utf-8")
    signature = base64.b64encode(
        hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    ).decode("utf-8")
    params["Signature"] = signature

    try:
        resp = requests.post(_CAPTCHA_ENDPOINT, data=params, timeout=5)
        result = resp.json()

        if result.get("Code") == "Success":
            verify_result = result.get("Result", {}).get("VerifyResult", False)
            verify_code = result.get("Result", {}).get("VerifyCode", "")
            if not verify_result:
                logger.warning("CAPTCHA rejected: VerifyCode=%s", verify_code)
            return verify_result

        logger.error(
            "CAPTCHA API error: Code=%s, Message=%s",
            result.get("Code"), result.get("Message"),
        )
        return False
    except Exception as e:
        logger.error("CAPTCHA verification error: %s", e)
        return False


# ---------------------------------------------------------------------------
# GitHub Issue creation
# ---------------------------------------------------------------------------


def _create_github_issue(
    report_id: str, report_type: str, title: str,
    summary: str, system_info: str, oss_path: str,
) -> str | None:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPO", "")
    if not token or not repo:
        return None

    type_prefix = "[Bug]" if report_type == "bug" else "[Feature]"
    issue_title = f"{type_prefix} {title}"

    labels = ["source:feedback", "status:open"]
    labels.append("bug" if report_type == "bug" else "enhancement")

    version_match = re.search(
        r"openakita[_ ]version[\"']?\s*[:=]\s*[\"']?([^\s\"',}]+)",
        system_info, re.I,
    )
    if version_match:
        labels.append(f"version:{version_match.group(1)}")

    os_match = re.search(r'"?os"?\s*[:=]\s*"?([^",}\n]+)', system_info, re.I)
    if os_match:
        os_val = os_match.group(1).strip().lower()
        if "windows" in os_val:
            labels.append("os:Windows")
        elif "darwin" in os_val or "mac" in os_val:
            labels.append("os:macOS")
        elif "linux" in os_val:
            labels.append("os:Linux")

    body_parts = [
        "## Feedback Report",
        f"- **Report ID:** `{report_id}`",
        f"- **Type:** {report_type}",
        f"- **Created:** {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Description",
        summary or "(No description provided)",
        "",
    ]
    if system_info:
        body_parts += ["## System Info", "```", system_info[:1500], "```", ""]
    body_parts += [
        "## Attachments",
        f"Diagnostic ZIP stored at: `{oss_path}`",
        "",
        "---",
        "*Auto-created by OpenAkita Feedback Service*",
    ]

    try:
        resp = requests.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"title": issue_title, "body": "\n".join(body_parts), "labels": labels},
            timeout=15,
        )
        if resp.status_code == 201:
            return resp.json().get("html_url")
        logger.error("GitHub Issue creation failed: %s %s", resp.status_code, resp.text[:300])
    except Exception as e:
        logger.error("GitHub Issue creation error: %s", e)
    return None


# ---------------------------------------------------------------------------
# Email notification (optional)
# ---------------------------------------------------------------------------


def _send_notification(
    report_id: str, title: str, summary: str,
    report_type: str, issue_url: str | None,
) -> None:
    api_key = os.environ.get("RESEND_API_KEY", "")
    email = os.environ.get("NOTIFY_EMAIL", "")
    if not api_key or not email:
        return
    type_label = "Bug Report" if report_type == "bug" else "Feature Request"
    truncated = (summary[:800] + "...") if len(summary) > 800 else summary
    issue_line = f'<p><a href="{issue_url}">View GitHub Issue</a></p>' if issue_url else ""
    try:
        requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "OpenAkita Feedback <onboarding@resend.dev>",
                "to": [email],
                "subject": f"[{type_label}] {title}",
                "html": (
                    f"<h2>{type_label}: {title}</h2>"
                    f"<p><b>Report ID:</b> {report_id}</p>"
                    f"<p><b>Time:</b> {datetime.now(timezone.utc).isoformat()}</p>"
                    f"{issue_line}"
                    f"<hr/><pre style='white-space:pre-wrap;font-size:13px;'>{truncated}</pre>"
                ),
            },
            timeout=10,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# HTTP response helpers
# ---------------------------------------------------------------------------


def _json_response(data: dict, status: int = 200) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        },
        "isBase64Encoded": False,
        "body": json.dumps(data),
    }


def _error(msg: str, status: int) -> dict:
    return _json_response({"error": msg}, status)


# ---------------------------------------------------------------------------
# Main handler — FC 3.0 event-based
# ---------------------------------------------------------------------------


def handler(event, context):
    evt = json.loads(event)
    method = evt.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = evt.get("requestContext", {}).get("http", {}).get("path", "/")

    if method == "OPTIONS":
        return {
            "statusCode": 204,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "*",
            },
            "body": "",
        }

    if path in ("/", "/health") and method == "GET":
        return _json_response({"status": "ok", "service": "feedback-fc"})

    if path == "/prepare" and method == "POST":
        return _handle_prepare(evt)

    complete_match = re.match(r"^/complete/([a-zA-Z0-9_-]+)$", path)
    if complete_match and method == "POST":
        return _handle_complete(evt, complete_match.group(1))

    return _error("Not found", 404)


def _parse_json_body(evt: dict) -> dict:
    """Parse JSON body from FC 3.0 event, handling optional Base64 encoding."""
    raw = evt.get("body", "")
    if not raw:
        return {}
    if evt.get("isBase64Encoded", False):
        import base64
        raw = base64.b64decode(raw).decode("utf-8")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _get_client_ip(evt: dict) -> str:
    headers = evt.get("headers", {})
    forwarded = ""
    for k, v in headers.items():
        if k.lower() == "x-forwarded-for":
            forwarded = v
            break
    if forwarded:
        return forwarded.split(",")[0].strip()
    return evt.get("requestContext", {}).get("http", {}).get("sourceIp", "unknown")


# ---------------------------------------------------------------------------
# POST /prepare — validate + issue pre-signed upload URL
# ---------------------------------------------------------------------------


def _handle_prepare(evt: dict) -> dict:
    body = _parse_json_body(evt)

    report_id = body.get("report_id", "")
    title = body.get("title", "")
    report_type = body.get("type", "bug")
    summary = body.get("summary", "")
    system_info = body.get("system_info", "")
    captcha_param = body.get("captcha_verify_param", "")
    client_ip = _get_client_ip(evt)

    if not report_id or not re.match(r"^[a-zA-Z0-9_-]+$", report_id):
        return _error("Invalid report_id", 400)
    if not title or len(title) < 2:
        return _error("Title must be at least 2 characters", 400)

    # 1. Verify captcha (CAPTCHA 2.0 VerifyIntelligentCaptcha)
    if captcha_param and captcha_param != "none":
        if not _verify_captcha(captcha_param):
            return _error("CAPTCHA verification failed", 403)

    # 2. Rate limiting
    rate_msg = _check_rate_limit(client_ip)
    if rate_msg:
        return _error(rate_msg, 429)

    # 3. Store metadata to OSS
    date = _today()
    zip_key = f"feedback/{date}/{report_id}/report.zip"
    meta_key = f"feedback/{date}/{report_id}/metadata.json"
    metadata = {
        "id": report_id,
        "type": report_type,
        "title": title,
        "summary": summary[:2000],
        "system_info": system_info[:2000],
        "status": "open",
        "ip": client_ip,
        "date": date,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    bucket = _get_bucket()
    try:
        bucket.put_object(meta_key, json.dumps(metadata, ensure_ascii=False, indent=2).encode())
    except Exception as e:
        logger.error("OSS metadata write failed: %s", e)
        return _error(f"Storage error: {e}", 502)

    # 4. Generate pre-signed PUT URL (using public endpoint so user machines can reach it)
    #    slash_safe=True keeps '/' literal in the URL path, avoiding %2F encoding
    #    issues between oss2 signature computation and the HTTP client.
    public_bucket = _get_public_bucket()
    try:
        upload_url = public_bucket.sign_url(
            "PUT", zip_key, PRESIGN_EXPIRE_SECONDS, slash_safe=True,
        )
    except Exception as e:
        logger.error("Failed to generate pre-signed URL: %s", e)
        return _error(f"Sign URL error: {e}", 500)

    return _json_response({
        "upload_url": upload_url,
        "report_id": report_id,
        "report_date": date,
    })


# ---------------------------------------------------------------------------
# POST /complete/{id} — confirm upload + create GitHub Issue
# ---------------------------------------------------------------------------


def _handle_complete(evt: dict, report_id: str) -> dict:
    body = _parse_json_body(evt)
    report_date = body.get("report_date", "")

    if not report_date or not re.match(r"^\d{4}-\d{2}-\d{2}$", report_date):
        return _error("Invalid or missing report_date", 400)

    bucket = _get_bucket()
    zip_key = f"feedback/{report_date}/{report_id}/report.zip"
    meta_key = f"feedback/{report_date}/{report_id}/metadata.json"

    # 1. Verify the ZIP was actually uploaded
    try:
        exists = bucket.object_exists(zip_key)
    except Exception:
        exists = False

    if not exists:
        return _error("Report ZIP not found in storage. Upload may have failed.", 404)

    # 2. Read existing metadata
    try:
        meta_obj = bucket.get_object(meta_key)
        metadata = json.loads(meta_obj.read().decode("utf-8"))
    except Exception:
        metadata = {"id": report_id, "date": report_date}

    # 3. Get ZIP size
    try:
        head = bucket.head_object(zip_key)
        metadata["size_bytes"] = head.content_length
    except Exception:
        pass

    # 4. Create GitHub Issue
    issue_url = _create_github_issue(
        report_id=report_id,
        report_type=metadata.get("type", "bug"),
        title=metadata.get("title", "(untitled)"),
        summary=metadata.get("summary", ""),
        system_info=metadata.get("system_info", ""),
        oss_path=zip_key,
    )

    if issue_url:
        metadata["github_issue_url"] = issue_url

    metadata["completed_at"] = datetime.now(timezone.utc).isoformat()

    # 5. Update metadata
    try:
        bucket.put_object(meta_key, json.dumps(metadata, ensure_ascii=False, indent=2).encode())
    except Exception:
        pass

    # 6. Send notification
    _send_notification(
        report_id, metadata.get("title", ""),
        metadata.get("summary", ""), metadata.get("type", "bug"), issue_url,
    )

    return _json_response({
        "status": "ok",
        "report_id": report_id,
        "issue_url": issue_url,
    })
