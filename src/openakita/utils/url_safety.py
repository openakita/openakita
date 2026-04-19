"""
URL Safety Checks (SSRF Protection)

Prevent access to internal networks via web_fetch / MCP and similar tools:
- Block private IPs (10.x, 172.16-31.x, 192.168.x, 127.x, ::1, fd00::)
- Block link-local addresses (169.254.x, fe80::)
- Block CGNAT range (100.64-127.x)
- Block cloud metadata endpoints (169.254.169.254, metadata.google.internal, etc.)
- Re-check after DNS resolution (prevents DNS rebinding)
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata.internal",
})

_METADATA_IPS = frozenset({
    "169.254.169.254",
    "169.254.170.2",
})


def _is_blocked_ip(ip_str: str) -> bool:
    """Check if IP address belongs to a blocked range."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True

    if addr.is_loopback:
        return True
    if addr.is_private:
        return True
    if addr.is_link_local:
        return True
    if addr.is_reserved:
        return True
    if addr.is_multicast:
        return True

    if isinstance(addr, ipaddress.IPv4Address):
        first_octet = int(ip_str.split(".")[0])
        second_octet = int(ip_str.split(".")[1]) if "." in ip_str else 0
        if first_octet == 100 and 64 <= second_octet <= 127:
            return True

    if ip_str in _METADATA_IPS:
        return True

    return False


def _resolve_and_check(hostname: str) -> tuple[bool, str]:
    """Synchronous DNS resolution + IP check (runs in thread pool)."""
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _, _, _, sockaddr in results:
            ip_str = sockaddr[0]
            if _is_blocked_ip(ip_str):
                return False, f"DNS resolved to blocked IP: {hostname} → {ip_str}"
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
