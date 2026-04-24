"""
代理和网络配置工具

从环境变量或配置中获取代理设置，以及 IPv4 强制配置。
"""

import ipaddress
import logging
import os
import socket
import time
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

# 缓存：避免重复打印日志
_ipv4_logged = False
_proxy_logged = False
_transport_cache: httpx.AsyncHTTPTransport | None = None

# 代理可达性缓存：(proxy_url, reachable, timestamp)
_proxy_reachable_cache: tuple[str, bool, float] | None = None
_PROXY_CHECK_TTL = 30.0  # 缓存 30 秒


def _is_truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y", "on")


def is_proxy_disabled() -> bool:
    """是否禁用代理

    用于排查“明明没配代理但所有端点都超时”的情况：
    某些 Windows 环境会全局注入 HTTP(S)_PROXY/ALL_PROXY，导致请求被强制走代理。

    支持的开关（任一为真即禁用）：
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
    """脱敏 proxy URL（避免日志泄露账号密码）"""
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(proxy)
        if parts.username or parts.password:
            # 组装 netloc：***:***@host:port
            host = parts.hostname or ""
            port = f":{parts.port}" if parts.port else ""
            netloc = f"***:***@{host}{port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        return proxy
    except Exception:
        return proxy


def build_httpx_timeout(timeout_value: object, default: float = 60.0) -> httpx.Timeout:
    """从配置构造 httpx.Timeout

    兼容：
    - int/float：视作“读超时”（整体上限），并给 connect/write/pool 合理的更小默认值
    - dict：支持字段 connect/read/write/pool/total（秒）
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

    # dict 形式：{"connect":10,"read":300,"write":30,"pool":30,"total":300}
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

        # 若 dict 无有效字段，回退到默认
        if not kwargs:
            return httpx.Timeout(default)
        return httpx.Timeout(**kwargs)

    # 数值形式：默认将 read 设为 t，connect/write/pool 设为较小值，避免“连接阶段卡满 t”
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
    """检测代理是否可达（TCP 连接测试）

    Args:
        proxy_url: 代理地址，如 socks5://127.0.0.1:7897 或 http://proxy:8080
        timeout: 连接超时（秒）

    Returns:
        True 表示可达，False 表示不可达
    """
    global _proxy_reachable_cache

    # 缓存命中
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
    """检测代理配置来源（不做可达性检查）

    Returns:
        (proxy_url, source_description) 或 None
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
    """获取代理配置（带可达性验证）

    优先级（从高到低）:
    1. ALL_PROXY 环境变量
    2. HTTPS_PROXY 环境变量
    3. HTTP_PROXY 环境变量
    4. 配置文件中的 all_proxy
    5. 配置文件中的 https_proxy
    6. 配置文件中的 http_proxy

    当代理不可达时自动降级为直连，避免 Clash/V2Ray 等残留配置导致所有请求失败。

    Returns:
        代理地址或 None
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


# ---------------------------------------------------------------------------
# Proxy bypass — 判断目标地址是否应跳过代理（直连）
# ---------------------------------------------------------------------------


def _is_private_host(hostname: str) -> bool:
    """判断 hostname 是否为私有 / 内网地址。

    覆盖：loopback、RFC 1918 私有、link-local、IPv6 ULA、.local 域名。
    """
    if not hostname:
        return False

    lower = hostname.lower()
    if lower in ("localhost", "localhost.localdomain"):
        return True
    if lower.endswith(".local"):
        return True

    try:
        addr = ipaddress.ip_address(lower)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
    except ValueError:
        return False


def _get_no_proxy_value() -> str:
    """获取 NO_PROXY 配置值（环境变量 + 配置文件合并）。"""
    parts: list[str] = []
    for env_var in ("OPENAKITA_NO_PROXY", "NO_PROXY", "no_proxy"):
        val = (os.environ.get(env_var) or "").strip()
        if val:
            parts.append(val)
            break

    try:
        from ...config import settings

        cfg_val = (getattr(settings, "no_proxy", "") or "").strip()
        if cfg_val:
            parts.append(cfg_val)
    except Exception:
        pass

    return ",".join(parts)


def _matches_no_proxy(hostname: str, no_proxy: str) -> bool:
    """检查 hostname 是否匹配 NO_PROXY 列表中的任一模式。

    支持：精确匹配、域名后缀(.example.com)、CIDR(10.0.0.0/8)、通配符(*)。
    """
    if not no_proxy:
        return False
    lower = hostname.lower()
    for pattern in no_proxy.split(","):
        pattern = pattern.strip()
        if not pattern:
            continue
        if pattern == "*":
            return True
        if "/" in pattern:
            try:
                network = ipaddress.ip_network(pattern, strict=False)
                if ipaddress.ip_address(lower) in network:
                    return True
            except ValueError:
                pass
            continue
        p_lower = pattern.lower()
        if p_lower.startswith("."):
            if lower.endswith(p_lower) or lower == p_lower[1:]:
                return True
            continue
        if lower == p_lower:
            return True
        try:
            if ipaddress.ip_address(lower) == ipaddress.ip_address(p_lower):
                return True
        except ValueError:
            pass
    return False


def should_bypass_proxy(target_url: str) -> bool:
    """判断目标 URL 是否应跳过代理。

    依据三层规则（任一命中即跳过）：
    1. 目标地址是私有 / 内网地址（自动）
    2. 匹配 NO_PROXY / OPENAKITA_NO_PROXY 环境变量
    3. 匹配配置文件 no_proxy

    Args:
        target_url: 完整目标 URL，如 "http://172.14.0.88/v1"
    """
    try:
        host = urlsplit(target_url).hostname
    except Exception:
        return False
    if not host:
        return False
    if _is_private_host(host):
        return True
    no_proxy = _get_no_proxy_value()
    if no_proxy and _matches_no_proxy(host, no_proxy):
        return True
    return False


def is_ipv4_only() -> bool:
    """检查是否强制使用 IPv4

    通过环境变量 FORCE_IPV4=true 或配置文件 force_ipv4=true 启用
    """
    # 检查环境变量
    if os.environ.get("FORCE_IPV4", "").lower() in ("true", "1", "yes"):
        return True

    # 检查配置文件
    try:
        from ...config import settings

        return getattr(settings, "force_ipv4", False)
    except Exception:
        pass

    return False


def get_httpx_transport() -> httpx.AsyncHTTPTransport | None:
    """获取 httpx transport（支持 IPv4-only 模式）

    当 FORCE_IPV4=true 时，创建强制使用 IPv4 的 transport。
    这对于某些 VPN（如 LetsTAP）不支持 IPv6 的情况很有用。

    Returns:
        httpx.AsyncHTTPTransport 或 None
    """
    global _ipv4_logged

    if is_ipv4_only():
        # 只在第一次打印日志
        if not _ipv4_logged:
            logger.info("[Network] IPv4-only mode enabled (FORCE_IPV4=true)")
            _ipv4_logged = True
        # local_address="0.0.0.0" 强制使用 IPv4
        return httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    return None


def get_httpx_proxy_mounts() -> dict | None:
    """获取 httpx 代理配置

    Returns:
        httpx 代理 mounts 字典或 None
    """
    proxy = get_proxy_config()
    if proxy:
        return {
            "http://": proxy,
            "https://": proxy,
        }
    return None


def get_httpx_client_kwargs(
    *,
    timeout: float = 30.0,
    is_local: bool = False,
    target_url: str = "",
) -> dict:
    """获取 httpx.AsyncClient 通用 kwargs

    统一处理代理、trust_env、超时、transport 等配置。
    始终设置 trust_env=False，避免 macOS/Windows 残留系统代理导致请求失败。
    包含 IPv4-only transport（FORCE_IPV4=true 时），与 LLM 客户端行为一致。

    Args:
        timeout: 请求超时（秒）
        is_local: 是否为本地端点（向后兼容；推荐改用 target_url 自动判断）
        target_url: 目标 URL — 命中私有地址 / NO_PROXY / .local 时自动跳过代理
    """
    kwargs: dict = {
        "timeout": timeout,
        "trust_env": False,
    }

    skip_proxy = is_local or (bool(target_url) and should_bypass_proxy(target_url))
    if not skip_proxy:
        proxy = get_proxy_config()
        if proxy:
            kwargs["proxy"] = proxy

    transport = get_httpx_transport()
    if transport:
        kwargs["transport"] = transport

    return kwargs


def extract_connection_error(exc: BaseException, max_depth: int = 5) -> str:
    """遍历异常链，提取底层错误信息。

    httpx 的 ConnectError 经常包装了真正的 OSError/SSL 错误，
    直接 str(e) 只得到空字符串。此函数走到链底提取有用信息。
    同时检查 __cause__（显式链）和 __context__（隐式链）。

    设计参考: claude-code errorUtils.ts extractConnectionErrorDetails()
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
    """生成代理诊断提示（用于错误信息）

    当用户已通过 DISABLE_PROXY=1 禁用代理时，不返回提示，避免误导。
    """
    if is_proxy_disabled():
        return ""

    detected = _detect_proxy_source()
    if not detected:
        return ""

    proxy, source = detected
    reachable = _check_proxy_reachable(proxy)
    status = "可达" if reachable else "不可达"
    if reachable:
        no_proxy_hint = (
            "如果目标是内网地址，请在设置中心「网络」→「代理例外」中添加该地址，"
            "或设置 NO_PROXY 环境变量。"
        )
    else:
        no_proxy_hint = "如果您未使用代理，请清除对应环境变量或设置 DISABLE_PROXY=1。"
    return (
        f"\n[代理诊断] 检测到代理 {_redact_proxy_url(proxy)} (来源: {source}), "
        f"状态: {status}。{no_proxy_hint}"
    )
