"""
Feedback Function — Alibaba Cloud FC 3.0 + OSS Pre-signed URL + GitHub Issues

Runtime: Python 3.10 (FC 3.0 built-in)
Handler: index.handler

This function does NOT receive large files. It only handles lightweight JSON
requests for authentication, rate-limiting, and pre-signed URL generation.
The actual ZIP upload goes directly from the client to OSS via pre-signed URL.

Endpoints:
  POST /prepare           — Validate captcha, rate-limit, return pre-signed upload URL
  POST /complete/{id}     — Verify upload succeeded, create GitHub Issue, return feedback_token
  GET  /status/{id}       — Query single feedback status by report_id + token
  POST /status/batch      — Batch query feedback status (up to 50 items)
  POST /reply/{id}        — User reply via bot proxy (token + rate limit + GitHub comment)
  POST /webhook/github    — Receive GitHub Issue events (comment/close/label)
  GET  /unsubscribe/{id}  — Email unsubscribe
  GET  /issues/search     — Public feedback search/browse (no auth required)
  GET  /issues/{number}   — Public issue detail + comments (sanitized, no system info)
  POST /issues/{number}   — Community comment on public issue (rate limited)
  GET  /health            — Health check

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
  GITHUB_WEBHOOK_SECRET  — Webhook secret for HMAC-SHA256 signature verification
  CAPTCHA_SCENE_ID       — 人机验证 2.0「场景ID」(optional, skips verification if empty)
  NOTIFY_EMAIL           — (optional) dev email for internal notifications
  RESEND_API_KEY         — (optional) Resend API key for email
  GITHUB_PAT_LOGIN       — (optional) GitHub username of the PAT, for webhook dedup
  PUBLIC_URL             — (optional) public URL of this FC function, used in unsubscribe links
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
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
    contact_email: str = "",
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
    ]
    if contact_email:
        body_parts.append(f"- **Contact:** `{html.escape(contact_email)}`")
    body_parts += [
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
    """Send internal dev notification when a new feedback is submitted."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    email = os.environ.get("NOTIFY_EMAIL", "")
    if not api_key or not email:
        return
    type_label = "Bug Report" if report_type == "bug" else "Feature Request"
    truncated = (summary[:800] + "...") if len(summary) > 800 else summary
    safe_title = html.escape(title)
    safe_truncated = html.escape(truncated)
    issue_line = f'<p><a href="{html.escape(issue_url)}">View GitHub Issue</a></p>' if issue_url else ""
    try:
        requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "OpenAkita Feedback <onboarding@resend.dev>",
                "to": [email],
                "subject": f"[{type_label}] {title}",
                "html": (
                    f"<h2>{type_label}: {safe_title}</h2>"
                    f"<p><b>Report ID:</b> {html.escape(report_id)}</p>"
                    f"<p><b>Time:</b> {datetime.now(timezone.utc).isoformat()}</p>"
                    f"{issue_line}"
                    f"<hr/><pre style='white-space:pre-wrap;font-size:13px;'>{safe_truncated}</pre>"
                ),
            },
            timeout=10,
        )
    except Exception:
        pass


def _send_user_reply_notification(
    report_id: str, feedback_token: str, user_email: str,
    title: str, comment_author: str, comment_body: str,
) -> None:
    """Send user-facing email when a developer replies to their feedback."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key or not user_email:
        return

    fc_url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    unsubscribe_url = f"{fc_url}/unsubscribe/{report_id}?token={feedback_token}" if fc_url else ""
    unsub_html = (
        f'<p style="color:#999;font-size:12px;margin-top:30px;">'
        f'不想再收到此反馈的通知？<a href="{unsubscribe_url}">点此退订</a> / '
        f'<a href="{unsubscribe_url}">Unsubscribe</a></p>'
    ) if unsubscribe_url else ""

    truncated_body = (comment_body[:1200] + "...") if len(comment_body) > 1200 else comment_body
    safe_title = html.escape(title)
    safe_author = html.escape(comment_author)
    safe_body = html.escape(truncated_body)

    try:
        requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "OpenAkita Feedback <onboarding@resend.dev>",
                "to": [user_email],
                "subject": f"[OpenAkita] 您的反馈有新回复: {title}",
                "html": (
                    f"<h2>您的反馈有新回复 / New Reply on Your Feedback</h2>"
                    f"<p><b>反馈标题:</b> {safe_title}</p>"
                    f"<p><b>回复者:</b> {safe_author}</p>"
                    f"<hr/>"
                    f"<pre style='white-space:pre-wrap;font-size:13px;'>{safe_body}</pre>"
                    f"<hr/>"
                    f"<p style='color:#666;font-size:13px;'>"
                    f"您可以在 OpenAkita 设置中心的「我的反馈」页面查看完整进度。<br/>"
                    f"You can view the full progress in the 'My Feedback' page of OpenAkita Setup Center.</p>"
                    f"{unsub_html}"
                ),
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning("Failed to send user reply notification to %s: %s", user_email, e)


# ---------------------------------------------------------------------------
# OSS index for fast report_id → date lookups
# ---------------------------------------------------------------------------


def _write_index_entry(
    bucket: oss2.Bucket, report_id: str, report_date: str, feedback_token: str,
) -> None:
    """Write a small index object: _index/{report_id}.json → {date, token}."""
    index_key = f"_index/{report_id}.json"
    data = {"date": report_date, "feedback_token": feedback_token}
    try:
        bucket.put_object(index_key, json.dumps(data).encode())
    except Exception as e:
        logger.error("Failed to write index entry %s: %s", index_key, e)


def _read_index_entry(bucket: oss2.Bucket, report_id: str) -> dict | None:
    """Read the index entry for a report_id. Returns {date, feedback_token} or None."""
    index_key = f"_index/{report_id}.json"
    try:
        obj = bucket.get_object(index_key)
        return json.loads(obj.read().decode("utf-8"))
    except oss2.exceptions.NoSuchKey:
        return None
    except Exception as e:
        logger.error("Failed to read index entry %s: %s", index_key, e)
        return None


def _read_metadata(bucket: oss2.Bucket, report_id: str, report_date: str) -> dict | None:
    """Read metadata.json for a given report."""
    meta_key = f"feedback/{report_date}/{report_id}/metadata.json"
    try:
        obj = bucket.get_object(meta_key)
        return json.loads(obj.read().decode("utf-8"))
    except oss2.exceptions.NoSuchKey:
        return None
    except Exception as e:
        logger.error("Failed to read metadata %s: %s", meta_key, e)
        return None


def _write_metadata(bucket: oss2.Bucket, report_id: str, report_date: str, metadata: dict) -> bool:
    meta_key = f"feedback/{report_date}/{report_id}/metadata.json"
    try:
        bucket.put_object(meta_key, json.dumps(metadata, ensure_ascii=False, indent=2).encode())
        return True
    except Exception as e:
        logger.error("Failed to write metadata %s: %s", meta_key, e)
        return False


def _sanitize_status(metadata: dict) -> dict:
    """Extract only the fields safe to return to the user (no IP, no system_info, no token)."""
    replies = metadata.get("developer_replies", [])
    latest_reply_at = replies[-1].get("created_at") if replies else None
    return {
        "report_id": metadata.get("id", ""),
        "title": metadata.get("title", ""),
        "type": metadata.get("type", "bug"),
        "status": metadata.get("status", "open"),
        "summary": metadata.get("summary", ""),
        "labels": metadata.get("labels", []),
        "created_at": metadata.get("created_at", ""),
        "completed_at": metadata.get("completed_at"),
        "resolved_at": metadata.get("resolved_at"),
        "github_issue_url": metadata.get("github_issue_url"),
        "developer_replies": [
            {
                "author": r.get("author", ""),
                "body": r.get("body", ""),
                "created_at": r.get("created_at", ""),
                "source": r.get("source", "developer"),
            }
            for r in replies
        ],
        "latest_reply_at": latest_reply_at,
    }


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
# Public issue browsing helpers
# ---------------------------------------------------------------------------

_SEARCH_RATE_LIMIT = 10  # per minute per IP
_COMMENT_DAILY_LIMIT = 5  # per day per IP

_GH_HEADERS_TMPL: dict[str, str] | None = None


def _gh_headers() -> dict[str, str]:
    """Reusable GitHub API request headers."""
    global _GH_HEADERS_TMPL
    if _GH_HEADERS_TMPL is None:
        _GH_HEADERS_TMPL = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    token = os.environ.get("GITHUB_TOKEN", "")
    h = dict(_GH_HEADERS_TMPL)
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _check_search_rate_limit(ip: str) -> bool:
    """Return True if within search rate limit, False if exceeded."""
    bucket = _get_bucket()
    minute = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    key = f"_ratelimit/search/{ip}/{minute}.txt"
    try:
        result = bucket.get_object(key)
        count = int(result.read().decode().strip())
    except oss2.exceptions.NoSuchKey:
        count = 0
    except Exception:
        count = 0
    if count >= _SEARCH_RATE_LIMIT:
        return False
    bucket.put_object(key, str(count + 1).encode())
    return True


def _check_comment_rate_limit(ip: str) -> bool:
    """Return True if within daily comment limit, False if exceeded."""
    bucket = _get_bucket()
    date = _today()
    key = f"_ratelimit/comment/{ip}/{date}.txt"
    try:
        result = bucket.get_object(key)
        count = int(result.read().decode().strip())
    except oss2.exceptions.NoSuchKey:
        count = 0
    except Exception:
        count = 0
    if count >= _COMMENT_DAILY_LIMIT:
        return False
    bucket.put_object(key, str(count + 1).encode())
    return True


def _map_issue_status(issue: dict) -> str:
    """Map GitHub issue state + labels to internal status string."""
    state = issue.get("state", "open")
    labels = [lb.get("name", "") for lb in issue.get("labels", [])]
    if state == "closed":
        return "wontfix" if "status:wontfix" in labels else "resolved"
    for lb in labels:
        if lb == "status:in_progress":
            return "confirmed"
    return "open"


def _map_issue_type(issue: dict) -> str:
    """Map GitHub issue labels to bug/feature type."""
    labels = [lb.get("name", "") for lb in issue.get("labels", [])]
    if "enhancement" in labels:
        return "feature"
    return "bug"


def _extract_description(body: str) -> str:
    """Extract the ## Description section from an issue body, stripping sensitive
    sections like System Info and Attachments."""
    if not body:
        return ""
    match = re.search(
        r"## Description\s*\n(.*?)(?=\n## |\Z)",
        body, re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def _classify_comment(comment: dict) -> dict | None:
    """Classify a GitHub issue comment. Returns sanitized dict or None to skip."""
    user = comment.get("user", {})
    login = user.get("login", "unknown")
    body_text = comment.get("body", "")

    if user.get("type") == "Bot" or login.endswith("[bot]"):
        return None

    # Prefix checks MUST come before PAT login filter: User Reply and
    # Community Reply are posted by the PAT account, so filtering PAT first
    # would hide them all.
    if body_text.startswith("**[User Reply]**"):
        source = "user_reply"
        body_text = body_text[len("**[User Reply]**"):].strip()
    elif body_text.startswith("**[Community Reply]**"):
        source = "community"
        body_text = body_text[len("**[Community Reply]**"):].strip()
    else:
        source = "developer"
        pat_login = os.environ.get("GITHUB_PAT_LOGIN", "")
        if pat_login and login == pat_login:
            return None

    return {
        "author": login,
        "body": body_text,
        "created_at": comment.get("created_at", ""),
        "source": source,
    }


def _sanitize_issue_item(issue: dict) -> dict:
    """Convert a GitHub issue to a safe public-facing dict (no body)."""
    return {
        "number": issue.get("number"),
        "title": re.sub(r"^\[(Bug|Feature)\]\s*", "", issue.get("title", "")),
        "type": _map_issue_type(issue),
        "status": _map_issue_status(issue),
        "created_at": issue.get("created_at", ""),
        "updated_at": issue.get("updated_at", ""),
        "comments_count": issue.get("comments", 0),
        "html_url": issue.get("html_url", ""),
        "labels": [lb.get("name", "") for lb in issue.get("labels", [])],
    }


# ---------------------------------------------------------------------------
# GET /issues/search — public feedback search / browse
# ---------------------------------------------------------------------------


def _handle_issues_search(evt: dict) -> dict:
    client_ip = _get_client_ip(evt)
    if not _check_search_rate_limit(client_ip):
        return _error("Search rate limit exceeded", 429)

    qs = evt.get("queryParameters", {}) or {}
    q = (qs.get("q", "") or "").strip()
    try:
        page = max(1, min(int(qs.get("page", "1")), 50))
    except (ValueError, TypeError):
        page = 1
    try:
        per_page = max(1, min(int(qs.get("per_page", "20")), 30))
    except (ValueError, TypeError):
        per_page = 20
    state_filter = qs.get("state", "all")
    type_filter = qs.get("type", "")

    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPO", "")
    if not token or not repo:
        return _error("GitHub integration not configured", 503)

    headers = _gh_headers()

    try:
        if q:
            search_q = f"repo:{repo} is:issue label:source:feedback {q}"
            if state_filter in ("open", "closed"):
                search_q += f" state:{state_filter}"
            if type_filter == "bug":
                search_q += " label:bug"
            elif type_filter == "feature":
                search_q += " label:enhancement"

            resp = requests.get(
                "https://api.github.com/search/issues",
                headers=headers,
                params={"q": search_q, "per_page": per_page, "page": page,
                        "sort": "created", "order": "desc"},
                timeout=10,
            )
        else:
            labels = "source:feedback"
            if type_filter == "bug":
                labels += ",bug"
            elif type_filter == "feature":
                labels += ",enhancement"
            params: dict = {
                "labels": labels,
                "per_page": per_page, "page": page,
                "sort": "created", "direction": "desc",
            }
            if state_filter in ("open", "closed"):
                params["state"] = state_filter
            else:
                params["state"] = "all"

            resp = requests.get(
                f"https://api.github.com/repos/{repo}/issues",
                headers=headers, params=params, timeout=10,
            )

        if resp.status_code != 200:
            logger.error("GitHub API error in search: %s %s", resp.status_code, resp.text[:300])
            return _error(f"GitHub API error ({resp.status_code})", 502)

        data = resp.json()

        if q:
            items = [_sanitize_issue_item(it) for it in data.get("items", [])]
            total_count = data.get("total_count", 0)
        else:
            raw_items = data if isinstance(data, list) else []
            items = [_sanitize_issue_item(it) for it in raw_items
                     if not it.get("pull_request")]
            link_header = resp.headers.get("Link", "")
            has_next = 'rel="next"' in link_header
            total_count = None  # Issues API doesn't return total_count
            if not has_next and page == 1:
                total_count = len(items)

        return _json_response({
            "items": items,
            "total_count": total_count,
            "page": page,
            "per_page": per_page,
            "has_next": len(items) == per_page,
        })

    except requests.Timeout:
        return _error("GitHub API timeout", 504)
    except Exception as e:
        logger.error("Issues search error: %s", e)
        return _error(f"Search failed: {e}", 500)


# ---------------------------------------------------------------------------
# GET /issues/{number} — public issue detail + comments
# ---------------------------------------------------------------------------


def _handle_issue_detail(evt: dict, issue_number: int) -> dict:
    client_ip = _get_client_ip(evt)
    if not _check_search_rate_limit(client_ip):
        return _error("Rate limit exceeded", 429)

    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPO", "")
    if not token or not repo:
        return _error("GitHub integration not configured", 503)

    headers = _gh_headers()

    try:
        issue_resp = requests.get(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}",
            headers=headers, timeout=10,
        )
        if issue_resp.status_code == 404:
            return _error("Issue not found", 404)
        if issue_resp.status_code != 200:
            return _error(f"GitHub API error ({issue_resp.status_code})", 502)

        issue = issue_resp.json()

        label_names = [lb.get("name", "") for lb in issue.get("labels", [])]
        if "source:feedback" not in label_names:
            return _error("Not a feedback issue", 403)

        summary = _extract_description(issue.get("body", "") or "")

        comments: list[dict] = []
        comments_url = issue.get("comments_url", "")
        if comments_url and issue.get("comments", 0) > 0:
            cm_resp = requests.get(
                comments_url,
                headers=headers,
                params={"per_page": 100},
                timeout=10,
            )
            if cm_resp.status_code == 200:
                for raw_cm in cm_resp.json():
                    classified = _classify_comment(raw_cm)
                    if classified:
                        comments.append(classified)

        return _json_response({
            "number": issue.get("number"),
            "title": re.sub(r"^\[(Bug|Feature)\]\s*", "", issue.get("title", "")),
            "type": _map_issue_type(issue),
            "status": _map_issue_status(issue),
            "summary": summary,
            "created_at": issue.get("created_at", ""),
            "updated_at": issue.get("updated_at", ""),
            "html_url": issue.get("html_url", ""),
            "labels": label_names,
            "comments": comments,
        })

    except requests.Timeout:
        return _error("GitHub API timeout", 504)
    except Exception as e:
        logger.error("Issue detail error: %s", e)
        return _error(f"Detail fetch failed: {e}", 500)


# ---------------------------------------------------------------------------
# POST /issues/{number}/comment — community reply on public issue
# ---------------------------------------------------------------------------


def _handle_issue_comment(evt: dict, issue_number: int) -> dict:
    body = _parse_json_body(evt)
    text = (body.get("body", "") or "").strip()
    if not text:
        return _error("Comment body is required", 400)
    if len(text) > 2000:
        return _error("Comment too long (max 2000 characters)", 400)

    client_ip = _get_client_ip(evt)
    if not _check_comment_rate_limit(client_ip):
        return _error("Comment rate limit exceeded (max 5/day)", 429)

    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPO", "")
    if not token or not repo:
        return _error("GitHub integration not configured", 503)

    headers = _gh_headers()

    try:
        issue_resp = requests.get(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}",
            headers=headers, timeout=10,
        )
        if issue_resp.status_code == 404:
            return _error("Issue not found", 404)
        if issue_resp.status_code != 200:
            return _error(f"GitHub API error ({issue_resp.status_code})", 502)

        issue = issue_resp.json()
        label_names = [lb.get("name", "") for lb in issue.get("labels", [])]
        if "source:feedback" not in label_names:
            return _error("Not a feedback issue", 403)

        comment_text = f"**[Community Reply]**\n\n{text}"
        cm_resp = requests.post(
            f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments",
            headers=headers,
            json={"body": comment_text},
            timeout=15,
        )
        if cm_resp.status_code == 201:
            return _json_response({
                "status": "ok",
                "comment_url": cm_resp.json().get("html_url"),
            })
        logger.error("GitHub comment failed: %s %s", cm_resp.status_code, cm_resp.text[:300])
        return _error(f"GitHub API error ({cm_resp.status_code})", 502)

    except requests.Timeout:
        return _error("GitHub API timeout", 504)
    except Exception as e:
        logger.error("Issue comment error: %s", e)
        return _error(f"Comment failed: {e}", 500)


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

    if path == "/status/batch" and method == "POST":
        return _handle_status_batch(evt)

    status_match = re.match(r"^/status/([a-zA-Z0-9_-]+)$", path)
    if status_match and method == "GET":
        return _handle_status_single(evt, status_match.group(1))

    if path == "/webhook/github" and method == "POST":
        return _handle_github_webhook(evt)

    reply_match = re.match(r"^/reply/([a-zA-Z0-9_-]+)$", path)
    if reply_match and method == "POST":
        return _handle_reply(evt, reply_match.group(1))

    unsubscribe_match = re.match(r"^/unsubscribe/([a-zA-Z0-9_-]+)$", path)
    if unsubscribe_match and method == "GET":
        return _handle_unsubscribe(evt, unsubscribe_match.group(1))

    if path == "/issues/search" and method == "GET":
        return _handle_issues_search(evt)

    issue_match = re.match(r"^/issues/(\d+)$", path)
    if issue_match:
        num = int(issue_match.group(1))
        if method == "GET":
            return _handle_issue_detail(evt, num)
        if method == "POST":
            return _handle_issue_comment(evt, num)

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
    contact_email = body.get("contact_email", "")
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
        "contact_email": contact_email[:200] if contact_email else "",
        "email_unsubscribed": False,
        "developer_replies": [],
        "labels": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resolved_at": None,
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

    # 1. Verify the ZIP was actually uploaded
    try:
        exists = bucket.object_exists(zip_key)
    except Exception:
        exists = False

    if not exists:
        return _error("Report ZIP not found in storage. Upload may have failed.", 404)

    # 2. Read existing metadata
    metadata = _read_metadata(bucket, report_id, report_date)
    if not metadata:
        metadata = {"id": report_id, "date": report_date}

    # 3. Get ZIP size
    try:
        head = bucket.head_object(zip_key)
        metadata["size_bytes"] = head.content_length
    except Exception:
        pass

    # 4. Generate feedback_token for user-side status queries
    feedback_token = secrets.token_urlsafe(24)
    metadata["feedback_token"] = feedback_token

    # 5. Create GitHub Issue (embed report_id for webhook cross-referencing)
    issue_url = _create_github_issue(
        report_id=report_id,
        report_type=metadata.get("type", "bug"),
        title=metadata.get("title", "(untitled)"),
        summary=metadata.get("summary", ""),
        system_info=metadata.get("system_info", ""),
        oss_path=zip_key,
        contact_email=metadata.get("contact_email", ""),
    )

    if issue_url:
        metadata["github_issue_url"] = issue_url

    metadata["completed_at"] = datetime.now(timezone.utc).isoformat()

    # 6. Update metadata
    _write_metadata(bucket, report_id, report_date, metadata)

    # 7. Write to OSS index for fast lookups (avoids scanning all date folders)
    _write_index_entry(bucket, report_id, report_date, feedback_token)

    # 8. Send dev notification
    _send_notification(
        report_id, metadata.get("title", ""),
        metadata.get("summary", ""), metadata.get("type", "bug"), issue_url,
    )

    return _json_response({
        "status": "ok",
        "report_id": report_id,
        "feedback_token": feedback_token,
        "issue_url": issue_url,
    })


# ---------------------------------------------------------------------------
# GET /status/{id}?token=xxx — single feedback status query
# ---------------------------------------------------------------------------


def _handle_status_single(evt: dict, report_id: str) -> dict:
    qs = evt.get("queryParameters", {}) or {}
    token = qs.get("token", "")
    if not token:
        return _error("Missing token parameter", 401)

    bucket = _get_bucket()
    index_entry = _read_index_entry(bucket, report_id)
    if not index_entry:
        return _error("Report not found", 404)

    if not secrets.compare_digest(index_entry.get("feedback_token", ""), token):
        return _error("Invalid token", 403)

    metadata = _read_metadata(bucket, report_id, index_entry["date"])
    if not metadata:
        return _error("Report metadata not found", 404)

    return _json_response(_sanitize_status(metadata))


# ---------------------------------------------------------------------------
# POST /status/batch — batch feedback status query (up to 50)
# ---------------------------------------------------------------------------


_BATCH_MAX = 50


def _handle_status_batch(evt: dict) -> dict:
    body = _parse_json_body(evt)
    items = body.get("items", [])
    if not isinstance(items, list) or len(items) == 0:
        return _error("items must be a non-empty array", 400)
    if len(items) > _BATCH_MAX:
        return _error(f"Maximum {_BATCH_MAX} items per batch", 400)

    bucket = _get_bucket()
    results = {}
    for item in items:
        rid = item.get("report_id", "")
        tok = item.get("token", "")
        if not rid or not tok:
            continue

        index_entry = _read_index_entry(bucket, rid)
        if not index_entry:
            results[rid] = {"error": "not_found"}
            continue

        if not secrets.compare_digest(index_entry.get("feedback_token", ""), tok):
            results[rid] = {"error": "invalid_token"}
            continue

        metadata = _read_metadata(bucket, rid, index_entry["date"])
        if not metadata:
            results[rid] = {"error": "metadata_missing"}
            continue

        results[rid] = _sanitize_status(metadata)

    return _json_response({"results": results})


# ---------------------------------------------------------------------------
# POST /webhook/github — GitHub Issue event receiver
# ---------------------------------------------------------------------------


def _verify_github_signature(evt: dict, raw_body: bytes) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature."""
    webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not webhook_secret:
        logger.warning("GITHUB_WEBHOOK_SECRET not set, skipping signature verification")
        return True

    headers = evt.get("headers", {})
    sig_header = ""
    for k, v in headers.items():
        if k.lower() == "x-hub-signature-256":
            sig_header = v
            break

    if not sig_header or not sig_header.startswith("sha256="):
        logger.warning("Missing or malformed X-Hub-Signature-256 header")
        return False

    expected = hmac.new(
        webhook_secret.encode("utf-8"), raw_body, hashlib.sha256,
    ).hexdigest()
    received = sig_header[7:]  # strip "sha256="
    return hmac.compare_digest(expected, received)


def _get_raw_body(evt: dict) -> bytes:
    """Get raw body bytes from FC event (handles base64)."""
    raw = evt.get("body", "")
    if evt.get("isBase64Encoded", False):
        import base64
        return base64.b64decode(raw)
    if isinstance(raw, str):
        return raw.encode("utf-8")
    return raw


def _extract_report_id_from_issue(issue_body: str) -> str | None:
    """Extract report_id from the GitHub Issue body created by _create_github_issue."""
    match = re.search(r"\*\*Report ID:\*\*\s*`([a-zA-Z0-9_-]+)`", issue_body)
    return match.group(1) if match else None


def _handle_github_webhook(evt: dict) -> dict:
    raw_body = _get_raw_body(evt)

    if not _verify_github_signature(evt, raw_body):
        return _error("Signature verification failed", 403)

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, TypeError):
        return _error("Invalid JSON", 400)

    headers = evt.get("headers", {})
    event_type = ""
    for k, v in headers.items():
        if k.lower() == "x-github-event":
            event_type = v
            break

    if event_type not in ("issues", "issue_comment"):
        return _json_response({"status": "ignored", "reason": f"unhandled event: {event_type}"})

    action = payload.get("action", "")
    issue = payload.get("issue", {})
    issue_body = issue.get("body", "") or ""
    report_id = _extract_report_id_from_issue(issue_body)

    if not report_id:
        return _json_response({"status": "ignored", "reason": "no report_id in issue body"})

    bucket = _get_bucket()
    index_entry = _read_index_entry(bucket, report_id)
    if not index_entry:
        return _json_response({"status": "ignored", "reason": "report not in index"})

    metadata = _read_metadata(bucket, report_id, index_entry["date"])
    if not metadata:
        return _json_response({"status": "ignored", "reason": "metadata not found"})

    changed = False

    if event_type == "issue_comment" and action == "created":
        comment = payload.get("comment", {})
        comment_user = comment.get("user", {})
        comment_author = comment_user.get("login", "unknown")
        comment_body = comment.get("body", "")
        comment_time = comment.get("created_at", datetime.now(timezone.utc).isoformat())

        if comment_user.get("type") == "Bot" or comment_author.endswith("[bot]"):
            return _json_response({
                "status": "ignored", "reason": "bot comment skipped",
            })

        if comment_body.startswith("**[User Reply]**"):
            return _json_response({
                "status": "ignored", "reason": "user reply echo skipped",
            })

        if comment_body.startswith("**[Community Reply]**"):
            return _json_response({
                "status": "ignored", "reason": "community reply echo skipped",
            })

        pat_login = os.environ.get("GITHUB_PAT_LOGIN", "")
        if pat_login and comment_author == pat_login:
            return _json_response({
                "status": "ignored", "reason": "PAT account echo skipped",
            })

        if not metadata.get("developer_replies"):
            metadata["developer_replies"] = []
        metadata["developer_replies"].append({
            "author": comment_author,
            "body": comment_body[:2000],
            "created_at": comment_time,
            "source": "developer",
        })
        changed = True

        if not metadata.get("email_unsubscribed") and metadata.get("contact_email"):
            _send_user_reply_notification(
                report_id=report_id,
                feedback_token=index_entry.get("feedback_token", ""),
                user_email=metadata["contact_email"],
                title=metadata.get("title", ""),
                comment_author=comment_author,
                comment_body=comment_body,
            )

    if event_type == "issues":
        issue_labels = [lb.get("name", "") for lb in issue.get("labels", [])]
        metadata["labels"] = issue_labels

        if action == "closed":
            metadata["status"] = "resolved"
            metadata["resolved_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
        elif action == "reopened":
            metadata["status"] = "open"
            metadata["resolved_at"] = None
            changed = True
        elif action == "labeled" or action == "unlabeled":
            for lb in issue_labels:
                if lb.startswith("status:"):
                    new_status = lb.split(":", 1)[1]
                    if new_status in ("open", "in_progress", "resolved", "closed", "wontfix"):
                        metadata["status"] = new_status
            changed = True

    if changed:
        _write_metadata(bucket, report_id, index_entry["date"], metadata)

    return _json_response({"status": "ok", "report_id": report_id, "updated": changed})


# ---------------------------------------------------------------------------
# POST /reply/{id} — user reply via bot proxy
# ---------------------------------------------------------------------------

_REPLY_DAILY_LIMIT = 20


def _check_reply_rate_limit(report_id: str) -> bool:
    """Return True if within daily limit, False if exceeded."""
    bucket = _get_bucket()
    date = _today()
    key = f"_ratelimit/reply/{report_id}/{date}.txt"
    try:
        result = bucket.get_object(key)
        count = int(result.read().decode().strip())
    except oss2.exceptions.NoSuchKey:
        count = 0
    except Exception:
        count = 0

    if count >= _REPLY_DAILY_LIMIT:
        return False

    bucket.put_object(key, str(count + 1).encode())
    return True


def _handle_reply(evt: dict, report_id: str) -> dict:
    body = _parse_json_body(evt)
    token = body.get("token", "")
    reply_body = body.get("body", "").strip()

    if not token:
        return _error("Missing token", 401)
    if not reply_body:
        return _error("Reply body is required", 400)
    if len(reply_body) > 2000:
        return _error("Reply too long (max 2000 characters)", 400)

    bucket = _get_bucket()
    index_entry = _read_index_entry(bucket, report_id)
    if not index_entry:
        return _error("Report not found", 404)

    if not secrets.compare_digest(index_entry.get("feedback_token", ""), token):
        return _error("Invalid token", 403)

    if not _check_reply_rate_limit(report_id):
        return _error("Too many replies today (max 20)", 429)

    metadata = _read_metadata(bucket, report_id, index_entry["date"])
    if not metadata:
        return _error("Report metadata not found", 404)

    github_issue_url = metadata.get("github_issue_url", "")
    if not github_issue_url:
        return _error("No GitHub Issue linked", 400)

    issue_match = re.search(r"/issues/(\d+)", github_issue_url)
    if not issue_match:
        return _error("Cannot parse issue number from URL", 400)

    issue_number = issue_match.group(1)
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    gh_repo = os.environ.get("GITHUB_REPO", "")
    if not gh_token or not gh_repo:
        return _error("GitHub integration not configured", 503)

    comment_text = f"**[User Reply]**\n\n{reply_body}"
    comment_url = None
    try:
        resp = requests.post(
            f"https://api.github.com/repos/{gh_repo}/issues/{issue_number}/comments",
            headers={
                "Authorization": f"Bearer {gh_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"body": comment_text},
            timeout=15,
        )
        if resp.status_code == 201:
            comment_url = resp.json().get("html_url")
        else:
            logger.error(
                "GitHub comment failed: %s %s", resp.status_code, resp.text[:300],
            )
            return _error(f"GitHub API error ({resp.status_code})", 502)
    except Exception as e:
        logger.error("GitHub comment error: %s", e)
        return _error(f"GitHub API error: {e}", 502)

    now = datetime.now(timezone.utc).isoformat()
    if not metadata.get("developer_replies"):
        metadata["developer_replies"] = []
    metadata["developer_replies"].append({
        "author": "user",
        "body": reply_body[:2000],
        "created_at": now,
        "source": "user_reply",
    })

    if not _write_metadata(bucket, report_id, index_entry["date"], metadata):
        logger.warning("Reply posted to GitHub but metadata write failed for %s", report_id)

    return _json_response({"status": "ok", "comment_url": comment_url})


# ---------------------------------------------------------------------------
# GET /unsubscribe/{id}?token=xxx — email unsubscribe
# ---------------------------------------------------------------------------


def _handle_unsubscribe(evt: dict, report_id: str) -> dict:
    qs = evt.get("queryParameters", {}) or {}
    token = qs.get("token", "")
    if not token:
        return _error("Missing token", 401)

    bucket = _get_bucket()
    index_entry = _read_index_entry(bucket, report_id)
    if not index_entry:
        return _error("Report not found", 404)

    if not secrets.compare_digest(index_entry.get("feedback_token", ""), token):
        return _error("Invalid token", 403)

    metadata = _read_metadata(bucket, report_id, index_entry["date"])
    if not metadata:
        return _error("Report metadata not found", 404)

    metadata["email_unsubscribed"] = True
    _write_metadata(bucket, report_id, index_entry["date"], metadata)

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
        },
        "isBase64Encoded": False,
        "body": (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>Unsubscribed</title>"
            "<style>body{font-family:sans-serif;text-align:center;padding:60px 20px;}"
            "h1{color:#333;}p{color:#666;}</style></head>"
            "<body><h1>✅ 已退订</h1>"
            f"<p>反馈 <code>{html.escape(report_id)}</code> 的邮件通知已关闭。</p>"
            "<p>You have been unsubscribed from email notifications for this feedback report.</p>"
            "</body></html>"
        ),
    }
