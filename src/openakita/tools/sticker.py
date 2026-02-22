"""
表情包引擎 (Sticker Engine)

基于 ChineseBQB 开源表情包库，提供关键词搜索、情绪映射、本地缓存和发送功能。

数据源: https://github.com/zhaoolee/ChineseBQB
JSON 索引: https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/chinesebqb_github.json

发送链路: search -> download_and_cache -> deliver_artifacts(type="image")
"""

import hashlib
import json
import logging
import random
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 情绪-关键词映射 ───────────────────────────────────────────────

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
    """表情包引擎"""

    INDEX_URL = "https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/chinesebqb_github.json"
    _MIRRORS = [
        "https://cdn.jsdelivr.net/gh/zhaoolee/ChineseBQB@master/",
        "https://raw.gitmirror.com/zhaoolee/ChineseBQB/master/",
    ]
    _GITHUB_RAW_PREFIX = "https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/"

    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir) if not isinstance(data_dir, Path) else data_dir
        self.index_file = self.data_dir / "chinesebqb_index.json"
        self.cache_dir = self.data_dir / "cache"
        self._stickers: list[dict] = []
        self._keyword_index: dict[str, list[int]] = {}  # keyword -> [sticker indices]
        self._category_index: dict[str, list[int]] = {}  # category -> [sticker indices]
        self._initialized = False

    async def initialize(self) -> bool:
        """
        初始化：加载索引 + 构建关键词映射

        如果本地索引不存在，则尝试下载。
        """
        if self._initialized:
            return True

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 加载本地索引
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

        # 尝试下载索引
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
        """从 JSON 数据中提取 sticker 列表，兼容多种格式"""
        # ChineseBQB 格式: {"status": 1000, "info": "...", "data": [...]}
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # 优先尝试 "data" 键（ChineseBQB 官方格式）
            if "data" in data:
                return data["data"] if isinstance(data["data"], list) else []
            # 备选 "stickers" 键
            if "stickers" in data:
                return data["stickers"] if isinstance(data["stickers"], list) else []
        return []

    async def _download_index(self) -> bool:
        """下载 ChineseBQB 索引 JSON，自动尝试镜像。"""
        index_urls = [self.INDEX_URL]
        relative = "chinesebqb_github.json"
        for mirror in self._MIRRORS:
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
        """从 sticker 数据构建关键词和分类索引"""
        self._keyword_index.clear()
        self._category_index.clear()

        for idx, sticker in enumerate(self._stickers):
            name = sticker.get("name", "")
            category = sticker.get("category", "")

            # 分类索引
            if category:
                # 提取中文分类名
                cat_cn = re.sub(r"^\d+\w*_", "", category)
                if cat_cn not in self._category_index:
                    self._category_index[cat_cn] = []
                self._category_index[cat_cn].append(idx)

            # 从文件名提取关键词
            # 格式示例: "滑稽大佬00012-鼓掌.gif"
            # 去掉扩展名
            base_name = re.sub(r"\.\w+$", "", name)
            # 按 - 分割
            parts = re.split(r"[-_]", base_name)
            for part in parts:
                # 提取中文字符序列
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
        关键词搜索表情包

        Args:
            query: 搜索关键词
            category: 可选分类限制
            limit: 返回数量上限

        Returns:
            匹配的 sticker 信息列表 [{"name", "category", "url"}, ...]
        """
        if not self._initialized:
            await self.initialize()

        if not self._stickers:
            return []

        # 收集候选 sticker 索引
        candidate_indices: set[int] = set()

        # 关键词匹配
        for kw, indices in self._keyword_index.items():
            if query in kw or kw in query:
                candidate_indices.update(indices)

        # 如果没有找到，尝试单字匹配
        if not candidate_indices:
            for char in query:
                if char in self._keyword_index:
                    candidate_indices.update(self._keyword_index[char])

        # 分类过滤
        if category:
            cat_indices = set()
            for cat_name, indices in self._category_index.items():
                if category in cat_name or cat_name in category:
                    cat_indices.update(indices)
            if cat_indices:
                if candidate_indices:
                    candidate_indices &= cat_indices
                else:
                    candidate_indices = cat_indices

        # 转换为 sticker 列表
        results = [self._stickers[i] for i in candidate_indices if i < len(self._stickers)]

        # 随机选取
        if len(results) > limit:
            results = random.sample(results, limit)

        return results

    async def get_random_by_mood(self, mood: str) -> dict | None:
        """
        按情绪随机获取一张表情包

        Args:
            mood: 情绪类型 (happy/sad/angry/greeting/encourage/love/tired/surprise)

        Returns:
            sticker 信息 或 None
        """
        keywords = MOOD_KEYWORDS.get(mood, [])
        if not keywords:
            return None

        # 收集所有匹配的 sticker
        all_candidates: list[dict] = []
        for kw in keywords:
            results = await self.search(kw, limit=10)
            all_candidates.extend(results)

        if not all_candidates:
            return None

        return random.choice(all_candidates)

    async def download_and_cache(self, url: str) -> Path | None:
        """
        下载表情包到本地缓存

        Args:
            url: 表情包 URL

        Returns:
            本地缓存文件路径 或 None
        """
        url_hash = hashlib.md5(url.encode()).hexdigest()
        ext = url.rsplit(".", 1)[-1] if "." in url else "gif"
        cache_path = self.cache_dir / f"{url_hash}.{ext}"

        if cache_path.exists():
            return cache_path

        urls_to_try = [url]
        if url.startswith(self._GITHUB_RAW_PREFIX):
            relative = url[len(self._GITHUB_RAW_PREFIX):]
            for mirror in self._MIRRORS:
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
        """尝试下载 URL 内容，返回 bytes 或 None。"""
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

                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return resp.content
        except Exception:
            pass
        return None
