from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openakita.tools.browser import manager


class _InstallProcess:
    def __init__(self, browsers_dir: Path, *, returncode: int = 0) -> None:
        self.returncode = returncode
        self._browsers_dir = browsers_dir
        self.killed = False

    async def communicate(self):
        if self.returncode == 0:
            (self._browsers_dir / "chromium-expected").mkdir(parents=True, exist_ok=True)
        return b"playwright install output", b""

    def kill(self) -> None:
        self.killed = True


def test_managed_browsers_dir_respects_openakita_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setenv("OPENAKITA_ROOT", str(tmp_path))

    assert manager._managed_browsers_dir() == tmp_path / "modules" / "browser" / "browsers"


@pytest.mark.asyncio
async def test_download_managed_chromium_uses_bundled_driver_cli(
    tmp_path: Path, monkeypatch
) -> None:
    browsers_dir = tmp_path / "browsers"
    calls = []

    async def fake_subprocess(*args, **kwargs):
        calls.append((args, kwargs))
        return _InstallProcess(browsers_dir)

    monkeypatch.setattr(
        "playwright._impl._driver.compute_driver_executable",
        lambda: (tmp_path / "node", tmp_path / "cli.js"),
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

    await manager._download_managed_chromium(browsers_dir)

    args, kwargs = calls[0]
    assert args[-3:] == ("install", "--no-shell", "chromium")
    assert kwargs["env"]["PLAYWRIGHT_BROWSERS_PATH"] == str(browsers_dir)
    assert (browsers_dir / "chromium-expected").is_dir()


@pytest.mark.asyncio
async def test_download_managed_chromium_reports_installer_failure(
    tmp_path: Path, monkeypatch
) -> None:
    browsers_dir = tmp_path / "browsers"

    monkeypatch.setattr(
        "playwright._impl._driver.compute_driver_executable",
        lambda: (tmp_path / "node", tmp_path / "cli.js"),
    )
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        lambda *args, **kwargs: _async_value(_InstallProcess(browsers_dir, returncode=1)),
    )

    with pytest.raises(RuntimeError, match="Chromium download failed"):
        await manager._download_managed_chromium(browsers_dir)


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_missing_chromium_downloads_then_restarts_driver(tmp_path: Path, monkeypatch) -> None:
    missing_executable = tmp_path / "missing" / "chrome.exe"
    installed_executable = tmp_path / "browsers" / "chromium-current" / "chrome.exe"
    installed_executable.parent.mkdir(parents=True)
    installed_executable.write_bytes(b"x" * 1_100_000)

    browser_type = SimpleNamespace(executable_path=str(missing_executable))
    browser_manager = object.__new__(manager.BrowserManager)
    browser_manager._bundled_executable = None
    browser_manager._playwright = SimpleNamespace(chromium=browser_type)
    browser_manager._chromium_install_error = None
    browser_manager._chromium_install_allowed = True
    browser_manager.chromium_install_required = False
    browser_manager._is_server = False
    browser_manager._cleanup_playwright = AsyncMock()
    browser_manager._launch_persistent = AsyncMock(return_value=True)
    browser_manager._launch_standard = AsyncMock(return_value=False)

    async def restart_driver() -> bool:
        browser_type.executable_path = str(installed_executable)
        return True

    browser_manager._start_playwright_driver = restart_driver
    download = AsyncMock()
    monkeypatch.setattr(manager, "_managed_browsers_dir", lambda: tmp_path / "browsers")
    monkeypatch.setattr(manager, "_download_managed_chromium", download)
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    assert await browser_manager._try_bundled_chromium(headless=True)
    download.assert_awaited_once_with(tmp_path / "browsers")
    browser_manager._cleanup_playwright.assert_awaited_once()
    browser_manager._launch_persistent.assert_awaited_once_with(None, True)
    assert manager.os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "browsers")


@pytest.mark.asyncio
async def test_missing_chromium_requires_confirmation_without_downloading(
    tmp_path: Path, monkeypatch
) -> None:
    browser_manager = object.__new__(manager.BrowserManager)
    browser_manager._bundled_executable = None
    browser_manager._playwright = SimpleNamespace(
        chromium=SimpleNamespace(executable_path=str(tmp_path / "missing" / "chrome.exe"))
    )
    browser_manager._chromium_install_error = None
    browser_manager._chromium_install_allowed = False
    browser_manager.chromium_install_required = False
    browser_manager._is_server = False
    download = AsyncMock()
    monkeypatch.setattr(manager, "_download_managed_chromium", download)

    with pytest.raises(RuntimeError, match="请先询问用户是否下载安装"):
        await browser_manager._try_bundled_chromium(headless=True)

    assert browser_manager.chromium_install_required is True
    download.assert_not_awaited()


def test_packaging_excludes_chromium_but_keeps_playwright_driver() -> None:
    root = Path(__file__).parents[2]
    spec = (root / "build" / "openakita.spec").read_text(encoding="utf-8")
    backend = (root / "build" / "build_backend.py").read_text(encoding="utf-8")

    assert 'datas.append((str(_pw_driver_dir), "playwright/driver"))' in spec
    assert "_pw_browser_dir" not in spec
    assert "Bundling Playwright Chromium" not in spec
    assert '".local-browsers" not in entry[0]' in spec
    assert 'scripts" / "pyinstaller_hooks' in spec
    assert "ensure_playwright_chromium" not in backend
    assert '"playwright", "install", "chromium"' not in backend

    hook_dir = root / "scripts" / "pyinstaller_hooks"
    for api in ("async_api", "sync_api"):
        hook = (hook_dir / f"hook-playwright.{api}.py").read_text(encoding="utf-8")
        assert "collect_data_files" not in hook
        assert "datas = []" in hook
