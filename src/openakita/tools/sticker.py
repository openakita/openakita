"""
Sticker Engine

Based on the ChineseBQB open-source sticker library, provides keyword search,
mood mapping, local caching, and sending functionality.

Data source: https://github.com/zhaoolee/ChineseBQB
JSON index: https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/chinesebqb_github.json

Send pipeline: search -> download_and_cache -> deliver_artifacts(type="image")
"""

import hashlib
import json
import logging
import random
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Mood-keyword mapping ───────────────────────────────────────────────

MOOD_KEYWORDS = {
    "happy": ["开心", "高兴", "哈哈", "笑", "鼓掌", "庆祝", "耶", "棒"],
    "sad": ["难过", "伤心", "哭", "可怜", "委屈", "泪"],
    "angry": ["生气", "愤怒", "菜刀", "打人", "暴怒", "摔"],
    "greeting": ["你好", "早安", "晚安", "问好", "招手", "嗨"],
    "encourage": ["加油", "棒", "厉害", "优秀", "tql", "冲", "赞"],
    "love": ["爱心", "心心", "比心", "送你", "花", "爱", "亲亲"],
    "tired": ["累", "困", "摸鱼", "划水", "上吊", "要饭", "躺平", "摆烂"],
    "surprise": ["震惊", "惊吓", "天哪", "不是吧", "卧槽", "吃惊"],
}


class StickerEngine:
    """Sticker Engine"""

    INDEX_URL = (
        "https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/chinesebqb_github.json"
    )
    _GITHUB_RAW_PREFIX = "https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/"

    # Built-in mirror list: GitHub proxies (China-friendly) + CDN mirrors
    # Each entry + relative path yields the full URL (proxy entries already include the original prefix)
    _BUILTIN_MIRRORS: list[str] = [
        "https://ghp.ci/https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/",
        "https://gh-proxy.com/https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/",
        "https://cdn.jsdelivr.net/gh/zhaoolee/ChineseBQB@master/",
        "https://raw.gitmirror.com/zhaoolee/ChineseBQB/master/",
    ]

    def __init__(self, data_dir: Path | str, mirrors: list[str] | None = None):
        self.data_dir = Path(data_dir) if not isinstance(data_dir, Path) else data_dir
        self.index_file = self.data_dir / "chinesebqb_index.json"
        self.cache_dir = self.data_dir / "cache"
        self._stickers: list[dict] = []
        self._keyword_index: dict[str, list[int]] = {}  # keyword -> [sticker indices]
        self._category_index: dict[str, list[int]] = {}  # category -> [sticker indices]
        self._initialized = False

        # User-configured mirrors first, then built-in mirrors (deduplicated, order-preserving)
        seen: set[str] = set()
        self._mirrors: list[str] = []
        for m in list(mirrors or []) + self._BUILTIN_MIRRORS:
            if m not in seen:
                seen.add(m)
                self._mirrors.append(m)

    async def initialize(self) -> bool:
        """
        Initialize: load index + build keyword mapping.

        Downloads the index if no local copy exists.
        """
        if self._initialized:
            return True

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Load local index
        if self.index_file.exists():
            try:
                data = json.loads(self.index_file.read_text(encoding="utf-8"))
                self._stickers = self._extract_sticker_list(data)
                self._build_indices()
                self._initialized = True
                logger.info(f"Sticker engine initialized: {len(self._stickers)} stickers loaded")
                return True
            except Exception as e:
                logger.warning(f"Failed to load local sticker index: {e}")

        # Attempt to download the index
        success = await self._download_index()
        if success:
            self._build_indices()
            self._initialized = True
            logger.info(f"Sticker engine initialized: {len(self._stickers)} stickers from remote")
        else:
            logger.warning("Sticker engine initialization failed: no index available")

        return self._initialized

    @staticmethod
    def _extract_sticker_list(data) -> list[dict]:
        """Extract sticker list from JSON data, compatible with multiple formats."""
        # ChineseBQB format: {"status": 1000, "info": "...", "data": [...]}
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # Try "data" key first (ChineseBQB official format)
            if "data" in data:
                return data["data"] if isinstance(data["data"], list) else []
            # Fallback "stickers" key
            if "stickers" in data:
                return data["stickers"] if isinstance(data["stickers"], list) else []
        return []

    async def _download_index(self) -> bool:
        """Download ChineseBQB index JSON, automatically trying mirrors."""
        index_urls = [self.INDEX_URL]
        relative = "chinesebqb_github.json"
        for mirror in self._mirrors:
            index_urls.append(mirror + relative)

        for url in index_urls:
            content = await self._download_bytes(url, timeout=30)
            if content:
                try:
                    data = json.loads(content)
                    self._stickers = self._extract_sticker_list(data)
                    self.index_file.write_text(
                        json.dumps(data, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    return True
                except (json.JSONDecodeError, Exception) as e:
                    logger.warning(f"Failed to parse sticker index from {url}: {e}")

        logger.warning("Failed to download sticker index: all mirrors exhausted")
        return False

    def _build_indices(self) -> None:
        """Build keyword and category indices from sticker data."""
        self._keyword_index.clear()
        self._category_index.clear()

        for idx, sticker in enumerate(self._stickers):
            name = sticker.get("name", "")
            category = sticker.get("category", "")

            # Category index
            if category:
                # Extract Chinese category name
                cat_cn = re.sub(r"^\d+\w*_", "", category)
                if cat_cn not in self._category_index:
                    self._category_index[cat_cn] = []
                self._category_index[cat_cn].append(idx)

            # Extract keywords from filename
            # Example format: "滑稽大佬00012-鼓掌.gif"
            # Remove extension
            base_name = re.sub(r"\.\w+$", "", name)
            # Split by - or _
            parts = re.split(r"[-_]", base_name)
            for part in parts:
                # Extract Chinese character sequences
                cn_matches = re.findall(r"[\u4e00-\u9fff]+", part)
                for kw in cn_matches:
                    if len(kw) >= 1:
                        if kw not in self._keyword_index:
                            self._keyword_index[kw] = []
                        self._keyword_index[kw].append(idx)

        logger.debug(
            f"Indices built: {len(self._keyword_index)} keywords, "
            f"{len(self._category_index)} categories"
        )

    async def search(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """
        Keyword search for stickers (with relevance scoring).

        Match priority: exact match > query is substring of kw > kw is substring of query (len>=2) > single-char fallback

        Args:
            query: Search keyword
            category: Optional category filter
            limit: Maximum number of results

        Returns:
            List of matching sticker info dicts [{"name", "category", "url"}, ...]
        """
        if not self._initialized:
            await self.initialize()

        if not self._stickers:
            return []

        scored: dict[int, float] = {}

        for kw, indices in self._keyword_index.items():
            if kw == query:
                score = 3.0
            elif query in kw:
                score = 2.0
            elif kw in query and len(kw) >= 2:
                score = 1.0
            else:
                continue
            for idx in indices:
                scored[idx] = max(scored.get(idx, 0), score)

        if not scored:
            for char in query:
                if char in self._keyword_index:
                    for idx in self._keyword_index[char]:
                        scored[idx] = max(scored.get(idx, 0), 0.5)

        # Category filter
        if category:
            cat_indices = set()
            for cat_name, indices in self._category_index.items():
                if category in cat_name or cat_name in category:
                    cat_indices.update(indices)
            if cat_indices:
                scored = {idx: s for idx, s in scored.items() if idx in cat_indices}
                if not scored:
                    for idx in cat_indices:
                        scored[idx] = 0.1

        # Sort by score (highest first), randomize within same score tier
        sorted_indices = sorted(
            scored.keys(),
            key=lambda i: (-scored[i], random.random()),
        )

        results = [self._stickers[i] for i in sorted_indices if i < len(self._stickers)]
        return results[:limit]

    async def get_random_by_mood(self, mood: str) -> dict | None:
        """
        Get a random sticker by mood.

        Args:
            mood: Mood type (happy/sad/angry/greeting/encourage/love/tired/surprise)

        Returns:
            Sticker info dict or None
        """
        keywords = MOOD_KEYWORDS.get(mood, [])
        if not keywords:
            return None

        # Collect all matching stickers
        all_candidates: list[dict] = []
        for kw in keywords:
            results = await self.search(kw, limit=10)
            all_candidates.extend(results)

        if not all_candidates:
            return None

        return random.choice(all_candidates)

    async def download_and_cache(self, url: str) -> Path | None:
        """
        Download sticker to local cache.

        Args:
            url: Sticker URL

        Returns:
            Local cache file path or None
        """
        url_hash = hashlib.md5(url.encode()).hexdigest()
        ext = url.rsplit(".", 1)[-1] if "." in url else "gif"
        cache_path = self.cache_dir / f"{url_hash}.{ext}"

        if cache_path.exists():
            return cache_path

        urls_to_try = [url]
        if url.startswith(self._GITHUB_RAW_PREFIX):
            relative = url[len(self._GITHUB_RAW_PREFIX) :]
            for mirror in self._mirrors:
                urls_to_try.append(mirror + relative)

        for attempt_url in urls_to_try:
            content = await self._download_bytes(attempt_url)
            if content:
                cache_path.write_bytes(content)
                return cache_path

        logger.warning(f"Failed to download sticker from {url}: all mirrors exhausted")
        return None

    @staticmethod
    async def _download_bytes(url: str, timeout: float = 15) -> bytes | None:
        """Try to download URL content, returns bytes or None."""
        try:
            try:
                import aiohttp

                async with (
                    aiohttp.ClientSession() as session,
                    session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
                ):
                    if resp.status == 200:
                        return await resp.read()
            except ImportError:
                import httpx
                from ..llm.providers.proxy_utils import get_httpx_client_kwargs

                async with httpx.AsyncClient(
                    **get_httpx_client_kwargs(timeout=timeout),
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return resp.content
        except Exception:
            pass
        return None
