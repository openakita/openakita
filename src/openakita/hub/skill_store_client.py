"""
SkillStoreClient — 与 OpenAkita Platform Skill Store 交互的客户端

功能：
- search: 搜索平台上的 Skill
- get_detail: 获取 Skill 详情
- install: 通过 installUrl 下载并安装 Skill 到本地
- rate: 为 Skill 评分
- submit_repo: 提交 GitHub 仓库供索引
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import json
from datetime import datetime, timezone

import asyncio
import random

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0

_RETRY_STATUS_CODES = {500, 502, 503, 504}
_MAX_RETRIES = 3
_BASE_BACKOFF = 1.0
_RATE_LIMIT_BACKOFF = 5.0


async def _retry_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int = _MAX_RETRIES,
    **kwargs,
) -> httpx.Response:
    """Execute an HTTP request with retry + exponential backoff for 5xx/timeout and 429."""
    last_exc: Exception | None = None
    last_resp: httpx.Response | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = await client.request(method, url, **kwargs)
            last_resp = resp
            if resp.status_code == 429:
                try:
                    retry_after = float(resp.headers.get("Retry-After", _RATE_LIMIT_BACKOFF))
                except (ValueError, TypeError):
                    retry_after = _RATE_LIMIT_BACKOFF
                wait = min(retry_after, 30.0) + random.uniform(0, 1)
                logger.warning("Rate limited (429) on %s, waiting %.1fs", url, wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code in _RETRY_STATUS_CODES and attempt < max_retries:
                wait = _BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "Server error %d on %s, retry %d/%d in %.1fs",
                    resp.status_code, url, attempt + 1, max_retries, wait,
                )
                await asyncio.sleep(wait)
                continue
            return resp
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_exc = e
            if attempt < max_retries:
                wait = _BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "Request to %s failed (%s), retry %d/%d in %.1fs",
                    url, type(e).__name__, attempt + 1, max_retries, wait,
                )
                await asyncio.sleep(wait)
            else:
                raise
    if last_resp is not None:
        return last_resp
    if last_exc is None:
        raise RuntimeError("All retry attempts exhausted")
    raise last_exc  # type: ignore[misc]


class SkillStoreClient:
    """Skill Store HTTP 客户端"""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.hub_api_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"User-Agent": f"OpenAkita/{self._get_version()}"}
        if settings.hub_api_key:
            headers["X-Akita-Key"] = settings.hub_api_key
        if settings.hub_device_id:
            headers["X-Akita-Device"] = settings.hub_device_id
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=DEFAULT_TIMEOUT,
                headers=self._auth_headers(),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _get_version() -> str:
        try:
            from .._bundled_version import __version__
            return __version__
        except Exception:
            return "dev"

    async def search(
        self,
        query: str = "",
        category: str = "",
        trust_level: str = "",
        sort: str = "installs",
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        client = await self._get_client()
        params: dict[str, Any] = {"page": str(page), "limit": str(limit), "sort": sort}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        if trust_level:
            params["trustLevel"] = trust_level

        resp = await _retry_request(client, "GET", "/skills", params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_detail(self, skill_id: str) -> dict[str, Any]:
        client = await self._get_client()
        resp = await _retry_request(client, "GET", f"/skills/{skill_id}")
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _write_origin(skill_dir: Path, install_url: str) -> None:
        """Write provenance files to track skill source."""
        try:
            origin = {
                "source": install_url,
                "type": "platform_store",
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                import re

                import yaml
                m = re.match(r"^---\s*\n(.*?)\n---", skill_md.read_text("utf-8"), re.DOTALL)
                if m:
                    fm = yaml.safe_load(m.group(1)) or {}
                    if fm.get("version"):
                        origin["version"] = fm["version"]
            (skill_dir / ".openakita-origin.json").write_text(
                json.dumps(origin, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Also write .openakita-source for compatibility with bridge/frontend matching
            (skill_dir / ".openakita-source").write_text(install_url, encoding="utf-8")
        except Exception as e:
            logger.debug(f"Failed to write origin tracking: {e}")

    async def install_skill(
        self,
        install_url: str,
        target_dir: Path | None = None,
        *,
        skill_id: str | None = None,
    ) -> Path:
        """安装 Skill 到本地

        优先从平台缓存下载 ZIP，失败时 fallback 到 git clone。
        install_url 格式: owner/repo@skill_name 或完整 git URL
        """
        if target_dir is None:
            target_dir = settings.skills_path

        target_dir.mkdir(parents=True, exist_ok=True)

        if "@" in install_url and "/" in install_url:
            repo_part, skill_name = install_url.rsplit("@", 1)
            if not repo_part.startswith("http"):
                repo_part = f"https://github.com/{repo_part}"
        else:
            repo_part = install_url
            skill_name = install_url.rsplit("/", 1)[-1]

        skill_dir = target_dir / skill_name
        if skill_dir.exists():
            logger.info(f"Skill {skill_name} already exists, updating...")
            shutil.rmtree(skill_dir)

        # Strategy 1: Download cached ZIP from platform
        if skill_id:
            try:
                installed = await self._install_from_platform_cache(skill_id, skill_name, skill_dir)
                if installed:
                    self._write_origin(skill_dir, install_url)
                    logger.info(f"Installed skill from platform cache: {skill_name} -> {skill_dir}")
                    return skill_dir
            except Exception as e:
                logger.debug(f"Platform cache download failed for {skill_id}: {e}")
                if skill_dir.exists():
                    shutil.rmtree(skill_dir, ignore_errors=True)

        # Ensure clean state: platform cache may have left a partial skill_dir
        if skill_dir.exists():
            shutil.rmtree(skill_dir, ignore_errors=True)

        # Strategy 2: git clone fallback
        try:
            installed = await self._install_via_git(repo_part, skill_name, skill_dir)
            if installed:
                self._write_origin(skill_dir, install_url)
                logger.info(f"Installed skill via git: {skill_name} -> {skill_dir}")
                return skill_dir
        except Exception as e:
            logger.debug(f"git clone failed for {skill_name}: {e}")
            if skill_dir.exists():
                shutil.rmtree(skill_dir, ignore_errors=True)

        raise RuntimeError(
            f"Failed to install skill '{skill_name}': "
            "neither platform cache nor git clone succeeded"
        )

    async def _install_from_platform_cache(
        self, skill_id: str, skill_name: str, skill_dir: Path
    ) -> bool:
        """Download cached ZIP from platform and extract."""
        import io
        import zipfile

        client = await self._get_client()
        resp = await _retry_request(
            client, "GET", f"/skills/{skill_id}/download",
            follow_redirects=True,
            timeout=60.0,
        )
        if resp.status_code != 200:
            return False

        data = resp.content
        if len(data) < 22:  # minimum ZIP size
            return False

        skill_dir.mkdir(parents=True, exist_ok=True)
        abs_target = str(skill_dir.resolve()) + os.sep
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                member_path = os.path.normpath(os.path.join(skill_dir.resolve(), member))
                if not member_path.startswith(abs_target) and member_path != abs_target.rstrip(os.sep):
                    raise RuntimeError(
                        f"Zip Slip detected: member '{member}' escapes target directory"
                    )
            zf.extractall(skill_dir)

        skill_md = skill_dir / "SKILL.md"
        return skill_md.exists()

    @staticmethod
    async def _install_via_git(repo_url: str, skill_name: str, skill_dir: Path) -> bool:
        """Clone from git, handling mono-repo structures.

        Many skill repos (e.g., inference-shell/skills) contain multiple skills
        in subdirectories. After cloning, we search for the skill_name subdirectory
        that contains SKILL.md, and only copy that to the target.
        """
        import tempfile

        git_exe = shutil.which("git")
        if git_exe is None:
            raise FileNotFoundError("git not found in PATH")

        extra_kwargs: dict = {}
        if sys.platform == "win32":
            extra_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        tmp_parent = Path(tempfile.mkdtemp(prefix="openakita_skill_"))
        tmp_dir = tmp_parent / "repo"
        try:
            result = subprocess.run(
                [git_exe, "clone", "--depth=1", repo_url, str(tmp_dir)],
                capture_output=True,
                text=True,
                timeout=120,
                **extra_kwargs,
            )
            if result.returncode != 0:
                raise RuntimeError(f"git clone failed: {result.stderr}")

            skill_md_at_root = tmp_dir / "SKILL.md"
            if skill_md_at_root.exists():
                shutil.copytree(str(tmp_dir), str(skill_dir))
                git_dir = skill_dir / ".git"
                if git_dir.exists():
                    shutil.rmtree(git_dir)
                return True

            candidates = [
                skill_name,
                f"skills/{skill_name}",
                f"tools/{skill_name}",
                f"packages/{skill_name}",
            ]
            seen: set[str] = set()
            for rel in candidates:
                rel_norm = rel.replace("\\", "/").strip("/")
                if not rel_norm or rel_norm in seen:
                    continue
                seen.add(rel_norm)
                candidate = tmp_dir / rel_norm
                if candidate.is_dir() and (candidate / "SKILL.md").exists():
                    shutil.copytree(str(candidate), str(skill_dir))
                    return True

            # Fallback: recursive search for SKILL.md with matching directory name
            for skill_md in tmp_dir.rglob("SKILL.md"):
                if skill_md.parent.name == skill_name:
                    shutil.copytree(str(skill_md.parent), str(skill_dir))
                    return True

            # Last resort: copy entire repo (backward-compatible)
            shutil.copytree(str(tmp_dir), str(skill_dir))
            git_dir = skill_dir / ".git"
            if git_dir.exists():
                shutil.rmtree(git_dir)
            return True
        finally:
            shutil.rmtree(str(tmp_parent), ignore_errors=True)

    async def rate(self, skill_id: str, score: int, comment: str = "", token: str = "") -> dict[str, Any]:
        client = await self._get_client()
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = await _retry_request(
            client, "POST", f"/skills/{skill_id}/rate",
            json={"score": score, "comment": comment},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def submit_repo(self, repo_url: str) -> dict[str, Any]:
        client = await self._get_client()
        resp = await _retry_request(
            client, "POST", "/skills/submit-repo",
            json={"repoUrl": repo_url},
        )
        resp.raise_for_status()
        return resp.json()
