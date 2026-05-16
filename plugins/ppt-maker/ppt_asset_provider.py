"""Image / icon resolution for ppt-maker.

Three image paths (chosen via the ``image_provider`` setting), all independent
of any other plugin:
  - ``pexels``    — `Pexels <https://www.pexels.com/api/>`_ free stock photos.
  - ``pixabay``   — `Pixabay <https://pixabay.com/api/docs/>`_ free stock photos.
  - ``dashscope`` — Alibaba 百炼 image generation (T2I task) for synthetic art.
  - ``none``      — disabled; falls back to icon-only or plain layout.

Icons are resolved deterministically: a small in-memory ``ICON_TABLE`` maps
English keywords to a ``python-pptx`` MSO_SHAPE plus a Unicode glyph. No vector
DB, no FastEmbed, no extra dependencies.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

try:
    from pptx.enum.shapes import MSO_SHAPE  # type: ignore[import-not-found]
except ModuleNotFoundError as exc:
    if exc.name != "pptx":
        raise
    MSO_SHAPE = SimpleNamespace(
        CLOUD_CALLOUT=108,
        DOWN_ARROW=36,
        GEAR_6=172,
        ISOSCELES_TRIANGLE=7,
        LIGHTNING_BOLT=22,
        OVAL=9,
        PARALLELOGRAM=2,
        PENTAGON=56,
        RECTANGLE=1,
        RIGHT_ARROW=33,
        ROUNDED_RECTANGLE=5,
        STAR_5_POINT=92,
        UP_ARROW=35,
    )

logger = logging.getLogger(__name__)


PEXELS_ENDPOINT = "https://api.pexels.com/v1/search"
PIXABAY_ENDPOINT = "https://pixabay.com/api/"
DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com"
DASHSCOPE_T2I_SUBMIT_PATH = "/api/v1/services/aigc/text2image/image-synthesis"
DASHSCOPE_TASK_QUERY_PATH = "/api/v1/tasks/{task_id}"

# Kept as module-level constants for backwards compat — callers that
# don't override base_url still see the official endpoints.
DASHSCOPE_T2I_SUBMIT = f"{DASHSCOPE_DEFAULT_BASE_URL}{DASHSCOPE_T2I_SUBMIT_PATH}"
DASHSCOPE_TASK_QUERY = f"{DASHSCOPE_DEFAULT_BASE_URL}{DASHSCOPE_TASK_QUERY_PATH}"


ICON_TABLE: dict[str, dict[str, Any]] = {
    "growth": {"shape": MSO_SHAPE.UP_ARROW, "emoji": "📈"},
    "decline": {"shape": MSO_SHAPE.DOWN_ARROW, "emoji": "📉"},
    "team": {"shape": MSO_SHAPE.OVAL, "emoji": "👥"},
    "users": {"shape": MSO_SHAPE.OVAL, "emoji": "👤"},
    "people": {"shape": MSO_SHAPE.OVAL, "emoji": "👥"},
    "shield": {"shape": MSO_SHAPE.PENTAGON, "emoji": "🛡"},
    "security": {"shape": MSO_SHAPE.PENTAGON, "emoji": "🔒"},
    "lock": {"shape": MSO_SHAPE.PENTAGON, "emoji": "🔒"},
    "rocket": {"shape": MSO_SHAPE.ISOSCELES_TRIANGLE, "emoji": "🚀"},
    "target": {"shape": MSO_SHAPE.OVAL, "emoji": "🎯"},
    "goal": {"shape": MSO_SHAPE.OVAL, "emoji": "🎯"},
    "idea": {"shape": MSO_SHAPE.LIGHTNING_BOLT, "emoji": "💡"},
    "innovation": {"shape": MSO_SHAPE.LIGHTNING_BOLT, "emoji": "💡"},
    "data": {"shape": MSO_SHAPE.RECTANGLE, "emoji": "📊"},
    "analytics": {"shape": MSO_SHAPE.RECTANGLE, "emoji": "📊"},
    "chart": {"shape": MSO_SHAPE.RECTANGLE, "emoji": "📊"},
    "money": {"shape": MSO_SHAPE.OVAL, "emoji": "💰"},
    "finance": {"shape": MSO_SHAPE.OVAL, "emoji": "💰"},
    "revenue": {"shape": MSO_SHAPE.OVAL, "emoji": "💰"},
    "warning": {"shape": MSO_SHAPE.ISOSCELES_TRIANGLE, "emoji": "⚠"},
    "risk": {"shape": MSO_SHAPE.ISOSCELES_TRIANGLE, "emoji": "⚠"},
    "check": {"shape": MSO_SHAPE.ROUNDED_RECTANGLE, "emoji": "✅"},
    "success": {"shape": MSO_SHAPE.ROUNDED_RECTANGLE, "emoji": "✅"},
    "globe": {"shape": MSO_SHAPE.OVAL, "emoji": "🌐"},
    "global": {"shape": MSO_SHAPE.OVAL, "emoji": "🌐"},
    "world": {"shape": MSO_SHAPE.OVAL, "emoji": "🌐"},
    "cloud": {"shape": MSO_SHAPE.CLOUD_CALLOUT, "emoji": "☁"},
    "lightbulb": {"shape": MSO_SHAPE.LIGHTNING_BOLT, "emoji": "💡"},
    "tools": {"shape": MSO_SHAPE.RECTANGLE, "emoji": "🛠"},
    "settings": {"shape": MSO_SHAPE.GEAR_6, "emoji": "⚙"},
    "ai": {"shape": MSO_SHAPE.PARALLELOGRAM, "emoji": "🤖"},
    "robot": {"shape": MSO_SHAPE.PARALLELOGRAM, "emoji": "🤖"},
    "speed": {"shape": MSO_SHAPE.RIGHT_ARROW, "emoji": "⚡"},
    "performance": {"shape": MSO_SHAPE.RIGHT_ARROW, "emoji": "⚡"},
    "calendar": {"shape": MSO_SHAPE.RECTANGLE, "emoji": "📅"},
    "time": {"shape": MSO_SHAPE.OVAL, "emoji": "⏱"},
    "process": {"shape": MSO_SHAPE.PENTAGON, "emoji": "🔄"},
    "milestone": {"shape": MSO_SHAPE.STAR_5_POINT, "emoji": "⭐"},
    "star": {"shape": MSO_SHAPE.STAR_5_POINT, "emoji": "⭐"},
    "default": {"shape": MSO_SHAPE.OVAL, "emoji": "•"},
}


class PptAssetProvider:
    """Resolve image/icon queries to local files / shapes.

    Settings keys (all stored in the plugin's ``settings.json``):
      - ``image_provider``   ∈ {none, pexels, pixabay, dashscope}
      - ``pexels_api_key``
      - ``pixabay_api_key``
      - ``dashscope_api_key``
      - ``dashscope_image_model`` (default ``wanx-v1``)
    """

    def __init__(self, *, settings: dict[str, str], data_root: str | Path) -> None:
        self._settings = dict(settings or {})
        self._data_root = Path(data_root)

    def update_settings(self, settings: dict[str, str]) -> None:
        self._settings = dict(settings or {})

    @property
    def provider(self) -> str:
        raw = (self._settings.get("image_provider") or "none").strip().lower()
        return raw if raw in {"none", "pexels", "pixabay", "dashscope"} else "none"

    # ── Image ──────────────────────────────────────────────────────────

    async def resolve_image(self, *, query: str, project_id: str) -> str | None:
        provider = self.provider
        if not query or provider == "none":
            return None

        out_dir = self._image_dir(project_id)
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            if provider == "pexels":
                return await self._fetch_pexels(query, out_dir)
            if provider == "pixabay":
                return await self._fetch_pixabay(query, out_dir)
            if provider == "dashscope":
                return await self._gen_dashscope(query, out_dir)
        except Exception as exc:  # noqa: BLE001
            logger.info("ppt-maker image resolve failed (%s/%s): %s", provider, query, exc)
            return None
        return None

    # ── Icon ───────────────────────────────────────────────────────────

    def resolve_icon(self, query: str | None) -> dict[str, Any] | None:
        if not query:
            return None
        token = self._normalize_icon_query(query)
        if not token:
            return None
        for key, value in ICON_TABLE.items():
            if key in token:
                return {"shape": value["shape"], "emoji": value["emoji"], "keyword": key}
        return {**ICON_TABLE["default"], "keyword": "default"}

    @staticmethod
    def _normalize_icon_query(query: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z\u4e00-\u9fff ]", " ", query)
        return cleaned.strip().lower()

    # ── Pexels / Pixabay / DashScope (independent HTTP clients) ────────

    async def _fetch_pexels(self, query: str, out_dir: Path) -> str | None:
        api_key = (self._settings.get("pexels_api_key") or "").strip()
        if not api_key:
            return None
        import httpx  # local import keeps optional dep optional

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                PEXELS_ENDPOINT,
                params={"query": query, "per_page": 1, "size": "medium", "orientation": "landscape"},
                headers={"Authorization": api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            photos = data.get("photos") or []
            if not photos:
                return None
            url = (photos[0].get("src") or {}).get("large") or (photos[0].get("src") or {}).get("original")
            if not url:
                return None
            return await self._download(client, url, out_dir, suffix=".jpg")

    async def _fetch_pixabay(self, query: str, out_dir: Path) -> str | None:
        api_key = (self._settings.get("pixabay_api_key") or "").strip()
        if not api_key:
            return None
        import httpx

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                PIXABAY_ENDPOINT,
                params={
                    "key": api_key,
                    "q": query,
                    "image_type": "photo",
                    "per_page": 3,
                    "safesearch": "true",
                    "orientation": "horizontal",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits") or []
            if not hits:
                return None
            url = hits[0].get("largeImageURL") or hits[0].get("webformatURL")
            if not url:
                return None
            return await self._download(client, url, out_dir, suffix=".jpg")

    async def _gen_dashscope(self, query: str, out_dir: Path) -> str | None:
        api_key = (self._settings.get("dashscope_api_key") or "").strip()
        if not api_key:
            return None
        model = (self._settings.get("dashscope_image_model") or "wanx-v1").strip()
        # Optional relay-station base URL injected by the plugin layer's
        # _apply_relay_overrides — keeps the official DashScope host
        # when empty so existing deployments are unaffected.
        base_url = (
            (self._settings.get("dashscope_base_url") or "").strip().rstrip("/")
            or DASHSCOPE_DEFAULT_BASE_URL
        )
        submit_url = f"{base_url}{DASHSCOPE_T2I_SUBMIT_PATH}"
        query_url_tpl = f"{base_url}{DASHSCOPE_TASK_QUERY_PATH}"
        import httpx

        async with httpx.AsyncClient(timeout=60) as client:
            submit = await client.post(
                submit_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "X-DashScope-Async": "enable",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "input": {"prompt": query[:200]},
                    "parameters": {"size": "1280*720", "n": 1},
                },
            )
            submit.raise_for_status()
            task_id = (submit.json().get("output") or {}).get("task_id")
            if not task_id:
                return None
            for _ in range(30):
                await asyncio.sleep(2)
                poll = await client.get(
                    query_url_tpl.format(task_id=task_id),
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                poll.raise_for_status()
                payload = poll.json().get("output") or {}
                status = payload.get("task_status")
                if status in {"SUCCEEDED", "SUCCESS"}:
                    results = payload.get("results") or []
                    if not results:
                        return None
                    url = results[0].get("url")
                    if not url:
                        return None
                    return await self._download(client, url, out_dir, suffix=".png")
                if status in {"FAILED", "UNKNOWN", "CANCELED"}:
                    return None
            return None

    @staticmethod
    async def _download(
        client: Any, url: str, out_dir: Path, *, suffix: str
    ) -> str | None:
        resp = await client.get(url)
        if resp.status_code != 200:
            return None
        digest = abs(hash((url, suffix))) % (10**12)
        path = out_dir / f"asset_{digest:012d}{suffix}"
        path.write_bytes(resp.content)
        return str(path)

    def _image_dir(self, project_id: str) -> Path:
        return self._data_root / "projects" / project_id / "assets"
