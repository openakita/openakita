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
import contextlib
import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from idea_models import TrendItem
from idea_research_inline.vendor_client import VendorAuthError, VendorError

_CRAWLER_BLOCK_KEYWORDS = (
    "captcha",
    "verify",
    "请输入验证",
    "访问异常",
    "需要登录",
    "sign in",
    "robot check",
)


def _now() -> int:
    return int(time.time())


def _new_item_id() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# CookiesVault                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class CookiesEntry:
    platform: str
    cookies: dict[str, str]
    expires_at: int | None = None
    updated_at: int = 0


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
        self, platform: str, cookies: dict[str, str], *, expires_at: int | None = None
    ) -> bool:
        return await self._run(self._save_sync, platform, cookies, expires_at)

    def _save_sync(
        self,
        platform: str,
        cookies: dict[str, str],
        expires_at: int | None,
    ) -> bool:
        encrypted_flag = 1 if self.encryption_ready else 0
        payload_json = json.dumps(cookies, ensure_ascii=False).encode("utf-8")
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
            exp = r["expires_at"]
            out.append(
                {
                    "platform": r["platform"],
                    "encrypted": bool(r["encrypted"]),
                    "expires_at": exp,
                    "updated_at": int(r["updated_at"]),
                    "expired": bool(exp and exp <= now),
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
            await context.add_cookies(
                [
                    {
                        "name": k,
                        "value": v,
                        "domain": domain,
                        "path": "/",
                    }
                    for k, v in cookies.items()
                ]
            )
        if extra_headers:
            await context.set_extra_http_headers(extra_headers)
        page = await context.new_page()
        json_payloads: list[dict[str, Any]] = []
        network_log: list[dict[str, Any]] = []
        if capture_xhr:

            async def _on_response(response: Any) -> None:
                try:
                    network_log.append({"url": response.url, "status": response.status})
                    if "json" in (response.headers.get("content-type", "") or ""):
                        json_payloads.append(await response.json())
                except Exception:
                    return

            page.on("response", _on_response)
        status = 0
        html = ""
        try:
            resp = await page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            status = int(resp.status if resp else 0)
            if wait_selector:
                await page.wait_for_selector(wait_selector, timeout=timeout_ms)
            if wait_for_url:
                await page.wait_for_url(wait_for_url, timeout=timeout_ms)
            for _ in range(max(0, scroll_steps)):
                await page.mouse.wheel(0, 1500)
                await asyncio.sleep(0.8)
            html = await page.content()
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
        risk_acknowledged: bool = False,
    ) -> None:
        self._driver = driver
        self._vault = vault
        self._risk_acknowledged = bool(risk_acknowledged)

    async def _load_cookies(self) -> dict[str, str]:
        if not self._risk_acknowledged:
            err = VendorError("Engine B 需用户先在 Settings → 数据源 勾选风险免责")
            err.error_kind = "auth"
            raise err
        entry = await self._vault.load(self.platform)
        cookies = entry.cookies if entry else {}
        missing = [k for k in self.cookies_required if not cookies.get(k)]
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
        body = (page.html or "").lower()
        if any(token in body for token in _CRAWLER_BLOCK_KEYWORDS):
            err = VendorError(f"{self.platform} 触发风控/验证码 (status={page.status})")
            err.error_kind = "crawler_blocked"
            raise err
        if page.status in (401, 403):
            err = VendorError(f"{self.platform} 登录失效 ({page.status})")
            err.error_kind = "cookies_expired"
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
    cookies_required = ("did", "kpf", "kpn", "clientid")
    cookies_domain = ".kuaishou.com"
    listing_url = "https://www.kuaishou.com/brilliant"

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
            extra_headers={"Referer": "https://www.kuaishou.com/"},
        )
        self._maybe_blocked(page)
        raws = _flatten_video_payloads(page.json_payloads, key_candidates=("photoId", "id"))
        items = [self._build_item(_normalize_ks(r)) for r in raws[: limit * 3]]
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
        raws = _flatten_video_payloads(page.json_payloads, key_candidates=("photoId", "id"))
        return [self._build_item(_normalize_ks(r)) for r in raws[:max_videos]]


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
                    if isinstance(entry, dict) and any(entry.get(k) for k in key_candidates):
                        out.append(entry)
        if not out and any(payload.get(k) for k in key_candidates):
            out.append(payload)
    return out


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
    photo_id = raw.get("photoId") or raw.get("id") or ""
    user = raw.get("user") or raw.get("author") or {}
    return {
        "external_id": str(photo_id),
        "external_url": (f"https://www.kuaishou.com/short-video/{photo_id}" if photo_id else ""),
        "title": raw.get("caption") or raw.get("title") or "",
        "author": user.get("name") or user.get("user_name") or "",
        "author_url": (
            f"https://www.kuaishou.com/profile/{user['id']}" if user.get("id") else None
        ),
        "cover_url": raw.get("coverUrl") or raw.get("cover_url"),
        "duration_seconds": raw.get("duration") and int(raw.get("duration") / 1000),
        "like_count": raw.get("likeCount"),
        "comment_count": raw.get("commentCount"),
        "view_count": raw.get("viewCount"),
        "publish_at": raw.get("timestamp") or 0,
    }


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
    "PlaywrightDriver",
    "PlaywrightUnavailable",
    "WeiboCrawler",
    "XhsCrawler",
]
