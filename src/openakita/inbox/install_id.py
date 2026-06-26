from __future__ import annotations

import asyncio
import hashlib
import secrets
from pathlib import Path

import aiofiles


async def get_or_create_install_id_hash(data_dir: Path) -> str:
    await asyncio.to_thread(data_dir.mkdir, parents=True, exist_ok=True)
    path = data_dir / "install_id"
    try:
        async with aiofiles.open(path, encoding="utf-8") as f:
            value = (await f.read()).strip()
            if value:
                return hash_install_id(value)
    except Exception:
        pass

    install_id = secrets.token_urlsafe(32)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(install_id)
    return hash_install_id(install_id)


def hash_install_id(install_id: str) -> str:
    return hashlib.sha256(install_id.encode("utf-8")).hexdigest()
