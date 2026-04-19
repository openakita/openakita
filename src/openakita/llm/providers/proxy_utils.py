"""
Proxy and network configuration utilities

Get proxy settings and IPv4 force configuration from environment variables or configuration.
"""

import logging
import os
import socket
import time

import httpx

logger = logging.getLogger(__name__)

# Cache: avoid duplicate log printing
_ipv4_logged = False
_proxy_logged = False
_transport_cache: httpx.AsyncHTTPTransport | None = None

# Proxy reachability cache: (proxy_url, reachable, timestamp)
_proxy_reachable_cache: tuple[str, bool, float] | None = None
_PROXY_CHECK_TTL = 30.0  # Cache for 30 seconds


def _is_truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y", "on")


def is_proxy_disabled() -> bool:
    """Whether proxy is disabled

    Used to troubleshoot "no proxy configured but all endpoints timeout" situations:
    Some Windows environments globally inject HTTP(S)_PROXY/ALL_PROXY, forcing requests through proxy.

    Supported switches (any true disables proxy):
    - LLM_DISABLE_PROXY=1
    - OPENAKITA_DISABLE_PROXY=1
    - DISABLE_PROXY=1
    """
    return (
        _is_truthy_env("LLM_DISABLE_PROXY")
        or _is_truthy_env("OPENAKITA_DISABLE_PROXY")
        or _is_truthy_env("DISABLE_PROXY")
    )


def _redact_proxy_url(proxy: str) -> str:
    """Redact proxy URL (prevent credential leaks in logs)"""
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(proxy)
        if parts.username or parts.password:
            # Build netloc: ***:***@host:port
            host = parts.hostname or ""
            port = f":{parts.port}" if parts.port else ""
            netloc = f"***:***@{host}{port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        return proxy
    except Exception:
        return proxy


def build_httpx_timeout(timeout_value: object, default: float = 60.0) -> httpx.Timeout:
    """Construct httpx.Timeout from configuration

    Compatible with:
    - int/float: treated as "read timeout" (overall limit), with smaller reasonable defaults for connect/write/pool
    - dict: supports fields connect/read/write/pool/total (seconds)
    """

    def _to_float_or_none(v: object) -> float | None:
        if v is None:
            return None
        if isinstance(v, str) and v.strip().lower() in (
            "none",
            "null",
            "off",
            "disable",
            "disabled",
        ):
            return None
        try:
            return float(v)  # type: ignore[arg-type]
        except Exception:
            return None

    # dict form: {"connect":10,"read":300,"write":30,"pool":30,"total":300}
    if isinstance(timeout_value, dict):
        total = _to_float_or_none(timeout_value.get("total"))  # type: ignore[union-attr]
        connect = _to_float_or_none(timeout_value.get("connect"))  # type: ignore[union-attr]
        read = _to_float_or_none(timeout_value.get("read"))  # type: ignore[union-attr]
        write = _to_float_or_none(timeout_value.get("write"))  # type: ignore[union-attr]
        pool = _to_float_or_none(timeout_value.get("pool"))  # type: ignore[union-attr]

        kwargs: dict = {}
        if total is not None:
            kwargs["timeout"] = total
        if connect is not None:
            kwargs["connect"] = connect
        if read is not None:
            kwargs["read"] = read
        if write is not None:
            kwargs["write"] = write
        if pool is not None:
            kwargs["pool"] = pool

        # If dict has no valid fields, fall back to default
        if not kwargs:
            return httpx.Timeout(default)
        return httpx.Timeout(**kwargs)

    # Numeric form: by default set read to t, set connect/write/pool to smaller values to avoid "connection phase maxed out at t"
    try:
        t = float(timeout_value)  # type: ignore[arg-type]
    except Exception:
        t = float(default)

    t = max(1.0, t)
    return httpx.Timeout(
        connect=min(10.0, t),
        read=t,
        write=min(30.0, t),
        pool=min(30.0, t),
    )


def _check_proxy_reachable(proxy_url: str, timeout: float = 2.0) -> bool:
    """Check if proxy is reachable (TCP connection test)

    Args:
        proxy_url: Proxy address, e.g. socks5://127.0.0.1:7897 or http://proxy:8080
        timeout: Connection timeout (seconds)

    Returns:
        True if reachable, False if not
    """
    global _proxy_reachable_cache

    # Cache hit
    if _proxy_reachable_cache:
        cached_url, cached_result, cached_time = _proxy_reachable_cache
        if cached_url == proxy_url and (time.monotonic() - cached_time) < _PROXY_CHECK_TTL:
            return cached_result

    try:
        from urllib.parse import urlsplit

        parts = urlsplit(proxy_url)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or (1080 if "socks" in (parts.scheme or "") else 8080)

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            _proxy_reachable_cache = (proxy_url, True, time.monotonic())
            return True
        except (OSError, TimeoutError):
            _proxy_reachable_cache = (proxy_url, False, time.monotonic())
            return False
        finally:
            sock.close()
    except Exception:
        _proxy_reachable_cache = (proxy_url, False, time.monotonic())
        return False


def _detect_proxy_source() -> tuple[str, str] | None:
    """Detect proxy configuration source (no reachability check)

    Returns:
        (proxy_url, source_description) or None
    """
    for env_var in [
        "ALL_PROXY",
        "all_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ]:
        proxy = (os.environ.get(env_var) or "").strip()
        if proxy:
            return proxy, f"env {env_var}"

    try:
        from ...config import settings

        for key, val in [
            ("all_proxy", settings.all_proxy),
            ("https_proxy", settings.https_proxy),
            ("http_proxy", settings.http_proxy),
        ]:
            if val and (v := (val or "").strip()):
                return v, f"config {key}"
    except Exception:
        pass

    return None


def get_proxy_config() -> str | None:
    """Get proxy configuration (with reachability verification)

    Priority (high to low):
    1. ALL_PROXY environment variable
    2. HTTPS_PROXY environment variable
    3. HTTP_PROXY environment variable
    4. all_proxy in config file
    5. https_proxy in config file
    6. http_proxy in config file

    Automatically fall back to direct connection when proxy is unreachable, avoiding request failures from leftover Clash/V2Ray configurations.

    Returns:
        Proxy address or None
    """
    global _proxy_logged

    if is_proxy_disabled():
        if not _proxy_logged:
            logger.info("[Proxy] Proxy disabled (LLM_DISABLE_PROXY=1)")
            _proxy_logged = True
        return None

    detected = _detect_proxy_source()
    if not detected:
        return None

    proxy, source = detected

    if not _check_proxy_reachable(proxy):
        logger.warning(
            f"[Proxy] Detected proxy from {source}: {_redact_proxy_url(proxy)}, "
            f"but it is UNREACHABLE (connection refused). Falling back to direct connection. "
            f"If you are not using a proxy, clear the proxy setting or set DISABLE_PROXY=1. "
            f"If you need the proxy, please start your proxy software."
        )
        return None

    if not _proxy_logged:
        logger.info(f"[Proxy] LLM proxy enabled from {source}: {_redact_proxy_url(proxy)}")
        _proxy_logged = True
    return proxy


def is_ipv4_only() -> bool:
    """Check if IPv4-only mode is forced

    Enabled via environment variable FORCE_IPV4=true or config file force_ipv4=true
    """
    # Check environment variable
    if os.environ.get("FORCE_IPV4", "").lower() in ("true", "1", "yes"):
        return True

    # Check config file
    try:
        from ...config import settings

        return getattr(settings, "force_ipv4", False)
    except Exception:
        pass

    return False


def get_httpx_transport() -> httpx.AsyncHTTPTransport | None:
    """Get httpx transport (supports IPv4-only mode)

    When FORCE_IPV4=true, create transport that forces IPv4 use.
    Useful for VPNs (like LetsTAP) that don't support IPv6.

    Returns:
        httpx.AsyncHTTPTransport or None
    """
    global _ipv4_logged

    if is_ipv4_only():
        # Only log on first time
        if not _ipv4_logged:
            logger.info("[Network] IPv4-only mode enabled (FORCE_IPV4=true)")
            _ipv4_logged = True
        # local_address="0.0.0.0" forces IPv4 use
        return httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    return None


def get_httpx_proxy_mounts() -> dict | None:
    """Get httpx proxy configuration

    Returns:
        httpx proxy mounts dict or None
    """
    proxy = get_proxy_config()
    if proxy:
        return {
            "http://": proxy,
            "https://": proxy,
        }
    return None


def get_httpx_client_kwargs(*, timeout: float = 30.0, is_local: bool = False) -> dict:
    """Get common httpx.AsyncClient kwargs

    Unified handling of proxy, trust_env, timeout, transport configuration.
    Always set trust_env=False to avoid request failures from leftover system proxy on macOS/Windows.
    Includes IPv4-only transport (when FORCE_IPV4=true), consistent with LLM client behavior.

    Args:
        timeout: Request timeout (seconds)
        is_local: Whether endpoint is local (local endpoints don't use proxy)
    """
    kwargs: dict = {
        "timeout": timeout,
        "trust_env": False,
    }

    if not is_local:
        proxy = get_proxy_config()
        if proxy:
            kwargs["proxy"] = proxy

    transport = get_httpx_transport()
    if transport:
        kwargs["transport"] = transport

    return kwargs


def extract_connection_error(exc: BaseException, max_depth: int = 5) -> str:
    """Traverse exception chain and extract underlying error message.

    httpx's ConnectError often wraps real OSError/SSL errors,
    str(e) only gets empty string. This function goes to chain bottom to extract useful info.
    Also checks __cause__ (explicit chain) and __context__ (implicit chain).

    Design reference: claude-code errorUtils.ts extractConnectionErrorDetails()
    """
    best: str = ""
    current: BaseException | None = exc
    visited: set[int] = set()
    depth = 0
    while current and depth < max_depth:
        cid = id(current)
        if cid in visited:
            break
        visited.add(cid)
        if isinstance(current, OSError) and (current.strerror or current.args):
            return f"{type(current).__name__}: {current}"
        s = str(current)
        if s and not best:
            best = f"{type(current).__name__}: {s}"
        cause = getattr(current, "__cause__", None)
        if cause is None or cause is current:
            cause = getattr(current, "__context__", None)
        if cause is None or cause is current:
            break
        current = cause
        depth += 1
    if best:
        return best
    return type(exc).__name__


def format_proxy_hint() -> str:
    """Generate proxy diagnostic hint (for error messages)

    When user already disabled proxy via DISABLE_PROXY=1, don't return hint to avoid misleading.
    """
    if is_proxy_disabled():
        return ""

    detected = _detect_proxy_source()
    if not detected:
        return ""

    proxy, source = detected
    reachable = _check_proxy_reachable(proxy)
    status = "reachable" if reachable else "unreachable"
    return (
        f"\n[Proxy Diagnosis] Detected proxy {_redact_proxy_url(proxy)} (source: {source}), "
        f"status: {status}. "
        f"{'If you are not using a proxy, clear the corresponding environment variable or set DISABLE_PROXY=1' if not reachable else ''}"
    )
