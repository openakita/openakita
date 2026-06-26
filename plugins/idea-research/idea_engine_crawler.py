"""Engine B — Playwright crawlers + ``CookiesVault`` (§6.2 + §6.3).

Why "vendored cryptography path" is OK: the entire module is opt-in
(advanced crawler mode), and the imports for ``cryptography`` /
``keyring`` / ``playwright`` are *lazy* — when those packages are
missing we raise a clean ``VendorError(error_kind='dependency')`` with
the exact ``pip install`` hint instead of crashing on plugin load.

Public surface
--------------
* ``CookiesVault``     — Fernet-encrypted cookies store, keyring-backed
                         master key, sqlite plain-text fallback w/ warn.
* ``PlaywrightDriver`` — single chromium pool (max 2 concurrent pages).
* Five concrete crawlers (``DouyinCrawler`` / ``XhsCrawler`` /
  ``KsCrawler`` / ``BiliLoggedCrawler`` / ``WeiboCrawler``) each
  exposing ``async def fetch_trending(keywords, time_window, limit)``
  and ``async def fetch_user(url, max_videos)`` returning
  ``list[TrendItem]``.

Tests in ``tests/test_collectors_engine_b.py`` swap the Playwright
driver for a fake page object so we never hit a real chromium runtime.
"""

from __future__ import annotations

import asyncio
import ast
import contextlib
import html as html_lib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from idea_models import TrendItem
from idea_research_inline.vendor_client import (
    VendorAuthError,
    VendorError,
    VendorFormatError,
    VendorNetworkError,
    VendorTimeoutError,
)

_CRAWLER_BLOCK_KEYWORDS = (
    "captcha",
    "verify code",
    "verification code",
    "slider verify",
    "security verification",
    "请输入验证码",
    "访问异常",
    "需要登录",
    "安全验证",
    "robot check",
)
PLATFORM_COOKIES_REQUIRED: dict[str, tuple[str, ...]] = {
    "douyin": ("sessionid_ss", "s_v_web_id", "ttwid"),
    "xhs": ("web_session", "xsecappid", "a1"),
    "ks": (),
    "bilibili": ("SESSDATA", "bili_jct", "DedeUserID"),
    "weibo": ("SUB", "SUBP"),
}

_COOKIE_METADATA_KEYS = {
    "domain",
    "expirationDate",
    "expires",
    "hostOnly",
    "httpOnly",
    "name",
    "path",
    "sameSite",
    "secure",
    "session",
    "storeId",
    "url",
    "value",
}
_INVALID_COOKIE_NAME_RE = re.compile(r"[\x00-\x20\x7f()<>@,;:\\\"/\[\]?={}]")
_INVALID_COOKIE_VALUE_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f;]")


def _now() -> int:
    return int(time.time())


def _new_item_id() -> str:
    return str(uuid.uuid4())


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_playwright_timeout(exc: BaseException) -> bool:
    return type(exc).__name__ == "TimeoutError"


def _time_window_seconds(value: str) -> int | None:
    clean = str(value or "").strip().lower()
    mapping = {
        "24h": 24 * 3600,
        "7d": 7 * 24 * 3600,
        "30d": 30 * 24 * 3600,
    }
    return mapping.get(clean)


# --------------------------------------------------------------------------- #
# CookiesVault                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class CookiesEntry:
    platform: str
    cookies: dict[str, str]
    expires_at: int | None = None
    updated_at: int = 0


def missing_cookies_keys(platform: str, cookies: dict[str, str]) -> list[str]:
    """Return required cookie names that are absent or empty for *platform*."""

    required = PLATFORM_COOKIES_REQUIRED.get(platform, ())
    return [k for k in required if not str(cookies.get(k) or "").strip()]


def _normalize_cookie_map(cookies: Any) -> dict[str, str]:
    out: dict[str, str] = {}

    def add(raw_name: Any, raw_value: Any) -> None:
        name = _clean_cookie_name(raw_name)
        value = _clean_cookie_value(raw_value)
        if not name or value is None:
            return
        out[name] = value

    if isinstance(cookies, str):
        parsed = _parse_cookie_export_string(cookies)
        if parsed is not None:
            return _normalize_cookie_map(parsed)
        for part in cookies.split(";"):
            if "=" not in part:
                continue
            raw_name, raw_value = part.split("=", 1)
            add(raw_name, raw_value)
        return out

    if isinstance(cookies, list):
        for item in cookies:
            if not isinstance(item, dict):
                continue
            if "name" in item and "value" in item:
                add(item.get("name"), item.get("value"))
        return out

    if not isinstance(cookies, dict):
        return out

    nested = cookies.get("cookies")
    if isinstance(nested, (list, dict, str)):
        return _normalize_cookie_map(nested)

    if "name" in cookies and "value" in cookies:
        add(cookies.get("name"), cookies.get("value"))
        return out

    for raw_name, raw_value in cookies.items():
        if isinstance(raw_value, dict) and "value" in raw_value:
            add(raw_value.get("name") or raw_name, raw_value.get("value"))
            continue
        if isinstance(raw_value, str) and raw_value.strip().startswith(("{", "[")):
            parsed_value = _parse_cookie_export_string(raw_value)
            if parsed_value is not None:
                out.update(_normalize_cookie_map(parsed_value))
                continue
        if isinstance(raw_value, (list, dict)):
            continue
        if str(raw_name) in _COOKIE_METADATA_KEYS:
            continue
        add(raw_name, raw_value)
    return out


def _parse_cookie_export_string(value: str) -> Any | None:
    stripped = value.strip()
    if not stripped.startswith(("[", "{")):
        return None
    with contextlib.suppress(Exception):
        return json.loads(stripped)
    with contextlib.suppress(Exception):
        return ast.literal_eval(stripped)
    return None


def _strip_cookie_paste(text: str) -> str:
    """Remove markdown code fences and outer whitespace from pasted cookies."""

    s = str(text or "").strip()
    if not s.startswith("```"):
        return s
    lines = s.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_cookies_upload_text(text: str) -> Any:
    """Parse cookies pasted in Settings (JSON, Cookie-Editor array, or header string)."""

    raw = _strip_cookie_paste(text)
    if not raw:
        raise ValueError(
            "cookies 内容为空；请粘贴 Cookie-Editor 导出的 JSON，或 name=value 格式的 Cookie 字符串"
        )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        parsed = _parse_cookie_export_string(raw)
        if parsed is not None:
            return parsed
        if "=" in raw:
            return raw
        raise ValueError(
            "无法识别 cookies 格式。请粘贴 Cookie-Editor 导出的 JSON（对象或数组），"
            "或含 name=value 的 Cookie 字符串（如 document.cookie 或 F12 Application 复制项）。"
        ) from exc


def _clean_cookie_name(raw_name: Any) -> str:
    name = str(raw_name or "").strip()
    if not name or _INVALID_COOKIE_NAME_RE.search(name):
        return ""
    return name


def _clean_cookie_value(raw_value: Any) -> str | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        value = raw_value
    elif isinstance(raw_value, bool):
        value = "true" if raw_value else "false"
    elif isinstance(raw_value, (dict, list)):
        return None
    else:
        value = str(raw_value)
    value = value.strip()
    if not value or _INVALID_COOKIE_VALUE_RE.search(value):
        return None
    return value


def _playwright_cookie_list(cookies: dict[str, str], domain: str) -> list[dict[str, Any]]:
    clean = _normalize_cookie_map(cookies)
    return [
        {
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
        }
        for name, value in clean.items()
    ]


def _visible_html_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript)\b[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _extract_embedded_json_payloads(html: str) -> list[dict[str, Any]]:
    if not html:
        return []
    payloads: list[dict[str, Any]] = []
    seen: set[str] = set()
    patterns = (
        r'(?is)<script[^>]+id="__NEXT_DATA__"[^>]*>\s*(\{.*?\})\s*</script>',
        r'(?is)<script[^>]+type="application/json"[^>]*>\s*(\{.*?\})\s*</script>',
        r"(?is)window\.__[A-Z0-9_]+__\s*=\s*(\{.*?\})\s*;",
    )
    for pattern in patterns:
        for raw in re.findall(pattern, html):
            text = html_lib.unescape(raw).strip()
            if not text or text in seen:
                continue
            with contextlib.suppress(Exception):
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    seen.add(text)
                    payloads.append(parsed)
    return payloads


class CookiesVault:
    """Encrypted cookies store with sqlite fallback (§6.3).

    The master key lives in the OS keyring (service =
    ``openakita-idea-research``, account = ``cookies-master``). When the
    keyring is unavailable (Linux without dbus / sandboxed CI / user
    refused) we fall back to plain bytes and surface ``encrypted=False``
    so the UI can show a yellow warn.
    """

    KEYRING_SERVICE = "openakita-idea-research"
    KEYRING_KEY = "cookies-master"
    SCHEMA_SQL = (
        "CREATE TABLE IF NOT EXISTS cookies_vault ("
        " platform TEXT PRIMARY KEY,"
        " encrypted INTEGER NOT NULL,"
        " payload BLOB NOT NULL,"
        " expires_at INTEGER,"
        " updated_at INTEGER NOT NULL"
        ")"
    )

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._lock = asyncio.Lock()
        self._fernet: Any = None
        self._encryption_ready: bool | None = None
        self._warn_messages: list[str] = []

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def warn_messages(self) -> list[str]:
        return list(self._warn_messages)

    @property
    def encryption_ready(self) -> bool:
        if self._encryption_ready is None:
            self._init_crypto()
        return bool(self._encryption_ready)

    def refresh_crypto_status(self) -> bool:
        """Re-probe optional crypto deps after in-plugin installation."""

        self._warn_messages.clear()
        self._fernet = None
        self._encryption_ready = None
        return self.encryption_ready

    # ---- crypto bootstrap --------------------------------------------------

    def _init_crypto(self) -> None:
        try:
            from cryptography.fernet import Fernet
        except Exception as exc:  # pragma: no cover — exercised via tests
            self._warn(
                "cryptography 未安装，cookies 将以明文 sqlite 存储；建议 "
                f"`pip install cryptography keyring`（{exc}）"
            )
            self._encryption_ready = False
            return
        key = self._load_or_create_master_key()
        if not key:
            self._encryption_ready = False
            return
        try:
            self._fernet = Fernet(key)
            self._encryption_ready = True
        except Exception as exc:
            self._warn(f"Fernet 初始化失败：{exc}")
            self._encryption_ready = False

    def _load_or_create_master_key(self) -> bytes | None:
        try:
            import keyring
        except Exception as exc:  # pragma: no cover
            self._warn(
                "keyring 未安装，主密钥将持久化到 sqlite plain；"
                f"建议 `pip install keyring`（{exc}）"
            )
            return self._fallback_master_key()
        try:
            existing = keyring.get_password(self.KEYRING_SERVICE, self.KEYRING_KEY)
        except Exception as exc:
            self._warn(f"读取系统 keyring 失败：{exc}")
            return self._fallback_master_key()
        if existing:
            return existing.encode()
        try:
            from cryptography.fernet import Fernet

            new_key = Fernet.generate_key()
            keyring.set_password(self.KEYRING_SERVICE, self.KEYRING_KEY, new_key.decode())
            return new_key
        except Exception as exc:
            self._warn(f"写入系统 keyring 失败：{exc}")
            return self._fallback_master_key()

    def _fallback_master_key(self) -> bytes | None:
        # Last resort: store the master key in a sibling file with chmod 600.
        try:
            from cryptography.fernet import Fernet
        except Exception:
            return None
        path = self._db_path.parent / ".idea_research_master.key"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return path.read_bytes().strip()
        new_key = Fernet.generate_key()
        path.write_bytes(new_key)
        with contextlib.suppress(Exception):
            path.chmod(0o600)
        self._warn(f"主密钥落盘到 {path}（keyring 不可用），请确保该文件不被备份/泄漏")
        return new_key

    def _warn(self, msg: str) -> None:
        if msg and msg not in self._warn_messages:
            self._warn_messages.append(msg)

    # ---- sqlite plumbing ---------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(self.SCHEMA_SQL)
        return conn

    async def _run(self, fn: Callable[..., Any], *args: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    # ---- public API --------------------------------------------------------

    async def save(
        self, platform: str, cookies: Any, *, expires_at: int | None = None
    ) -> bool:
        return await self._run(self._save_sync, platform, cookies, expires_at)

    def _save_sync(
        self,
        platform: str,
        cookies: Any,
        expires_at: int | None,
    ) -> bool:
        encrypted_flag = 1 if self.encryption_ready else 0
        payload_json = json.dumps(
            _normalize_cookie_map(cookies),
            ensure_ascii=False,
        ).encode("utf-8")
        if encrypted_flag and self._fernet is not None:
            payload = self._fernet.encrypt(payload_json)
        else:
            payload = payload_json
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO cookies_vault (platform, encrypted, payload,"
                " expires_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(platform) DO UPDATE SET"
                "   encrypted=excluded.encrypted,"
                "   payload=excluded.payload,"
                "   expires_at=excluded.expires_at,"
                "   updated_at=excluded.updated_at",
                (platform, encrypted_flag, payload, expires_at, _now()),
            )
        return bool(encrypted_flag)

    async def load(self, platform: str) -> CookiesEntry | None:
        return await self._run(self._load_sync, platform)

    def _load_sync(self, platform: str) -> CookiesEntry | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT platform, encrypted, payload, expires_at, updated_at"
                " FROM cookies_vault WHERE platform = ?",
                (platform,),
            ).fetchone()
        if not row:
            return None
        raw = bytes(row["payload"])
        if int(row["encrypted"]) == 1 and self.encryption_ready and self._fernet is not None:
            try:
                raw = self._fernet.decrypt(raw)
            except Exception as exc:
                raise VendorError(
                    f"解密 {platform} cookies 失败：{exc}",
                    payload={"platform": platform},
                ) from exc
        try:
            cookies = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VendorError(
                f"cookies payload 损坏：{exc}", payload={"platform": platform}
            ) from exc
        cookies = _normalize_cookie_map(cookies)
        return CookiesEntry(
            platform=platform,
            cookies=cookies,
            expires_at=row["expires_at"],
            updated_at=int(row["updated_at"] or 0),
        )

    async def delete(self, platform: str) -> int:
        return await self._run(self._delete_sync, platform)

    def _delete_sync(self, platform: str) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM cookies_vault WHERE platform = ?", (platform,))
            return int(cur.rowcount)

    async def list_status(self) -> list[dict[str, Any]]:
        return await self._run(self._list_status_sync)

    def _list_status_sync(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT platform, encrypted, expires_at, updated_at"
                " FROM cookies_vault ORDER BY platform"
            ).fetchall()
        out: list[dict[str, Any]] = []
        now = _now()
        for r in rows:
            platform = str(r["platform"])
            exp = r["expires_at"]
            expired = bool(exp and exp <= now)
            entry = self._load_sync(platform)
            cookies = entry.cookies if entry else {}
            missing = missing_cookies_keys(platform, cookies)
            configured = bool(cookies)
            out.append(
                {
                    "platform": platform,
                    "encrypted": bool(r["encrypted"]),
                    "expires_at": exp,
                    "updated_at": int(r["updated_at"]),
                    "expired": expired,
                    "configured": configured,
                    "missing_keys": missing,
                    "required_keys": list(PLATFORM_COOKIES_REQUIRED.get(platform, ())),
                    "ok": configured and not missing and not expired,
                }
            )
        return out


# --------------------------------------------------------------------------- #
# Playwright driver                                                            #
# --------------------------------------------------------------------------- #


class PlaywrightUnavailable(VendorError):
    """Raised lazily on first crawler use when playwright isn't installed."""

    error_kind = "dependency"


@dataclass
class PageResponse:
    url: str
    status: int
    html: str
    json_payloads: list[dict[str, Any]] = field(default_factory=list)
    network_log: list[dict[str, Any]] = field(default_factory=list)


class PlaywrightDriver:
    """Single chromium pool with ``asyncio.Semaphore(2)`` for crawlers.

    The real implementation lives behind ``_ensure_browser`` which lazy-
    imports ``playwright.async_api``. Tests inject a *fake* driver via
    the optional ``override_fetch`` constructor arg, bypassing chromium
    altogether.
    """

    def __init__(
        self,
        *,
        max_concurrent: int = 2,
        override_fetch: Callable[..., Any] | None = None,
    ) -> None:
        self._sem = asyncio.Semaphore(max(1, int(max_concurrent)))
        self._browser: Any = None
        self._playwright: Any = None
        self._override_fetch = override_fetch

    async def aclose(self) -> None:
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

    async def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise PlaywrightUnavailable(
                "playwright 未安装；请执行 `pip install playwright` 后再 "
                "`python -m playwright install chromium`"
            ) from exc
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        return self._browser

    async def fetch(
        self,
        url: str,
        *,
        cookies: dict[str, str] | None = None,
        wait_selector: str | None = None,
        wait_for_url: str | None = None,
        capture_xhr: bool = False,
        domain: str | None = None,
        scroll_steps: int = 0,
        timeout_ms: int = 20_000,
        extra_headers: dict[str, str] | None = None,
    ) -> PageResponse:
        async with self._sem:
            if self._override_fetch is not None:
                return await self._override_fetch(
                    url=url,
                    cookies=cookies,
                    wait_selector=wait_selector,
                    wait_for_url=wait_for_url,
                    capture_xhr=capture_xhr,
                    domain=domain,
                    scroll_steps=scroll_steps,
                    timeout_ms=timeout_ms,
                    extra_headers=extra_headers,
                )
            return await self._fetch_real(
                url=url,
                cookies=cookies,
                wait_selector=wait_selector,
                wait_for_url=wait_for_url,
                capture_xhr=capture_xhr,
                domain=domain,
                scroll_steps=scroll_steps,
                timeout_ms=timeout_ms,
                extra_headers=extra_headers,
            )

    async def _fetch_real(
        self,
        url: str,
        *,
        cookies: dict[str, str] | None,
        wait_selector: str | None,
        wait_for_url: str | None,
        capture_xhr: bool,
        domain: str | None,
        scroll_steps: int,
        timeout_ms: int,
        extra_headers: dict[str, str] | None,
    ) -> PageResponse:  # pragma: no cover — needs real chromium
        browser = await self._ensure_browser()
        context = await browser.new_context()
        if cookies and domain:
            cookie_list = _playwright_cookie_list(cookies, domain)
            if cookie_list:
                try:
                    await self._add_cookies_best_effort(context, cookie_list, source_url=url)
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        await context.close()
                    raise VendorAuthError(
                        "browser rejected cookies; re-export this platform's cookies"
                    ) from exc
        if extra_headers:
            await context.set_extra_http_headers(extra_headers)
        page = await context.new_page()
        json_payloads: list[dict[str, Any]] = []
        network_log: list[dict[str, Any]] = []
        if capture_xhr:

            async def _on_response(response: Any) -> None:
                try:
                    content_type = response.headers.get("content-type", "") or ""
                    network_log.append(
                        {
                            "url": response.url,
                            "status": response.status,
                            "content_type": content_type,
                        }
                    )
                    if "json" in content_type:
                        json_payloads.append(await response.json())
                except Exception:
                    return

            page.on("response", _on_response)
        status = 0
        html = ""
        try:
            resp = await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            status = int(resp.status if resp else 0)
            if capture_xhr:
                with contextlib.suppress(Exception):
                    await page.wait_for_timeout(1200)
            if wait_selector:
                await page.wait_for_selector(wait_selector, timeout=timeout_ms)
            if wait_for_url:
                await page.wait_for_url(wait_for_url, timeout=timeout_ms)
            for _ in range(max(0, scroll_steps)):
                await page.mouse.wheel(0, 1500)
                await asyncio.sleep(0.8)
            html = await page.content()
        except Exception as exc:
            if _is_playwright_timeout(exc):
                raise VendorTimeoutError(
                    f"browser timeout fetching {url}: {exc}",
                    payload={"url": url, "timeout_ms": timeout_ms},
                ) from exc
            raise
        finally:
            with contextlib.suppress(Exception):
                await context.close()
        return PageResponse(
            url=url,
            status=status,
            html=html,
            json_payloads=json_payloads,
            network_log=network_log,
        )

    async def _add_cookies_best_effort(
        self,
        context: Any,
        cookie_list: list[dict[str, Any]],
        *,
        source_url: str,
    ) -> None:
        try:
            await context.add_cookies(cookie_list)
            return
        except Exception as batch_exc:
            accepted = 0
            last_exc: Exception = batch_exc
            parsed = urlsplit(source_url)
            origin_url = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else ""
            for cookie in cookie_list:
                try:
                    await context.add_cookies([cookie])
                    accepted += 1
                    continue
                except Exception as exc:
                    last_exc = exc
                if origin_url:
                    url_cookie = {
                        "name": cookie.get("name"),
                        "value": cookie.get("value"),
                        "url": origin_url,
                    }
                    try:
                        await context.add_cookies([url_cookie])
                        accepted += 1
                        continue
                    except Exception as exc:
                        last_exc = exc
                        continue
            if accepted > 0:
                return
            raise last_exc


# --------------------------------------------------------------------------- #
# Crawler base + 5 platforms                                                   #
# --------------------------------------------------------------------------- #


class CrawlerBase:
    """Shared plumbing for the 5 platform crawlers."""

    name: str = "base_crawler"
    platform: str = "other"
    cookies_required: tuple[str, ...] = ()
    cookies_domain: str = ""
    listing_url: str = ""

    def __init__(
        self,
        *,
        driver: PlaywrightDriver,
        vault: CookiesVault,
        http_client: httpx.AsyncClient | None = None,
        risk_acknowledged: bool = False,
    ) -> None:
        self._driver = driver
        self._vault = vault
        self._http = http_client
        self._risk_acknowledged = bool(risk_acknowledged)

    async def _load_cookies(self) -> dict[str, str]:
        if not self._risk_acknowledged:
            err = VendorError("Engine B 需用户先在 Settings → 数据源 勾选风险免责")
            err.error_kind = "auth"
            raise err
        entry = await self._vault.load(self.platform)
        cookies = entry.cookies if entry else {}
        missing = missing_cookies_keys(self.platform, cookies)
        if missing:
            err = VendorError(f"{self.platform} cookies 缺少必备字段: {missing}")
            err.error_kind = "cookies_expired"
            raise err
        if entry and entry.expires_at and entry.expires_at <= _now():
            err = VendorAuthError(f"{self.platform} cookies 已过期 (expires_at={entry.expires_at})")
            err.error_kind = "cookies_expired"
            raise err
        return cookies

    def _maybe_blocked(self, page: PageResponse) -> None:
        body = _visible_html_text(page.html or "")
        if page.status in (401, 403):
            err = VendorError(f"{self.platform} 登录失效 ({page.status})")
            err.error_kind = "cookies_expired"
            raise err
        matched = next((token for token in _CRAWLER_BLOCK_KEYWORDS if token in body), None)
        if matched:
            err = VendorError(f"{self.platform} 触发风控/验证码 (status={page.status})")
            err.error_kind = "crawler_blocked"
            err.payload = {
                "url": page.url,
                "status": page.status,
                "matched_token": matched,
            }
            raise err

    def _build_item(self, raw: dict[str, Any]) -> TrendItem:
        return TrendItem(
            id=_new_item_id(),
            platform=self.platform,  # type: ignore[arg-type]
            external_id=str(raw.get("external_id") or raw.get("id") or ""),
            external_url=str(raw.get("external_url") or raw.get("url") or ""),
            title=str(raw.get("title") or ""),
            author=str(raw.get("author") or ""),
            author_url=raw.get("author_url"),
            cover_url=raw.get("cover_url"),
            duration_seconds=raw.get("duration_seconds"),
            description=raw.get("description"),
            like_count=raw.get("like_count"),
            comment_count=raw.get("comment_count"),
            share_count=raw.get("share_count"),
            view_count=raw.get("view_count"),
            publish_at=int(raw.get("publish_at") or 0),
            fetched_at=_now(),
            engine_used="b",
            collector_name=self.name,
            raw_payload_json=json.dumps(raw, ensure_ascii=False),
            data_quality="high",
            keywords_matched=list(raw.get("keywords_matched", [])),
        )

    @staticmethod
    def _filter_keywords(items: list[TrendItem], keywords: list[str]) -> list[TrendItem]:
        if not keywords:
            return items
        kw_lower = [k.lower() for k in keywords if k]
        out: list[TrendItem] = []
        for it in items:
            text = f"{it.title} {it.description or ''}".lower()
            matched = [k for k in kw_lower if k in text]
            if matched:
                it.keywords_matched = matched
                out.append(it)
        return out


class DouyinCrawler(CrawlerBase):
    name = "douyin_crawler"
    platform = "douyin"
    cookies_required = ("sessionid_ss", "s_v_web_id", "ttwid")
    cookies_domain = ".douyin.com"
    listing_url = "https://www.douyin.com/hot"

    async def fetch_trending(
        self,
        keywords: list[str],
        time_window: str = "24h",
        limit: int = 20,
    ) -> list[TrendItem]:
        cookies = await self._load_cookies()
        page = await self._driver.fetch(
            self.listing_url,
            cookies=cookies,
            domain=self.cookies_domain,
            capture_xhr=True,
            scroll_steps=3,
            wait_selector="div[data-e2e='hot-list']",
            timeout_ms=20_000,
        )
        self._maybe_blocked(page)
        raws = _flatten_video_payloads(page.json_payloads, key_candidates=("video_id", "aweme_id"))
        items = [self._build_item(_normalize_douyin(r)) for r in raws[: limit * 3]]
        items = self._filter_keywords(items, keywords)
        return items[:limit]

    async def fetch_user(self, url: str, max_videos: int = 20) -> list[TrendItem]:
        cookies = await self._load_cookies()
        page = await self._driver.fetch(
            url,
            cookies=cookies,
            domain=self.cookies_domain,
            capture_xhr=True,
            scroll_steps=4,
            timeout_ms=25_000,
        )
        self._maybe_blocked(page)
        raws = _flatten_video_payloads(page.json_payloads, key_candidates=("video_id", "aweme_id"))
        return [self._build_item(_normalize_douyin(r)) for r in raws[:max_videos]]


class XhsCrawler(CrawlerBase):
    name = "xhs_crawler"
    platform = "xhs"
    cookies_required = ("web_session", "xsecappid", "a1")
    cookies_domain = ".xiaohongshu.com"
    listing_url = "https://www.xiaohongshu.com/explore"

    async def fetch_trending(
        self,
        keywords: list[str],
        time_window: str = "24h",
        limit: int = 20,
    ) -> list[TrendItem]:
        cookies = await self._load_cookies()
        page = await self._driver.fetch(
            self.listing_url,
            cookies=cookies,
            domain=self.cookies_domain,
            capture_xhr=True,
            scroll_steps=3,
            timeout_ms=20_000,
        )
        self._maybe_blocked(page)
        raws = _flatten_video_payloads(page.json_payloads, key_candidates=("note_id", "id"))
        items = [self._build_item(_normalize_xhs(r)) for r in raws[: limit * 3]]
        items = self._filter_keywords(items, keywords)
        return items[:limit]

    async def fetch_user(self, url: str, max_videos: int = 20) -> list[TrendItem]:
        cookies = await self._load_cookies()
        page = await self._driver.fetch(
            url,
            cookies=cookies,
            domain=self.cookies_domain,
            capture_xhr=True,
            scroll_steps=3,
            timeout_ms=25_000,
        )
        self._maybe_blocked(page)
        raws = _flatten_video_payloads(page.json_payloads, key_candidates=("note_id", "id"))
        return [self._build_item(_normalize_xhs(r)) for r in raws[:max_videos]]


class KsCrawler(CrawlerBase):
    name = "ks_crawler"
    platform = "ks"
    cookies_required = ()
    cookies_domain = ".kuaishou.com"
    listing_url = "https://www.kuaishou.com/brilliant"
    search_url = "https://www.kuaishou.com/search/video?searchKey="
    graphql_url = "https://www.kuaishou.com/graphql"
    graphql_search_query = (
        "fragment photoContent on PhotoEntity {\n"
        "  id\n"
        "  caption\n"
        "  coverUrl\n"
        "  duration\n"
        "  likeCount\n"
        "  viewCount\n"
        "  commentCount\n"
        "  timestamp\n"
        "  expTag\n"
        "  llsid\n"
        "}\n"
        "query visionSearchPhoto($keyword: String, $pcursor: String, $searchSessionId: String, $page: String, $webPageArea: String) {\n"
        "  visionSearchPhoto(keyword: $keyword, pcursor: $pcursor, searchSessionId: $searchSessionId, page: $page, webPageArea: $webPageArea) {\n"
        "    result\n"
        "    llsid\n"
        "    webPageArea\n"
        "    feeds {\n"
        "      type\n"
        "      author {\n"
        "        id\n"
        "        name\n"
        "        fansCount\n"
        "        headerUrl\n"
        "      }\n"
        "      photo {\n"
        "        ...photoContent\n"
        "      }\n"
        "    }\n"
        "    pcursor\n"
        "    searchSessionId\n"
        "  }\n"
        "}\n"
    )
    _http_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.kuaishou.com/",
        "Origin": "https://www.kuaishou.com",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._comment_cache: dict[str, list[dict[str, Any]]] = {}

    async def _load_ks_cookies(self) -> dict[str, str]:
        cookies = await self._load_cookies()
        if not cookies:
            err = VendorError("ks cookies 为空；请先在 Settings -> Data Sources 保存快手 cookies")
            err.error_kind = "cookies_expired"
            raise err
        return cookies

    async def _fetch_ks_page(
        self,
        url: str,
        *,
        scroll_steps: int,
        timeout_ms: int,
    ) -> PageResponse:
        cookies = await self._load_ks_cookies()
        page = await self._driver.fetch(
            url,
            cookies=cookies,
            domain=self.cookies_domain,
            capture_xhr=True,
            scroll_steps=scroll_steps,
            timeout_ms=timeout_ms,
            extra_headers={
                "Referer": "https://www.kuaishou.com/",
                "Origin": "https://www.kuaishou.com",
            },
        )
        self._maybe_blocked(page)
        return page

    def _ks_headers(
        self,
        cookies: dict[str, str],
        *,
        json_body: bool = False,
    ) -> dict[str, str]:
        headers = dict(self._http_headers)
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    async def _fetch_ks_http_page(self, url: str, *, timeout_s: float = 12.0) -> PageResponse:
        if self._http is None:
            err = VendorError("ks http client 未注入")
            err.error_kind = "dependency"
            raise err
        cookies = await self._load_ks_cookies()
        try:
            resp = await self._http.get(
                url,
                headers=self._ks_headers(cookies),
                timeout=timeout_s,
                follow_redirects=True,
            )
        except httpx.TimeoutException as exc:
            raise VendorTimeoutError(
                f"ks http timeout fetching {url}",
                payload={"url": url, "timeout_s": timeout_s},
            ) from exc
        except httpx.HTTPError as exc:
            raise VendorNetworkError(
                f"ks http error fetching {url}: {exc}",
                payload={"url": url, "error_type": type(exc).__name__},
            ) from exc
        page = PageResponse(
            url=str(resp.url),
            status=int(resp.status_code),
            html=resp.text,
            json_payloads=_extract_embedded_json_payloads(resp.text),
        )
        self._maybe_blocked(page)
        if resp.status_code in (401, 403):
            raise VendorAuthError(f"ks auth failed ({resp.status_code}) fetching {url}")
        if resp.status_code >= 400:
            raise VendorNetworkError(
                f"ks unexpected {resp.status_code} fetching {url}",
                status_code=resp.status_code,
            )
        return page

    async def _search_ks_via_http(
        self,
        keyword: str,
        *,
        timeout_s: float = 12.0,
    ) -> list[dict[str, Any]]:
        if self._http is None:
            return []
        cookies = await self._load_ks_cookies()
        body = {
            "operationName": "visionSearchPhoto",
            "query": self.graphql_search_query,
            "variables": {
                "keyword": keyword,
                "pcursor": "",
                "searchSessionId": "",
                "page": "search",
                "webPageArea": "searchResult",
            },
        }
        try:
            resp = await self._http.post(
                self.graphql_url,
                headers=self._ks_headers(cookies, json_body=True),
                json=body,
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise VendorTimeoutError(
                f"ks graphql timeout fetching {self.graphql_url}",
                payload={"url": self.graphql_url, "timeout_s": timeout_s},
            ) from exc
        except httpx.HTTPError as exc:
            raise VendorNetworkError(
                f"ks graphql error fetching {self.graphql_url}: {exc}",
                payload={"url": self.graphql_url, "error_type": type(exc).__name__},
            ) from exc
        if resp.status_code in (401, 403):
            raise VendorAuthError(f"ks auth failed ({resp.status_code}) fetching {self.graphql_url}")
        if resp.status_code >= 400:
            raise VendorNetworkError(
                f"ks unexpected {resp.status_code} fetching {self.graphql_url}",
                status_code=resp.status_code,
            )
        payload = resp.json()
        raws = _flatten_video_payloads([payload], key_candidates=("photoId", "photo_id", "photo", "id"))
        return raws

    def _trim_recent(self, items: list[TrendItem], time_window: str) -> list[TrendItem]:
        window_s = _time_window_seconds(time_window)
        if not window_s:
            return items
        cutoff = _now() - window_s
        filtered = [it for it in items if not it.publish_at or it.publish_at >= cutoff]
        return filtered or items

    @staticmethod
    def _dedupe_items(items: list[TrendItem]) -> list[TrendItem]:
        seen: set[tuple[str, str]] = set()
        out: list[TrendItem] = []
        for item in items:
            key = (item.external_id or "", item.external_url or "")
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    async def fetch_brilliant(self, *, time_window: str = "24h", limit: int = 20) -> list[TrendItem]:
        http_exc: VendorError | None = None
        if self._http is not None:
            try:
                page = await self._fetch_ks_http_page(self.listing_url)
            except VendorError as exc:
                http_exc = exc
                page = await self._fetch_ks_page(self.listing_url, scroll_steps=3, timeout_ms=20_000)
        else:
            page = await self._fetch_ks_page(self.listing_url, scroll_steps=3, timeout_ms=20_000)
        raws = _flatten_video_payloads(
            page.json_payloads,
            key_candidates=("photoId", "photo_id", "photo", "id"),
        )
        if not raws:
            if http_exc is not None:
                raise http_exc
            _raise_empty_parse("ks", page, ("photoId", "photo_id", "photo", "id"))
        items = self._dedupe_items([self._build_item(_normalize_ks(r)) for r in raws[: limit * 3]])
        return self._trim_recent(items, time_window)[:limit]

    async def search_by_keyword(
        self,
        keyword: str,
        *,
        time_window: str = "24h",
        limit: int = 20,
    ) -> list[TrendItem]:
        query = str(keyword or "").strip()
        if not query:
            return []
        if self._http is not None:
            raws = await self._search_ks_via_http(query)
            if not raws:
                page = await self._fetch_ks_http_page(f"{self.search_url}{quote(query)}")
                raws = _flatten_video_payloads(
                    page.json_payloads,
                    key_candidates=("photoId", "photo_id", "photo", "id"),
                )
            else:
                page = PageResponse(
                    url=f"{self.search_url}{quote(query)}",
                    status=200,
                    html="",
                    json_payloads=[],
                )
        else:
            page = await self._fetch_ks_page(
                f"{self.search_url}{quote(query)}",
                scroll_steps=2,
                timeout_ms=20_000,
            )
            raws = _flatten_video_payloads(
                page.json_payloads,
                key_candidates=("photoId", "photo_id", "photo", "id"),
            )
        if not raws:
            _raise_empty_parse("ks", page, ("photoId", "photo_id", "photo", "id"))
        items = self._dedupe_items([self._build_item(_normalize_ks(r)) for r in raws[: limit * 3]])
        items = self._trim_recent(items, time_window)
        items = self._filter_keywords(items, [query])
        for item in items:
            if not item.keywords_matched:
                item.keywords_matched = [query.lower()]
        return items[:limit]

    async def fetch_trending(
        self,
        keywords: list[str],
        time_window: str = "24h",
        limit: int = 20,
    ) -> list[TrendItem]:
        if keywords:
            gathered: list[TrendItem] = []
            for keyword in keywords:
                with contextlib.suppress(VendorError):
                    gathered.extend(
                        await self.search_by_keyword(keyword, time_window=time_window, limit=limit)
                    )
            if not gathered:
                fallback = await self.fetch_brilliant(time_window=time_window, limit=limit * 3)
                gathered = self._filter_keywords(fallback, keywords)
            unique: dict[str, TrendItem] = {}
            for item in gathered:
                if item.external_id and item.external_id not in unique:
                    unique[item.external_id] = item
            return list(unique.values())[:limit]
        return await self.fetch_brilliant(time_window=time_window, limit=limit)

    async def fetch_creator(self, url: str, max_videos: int = 20) -> dict[str, Any]:
        page = await self._fetch_ks_page(url, scroll_steps=4, timeout_ms=25_000)
        raws = _flatten_video_payloads(
            page.json_payloads,
            key_candidates=("photoId", "photo_id", "photo", "id"),
        )
        if not raws:
            _raise_empty_parse("ks", page, ("photoId", "photo_id", "photo", "id"))
        creator = _extract_ks_creator(page.json_payloads, profile_url=url)
        videos = [self._build_item(_normalize_ks(r)) for r in raws[:max_videos]]
        return {"creator": creator, "videos": videos}

    async def fetch_user(self, url: str, max_videos: int = 20) -> list[TrendItem]:
        creator = await self.fetch_creator(url, max_videos=max_videos)
        return list(creator.get("videos") or [])

    async def fetch_detail(self, url: str) -> TrendItem:
        page = await self._fetch_ks_page(url, scroll_steps=1, timeout_ms=20_000)
        raw = _extract_ks_detail(page.json_payloads)
        if raw is None:
            _raise_empty_parse("ks", page, ("photoId", "photo_id", "photo", "id"))
        item = self._build_item(_normalize_ks(raw))
        self._comment_cache[item.external_id] = _extract_ks_comments(page.json_payloads, limit=40)
        return item

    async def fetch_comments(self, item: TrendItem, *, limit: int = 40) -> list[dict[str, Any]]:
        cached = list(self._comment_cache.get(item.external_id) or [])
        if cached:
            return cached[:limit]
        try:
            page = await self._fetch_ks_page(item.external_url, scroll_steps=1, timeout_ms=20_000)
        except VendorError:
            return []
        comments = _extract_ks_comments(page.json_payloads, limit=limit)
        self._comment_cache[item.external_id] = list(comments)
        return comments[:limit]


class BiliLoggedCrawler(CrawlerBase):
    name = "bili_logged_crawler"
    platform = "bilibili"
    cookies_required = ("SESSDATA", "bili_jct", "DedeUserID")
    cookies_domain = ".bilibili.com"
    listing_url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all"

    async def fetch_trending(
        self,
        keywords: list[str],
        time_window: str = "24h",
        limit: int = 20,
    ) -> list[TrendItem]:
        cookies = await self._load_cookies()
        page = await self._driver.fetch(
            self.listing_url,
            cookies=cookies,
            domain=self.cookies_domain,
            capture_xhr=True,
            timeout_ms=20_000,
        )
        self._maybe_blocked(page)
        raws = _flatten_video_payloads(page.json_payloads, key_candidates=("bvid", "id"))
        items = [self._build_item(_normalize_bili_logged(r)) for r in raws[: limit * 3]]
        items = self._filter_keywords(items, keywords)
        return items[:limit]

    async def fetch_user(self, url: str, max_videos: int = 20) -> list[TrendItem]:
        cookies = await self._load_cookies()
        page = await self._driver.fetch(
            url,
            cookies=cookies,
            domain=self.cookies_domain,
            capture_xhr=True,
            scroll_steps=2,
            timeout_ms=25_000,
        )
        self._maybe_blocked(page)
        raws = _flatten_video_payloads(page.json_payloads, key_candidates=("bvid", "id"))
        return [self._build_item(_normalize_bili_logged(r)) for r in raws[:max_videos]]


class WeiboCrawler(CrawlerBase):
    name = "weibo_crawler"
    platform = "weibo"
    cookies_required = ("SUB", "SUBP")
    cookies_domain = ".weibo.com"
    listing_url = "https://s.weibo.com/top/summary"

    async def fetch_trending(
        self,
        keywords: list[str],
        time_window: str = "24h",
        limit: int = 20,
    ) -> list[TrendItem]:
        cookies = await self._load_cookies()
        page = await self._driver.fetch(
            self.listing_url,
            cookies=cookies,
            domain=self.cookies_domain,
            capture_xhr=True,
            scroll_steps=2,
            timeout_ms=20_000,
        )
        self._maybe_blocked(page)
        raws = _flatten_video_payloads(page.json_payloads, key_candidates=("mid", "id"))
        items = [self._build_item(_normalize_weibo(r)) for r in raws[: limit * 3]]
        items = self._filter_keywords(items, keywords)
        return items[:limit]

    async def fetch_user(self, url: str, max_videos: int = 20) -> list[TrendItem]:
        cookies = await self._load_cookies()
        page = await self._driver.fetch(
            url,
            cookies=cookies,
            domain=self.cookies_domain,
            capture_xhr=True,
            scroll_steps=3,
            timeout_ms=25_000,
        )
        self._maybe_blocked(page)
        raws = _flatten_video_payloads(page.json_payloads, key_candidates=("mid", "id"))
        return [self._build_item(_normalize_weibo(r)) for r in raws[:max_videos]]


# --------------------------------------------------------------------------- #
# Per-platform XHR payload normalisers                                         #
# --------------------------------------------------------------------------- #


def _flatten_video_payloads(
    payloads: list[dict[str, Any]], *, key_candidates: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Flatten heterogeneous platform XHR JSON shapes into a flat list."""

    out: list[dict[str, Any]] = []
    seen: set[int] = set()

    def append_entry(entry: dict[str, Any]) -> None:
        ident = id(entry)
        if ident in seen:
            return
        seen.add(ident)
        out.append(entry)

    def scan(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                scan(child)
            return
        if not isinstance(value, dict):
            return
        if _looks_like_video_entry(value, key_candidates):
            append_entry(value)
        for child in value.values():
            if isinstance(child, (dict, list)):
                scan(child)

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for path in (
            ("aweme_list",),
            ("data", "aweme_list"),
            ("items",),
            ("data", "items"),
            ("data", "feeds"),
            ("data", "list"),
            ("data", "notes"),
            ("data", "noteList"),
            ("notes",),
            ("statuses",),
            ("data", "cards"),
            ("data", "feed"),
            ("data", "visionHotRank", "feeds"),
            ("data", "visionProfilePhotoList", "feeds"),
            ("data", "visionSearchPhoto", "feeds"),
            ("data", "visionSearchPhoto", "photos"),
        ):
            cur: Any = payload
            ok = True
            for key in path:
                if isinstance(cur, dict) and key in cur:
                    cur = cur[key]
                else:
                    ok = False
                    break
            if ok and isinstance(cur, list):
                for entry in cur:
                    if isinstance(entry, dict) and _looks_like_video_entry(entry, key_candidates):
                        append_entry(entry)
        scan(payload)
    return out


def _looks_like_video_entry(entry: dict[str, Any], key_candidates: tuple[str, ...]) -> bool:
    non_id_candidates = [k for k in key_candidates if k != "id"]
    if any(entry.get(k) for k in non_id_candidates):
        return True
    if not entry.get("id"):
        return False
    hint_keys = {
        "aweme_id",
        "video_id",
        "note_id",
        "bvid",
        "photoId",
        "photo_id",
        "photo",
        "video",
        "caption",
        "title",
        "desc",
        "cover",
        "coverUrl",
        "duration",
        "timestamp",
        "create_time",
        "statistics",
        "likeCount",
        "commentCount",
        "viewCount",
    }
    return any(k in entry for k in hint_keys)


def _raise_empty_parse(
    platform: str,
    page: PageResponse,
    key_candidates: tuple[str, ...],
) -> None:
    json_urls = [
        row.get("url")
        for row in (page.network_log or [])
        if isinstance(row, dict)
        and row.get("url")
        and "json" in str(row.get("content_type") or "").lower()
    ][:8]
    if not json_urls:
        json_urls = [
            row.get("url")
            for row in (page.network_log or [])
            if isinstance(row, dict) and row.get("url")
        ][:8]
    payload_keys: list[list[str]] = []
    for payload in (page.json_payloads or [])[:5]:
        if isinstance(payload, dict):
            payload_keys.append(sorted(str(k) for k in payload.keys())[:20])
    err = VendorFormatError(
        f"{platform} crawler parsed zero video payloads "
        f"(status={page.status}, json_payloads={len(page.json_payloads)})",
        payload={
            "url": page.url,
            "status": page.status,
            "json_payload_count": len(page.json_payloads),
            "network_count": len(page.network_log or []),
            "network_urls": json_urls,
            "payload_top_keys": payload_keys,
            "key_candidates": list(key_candidates),
            "visible_text_excerpt": _visible_html_text(page.html or "")[:240],
        },
    )
    raise err


def _normalize_douyin(raw: dict[str, Any]) -> dict[str, Any]:
    aweme = raw
    statistics = aweme.get("statistics") or {}
    author = aweme.get("author") or {}
    return {
        "external_id": str(aweme.get("aweme_id") or aweme.get("video_id") or ""),
        "external_url": (
            f"https://www.douyin.com/video/{aweme.get('aweme_id')}"
            if aweme.get("aweme_id")
            else aweme.get("share_url") or ""
        ),
        "title": aweme.get("desc") or aweme.get("title") or "",
        "author": author.get("nickname") or "",
        "author_url": (
            f"https://www.douyin.com/user/{author['sec_uid']}" if author.get("sec_uid") else None
        ),
        "cover_url": ((aweme.get("video") or {}).get("cover") or {}).get("url_list", [None])[0]
        if isinstance(aweme.get("video"), dict)
        else None,
        "duration_seconds": (aweme.get("video") or {}).get("duration")
        and int((aweme.get("video") or {}).get("duration") / 1000),
        "like_count": statistics.get("digg_count"),
        "comment_count": statistics.get("comment_count"),
        "share_count": statistics.get("share_count"),
        "view_count": statistics.get("play_count"),
        "publish_at": aweme.get("create_time") or 0,
    }


def _normalize_xhs(raw: dict[str, Any]) -> dict[str, Any]:
    user = raw.get("user") or raw.get("author") or {}
    interact = raw.get("interact_info") or raw.get("interactInfo") or {}
    note_id = raw.get("note_id") or raw.get("id") or ""
    return {
        "external_id": str(note_id),
        "external_url": (
            f"https://www.xiaohongshu.com/explore/{note_id}" if note_id else raw.get("url") or ""
        ),
        "title": raw.get("title") or raw.get("display_title") or "",
        "author": user.get("nickname") or user.get("name") or "",
        "author_url": (
            f"https://www.xiaohongshu.com/user/profile/{user['user_id']}"
            if user.get("user_id")
            else None
        ),
        "cover_url": (
            (raw.get("cover") or {}).get("url")
            or (raw.get("cover") or {}).get("urlDefault")
            or None
        ),
        "like_count": interact.get("liked_count") or interact.get("likedCount"),
        "comment_count": interact.get("comment_count") or interact.get("commentCount"),
        "share_count": interact.get("shared_count") or interact.get("shareCount"),
        "view_count": interact.get("view_count"),
        "publish_at": raw.get("time") or raw.get("create_time") or 0,
    }


def _normalize_ks(raw: dict[str, Any]) -> dict[str, Any]:
    photo = raw.get("photo") if isinstance(raw.get("photo"), dict) else raw
    user = (
        raw.get("user")
        or raw.get("author")
        or raw.get("owner")
        or photo.get("user")
        or photo.get("author")
        or {}
    )
    photo_id = (
        raw.get("photoId")
        or raw.get("photo_id")
        or photo.get("photoId")
        or photo.get("photo_id")
        or photo.get("id")
        or raw.get("id")
        or ""
    )
    duration = _coerce_int(raw.get("duration") or photo.get("duration"))
    if duration and duration > 1000:
        duration = int(duration / 1000)
    return {
        "external_id": str(photo_id),
        "external_url": (f"https://www.kuaishou.com/short-video/{photo_id}" if photo_id else ""),
        "title": raw.get("caption") or photo.get("caption") or raw.get("title") or photo.get("title") or "",
        "author": user.get("name") or user.get("user_name") or user.get("userName") or "",
        "author_url": (
            f"https://www.kuaishou.com/profile/{user['id']}" if user.get("id") else None
        ),
        "cover_url": raw.get("coverUrl") or raw.get("cover_url") or photo.get("coverUrl") or photo.get("cover_url"),
        "duration_seconds": duration,
        "like_count": raw.get("likeCount") or photo.get("likeCount"),
        "comment_count": raw.get("commentCount") or photo.get("commentCount"),
        "view_count": raw.get("viewCount") or photo.get("viewCount"),
        "publish_at": raw.get("timestamp") or photo.get("timestamp") or 0,
    }


def _extract_ks_creator(payloads: list[Any], *, profile_url: str) -> dict[str, Any]:
    creator: dict[str, Any] = {
        "name": "",
        "profile_url": profile_url,
        "follower_count": None,
        "bio": None,
    }
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        profile = data.get("visionProfile")
        if not isinstance(profile, dict):
            continue
        user_profile = profile.get("userProfile") or {}
        meta = user_profile.get("profile") or {}
        name = (
            meta.get("user_name")
            or meta.get("name")
            or meta.get("nickname")
            or creator["name"]
        )
        creator["name"] = str(name or "")
        creator["bio"] = meta.get("user_text") or meta.get("bio") or creator.get("bio")
        creator["follower_count"] = _coerce_int(
            user_profile.get("ownerCount")
            or user_profile.get("fanCount")
            or meta.get("follower_count")
        )
        profile_id = meta.get("user_id") or meta.get("id")
        if profile_id:
            creator["profile_url"] = f"https://www.kuaishou.com/profile/{profile_id}"
        break
    return creator


def _extract_ks_detail(payloads: list[Any]) -> dict[str, Any] | None:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        detail = data.get("visionVideoDetail") or data.get("visionPhotoDetail")
        if not isinstance(detail, dict):
            continue
        photo = detail.get("photo")
        if isinstance(photo, dict):
            author = detail.get("author") or detail.get("user") or {}
            if isinstance(author, dict):
                return {"photo": photo, "author": author}
            return {"photo": photo}
    return None


def _extract_ks_comments(payloads: list[Any], *, limit: int = 40) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def append_comment(raw: dict[str, Any]) -> None:
        if len(out) >= limit:
            return
        comment_id = raw.get("commentId") or raw.get("id") or raw.get("cid")
        text = raw.get("content") or raw.get("text") or raw.get("comment")
        if not comment_id or not text:
            return
        author = (
            raw.get("authorName")
            or ((raw.get("author") or {}).get("name") if isinstance(raw.get("author"), dict) else None)
            or ((raw.get("user") or {}).get("name") if isinstance(raw.get("user"), dict) else None)
            or ""
        )
        out.append(
            {
                "comment_id": str(comment_id),
                "text": str(text),
                "author": str(author or ""),
                "like_count": _coerce_int(raw.get("likeCount") or raw.get("like_count")),
                "publish_at": _coerce_int(raw.get("timestamp") or raw.get("createTime")) or 0,
            }
        )

    def scan(value: Any) -> None:
        if len(out) >= limit:
            return
        if isinstance(value, list):
            for child in value:
                scan(child)
            return
        if not isinstance(value, dict):
            return
        if value.get("commentId") or value.get("cid"):
            append_comment(value)
        for child in value.values():
            if isinstance(child, (dict, list)):
                scan(child)

    for payload in payloads:
        scan(payload)
        if len(out) >= limit:
            break
    return out[:limit]


def _normalize_bili_logged(raw: dict[str, Any]) -> dict[str, Any]:
    bvid = raw.get("bvid") or raw.get("id") or ""
    stat = raw.get("stat") or {}
    owner = raw.get("owner") or {}
    return {
        "external_id": str(bvid),
        "external_url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
        "title": raw.get("title") or "",
        "author": owner.get("name") or "",
        "author_url": (f"https://space.bilibili.com/{owner['mid']}" if owner.get("mid") else None),
        "cover_url": raw.get("pic"),
        "duration_seconds": raw.get("duration"),
        "like_count": stat.get("like"),
        "comment_count": stat.get("reply"),
        "share_count": stat.get("share"),
        "view_count": stat.get("view"),
        "publish_at": raw.get("pubdate") or raw.get("ctime") or 0,
    }


def _normalize_weibo(raw: dict[str, Any]) -> dict[str, Any]:
    user = raw.get("user") or {}
    mid = raw.get("mid") or raw.get("id") or ""
    return {
        "external_id": str(mid),
        "external_url": (
            f"https://weibo.com/{user.get('id', '')}/{mid}" if mid else raw.get("url") or ""
        ),
        "title": raw.get("text_raw") or raw.get("text") or raw.get("title") or "",
        "author": user.get("screen_name") or "",
        "author_url": (f"https://weibo.com/u/{user['id']}" if user.get("id") else None),
        "cover_url": (raw.get("pic_infos") or {}).get("largest", {}).get("url"),
        "like_count": raw.get("attitudes_count"),
        "comment_count": raw.get("comments_count"),
        "share_count": raw.get("reposts_count"),
        "publish_at": _parse_weibo_time(raw.get("created_at")),
    }


def _parse_weibo_time(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        from datetime import datetime

        return int(datetime.strptime(str(value), "%a %b %d %H:%M:%S %z %Y").timestamp())
    except Exception:
        return 0


__all__ = [
    "BiliLoggedCrawler",
    "CookiesEntry",
    "CookiesVault",
    "CrawlerBase",
    "DouyinCrawler",
    "KsCrawler",
    "PageResponse",
    "PLATFORM_COOKIES_REQUIRED",
    "PlaywrightDriver",
    "PlaywrightUnavailable",
    "WeiboCrawler",
    "XhsCrawler",
    "missing_cookies_keys",
    "parse_cookies_upload_text",
]
