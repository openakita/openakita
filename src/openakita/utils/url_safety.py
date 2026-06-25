"""
URL 安全检查（SSRF 防护）

防止通过 web_fetch / MCP 等工具访问内部网络：
- 阻止 private IP（10.x, 172.16-31.x, 192.168.x, 127.x, ::1, fd00::）
- 阻止 link-local（169.254.x, fe80::）
- 阻止 CGNAT（100.64-127.x）
- 阻止云元数据端点（169.254.169.254, metadata.google.internal 等）
- DNS 解析后二次检查（防止 DNS rebinding）
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import ParseResult, urlparse

logger = logging.getLogger(__name__)

# ──────────────────────── 通用安全解析 ────────────────────────
#
# Python 3.11+ 的 urlparse / urlsplit 在遇到格式错误的 IPv6 URL 时
# 会抛出 ValueError，而之前的版本默默返回空结果。代码库中存在大量
# urlparse 调用处理不可信的用户输入（消息文本、工具参数、配置等），
# 需要统一保护，避免散弹枪式地在每个调用点写 try-except。

_EMPTY_PARSE_RESULT = ParseResult(scheme="", netloc="", path="", params="", query="", fragment="")


def safe_urlparse(url: str) -> ParseResult:
    """urlparse wrapper that never raises on malformed input.

    Returns an empty ParseResult instead of raising ValueError for
    invalid IPv6 URLs (Python 3.11+) or other malformed strings.
    """
    try:
        return urlparse(url)
    except (ValueError, TypeError):
        return _EMPTY_PARSE_RESULT


_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.internal",
    }
)

_METADATA_IPS = frozenset(
    {
        "169.254.169.254",
        "169.254.170.2",
    }
)

_PROXY_INTERCEPT_NET = ipaddress.ip_network("198.18.0.0/15")


def _blocked_ip_reason(ip_str: str) -> str:
    """Return the blocked-IP category, or an empty string when allowed."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return "invalid IP address"

    if isinstance(addr, ipaddress.IPv4Address) and addr in _PROXY_INTERCEPT_NET:
        return (
            "reserved benchmark range 198.18.0.0/15, often caused by proxy/TUN/DNS "
            "interception"
        )

    if addr.is_loopback:
        return "loopback address"
    if addr.is_private:
        return "private address"
    if addr.is_link_local:
        return "link-local address"
    if addr.is_reserved:
        return "reserved address"
    if addr.is_multicast:
        return "multicast address"

    if isinstance(addr, ipaddress.IPv4Address):
        first_octet = int(ip_str.split(".")[0])
        second_octet = int(ip_str.split(".")[1]) if "." in ip_str else 0
        if first_octet == 100 and 64 <= second_octet <= 127:
            return "CGNAT address"

    if ip_str in _METADATA_IPS:
        return "cloud metadata endpoint"

    return ""


def _is_blocked_ip(ip_str: str) -> bool:
    """Check if IP address belongs to a blocked range."""
    return bool(_blocked_ip_reason(ip_str))


def _resolve_and_check(hostname: str) -> tuple[bool, str]:
    """Synchronous DNS resolution + IP check (runs in thread pool)."""
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _, _, _, sockaddr in results:
            ip_str = sockaddr[0]
            reason = _blocked_ip_reason(ip_str)
            if reason:
                return False, f"DNS resolved to blocked IP: {hostname} → {ip_str} ({reason})"
    except socket.gaierror:
        return False, f"DNS resolution failed: {hostname}"
    return True, ""


def _check_url_pre_dns(url: str) -> tuple[bool, str, str]:
    """Fast pre-DNS checks. Returns (pass, reason, hostname)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL format", ""

    if not parsed.scheme or parsed.scheme not in ("http", "https"):
        return False, f"Blocked scheme: {parsed.scheme or '(empty)'}", ""

    hostname = parsed.hostname
    if not hostname:
        return False, "Missing hostname", ""

    hostname_lower = hostname.lower()
    if hostname_lower in _BLOCKED_HOSTNAMES:
        return False, f"Blocked hostname: {hostname}", ""

    try:
        addr = ipaddress.ip_address(hostname)
        if _is_blocked_ip(str(addr)):
            return False, f"Blocked IP: {hostname}", ""
    except ValueError:
        pass

    return True, "", hostname


async def is_safe_url(url: str) -> tuple[bool, str]:
    """
    Validate a URL is safe from SSRF attacks.

    DNS resolution is offloaded to a thread pool to avoid blocking
    the event loop.

    Returns:
        (is_safe, reason) - reason is empty string if safe
    """
    ok, reason, hostname = _check_url_pre_dns(url)
    if not ok:
        return False, reason

    return await asyncio.to_thread(_resolve_and_check, hostname)


def is_safe_url_sync(url: str) -> tuple[bool, str]:
    """Synchronous variant for non-async callers."""
    ok, reason, hostname = _check_url_pre_dns(url)
    if not ok:
        return False, reason
    return _resolve_and_check(hostname)
