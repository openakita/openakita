from __future__ import annotations

from pathlib import Path
from typing import Any

from _plugin_loader import load_seedance_plugin
from fastapi import FastAPI
from fastapi.testclient import TestClient

_plugin = load_seedance_plugin()
Plugin = _plugin.Plugin


class _FakeAPI:
    def __init__(self, data_dir: Path, app: FastAPI) -> None:
        self._data_dir = data_dir
        self._app = app

    def get_data_dir(self) -> Path:
        return self._data_dir

    def register_api_routes(self, router: Any) -> None:
        self._app.include_router(router)

    def register_tools(self, tools: list[dict], **kwargs: Any) -> None:
        pass

    def spawn_task(self, coro: Any, **kwargs: Any) -> None:
        coro.close()

    def log(self, message: str) -> None:
        pass


class _FakeTM:
    def __init__(self, assets: list[dict]) -> None:
        self._assets = assets

    async def list_assets(
        self,
        asset_type: str | None = None,
        task_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict], int]:
        items = [a for a in self._assets if not asset_type or a["type"] == asset_type]
        return items[offset : offset + limit], len(items)

    async def get_asset(self, asset_id: str) -> dict | None:
        for asset in self._assets:
            if asset["id"] == asset_id:
                return asset
        return None


def test_assets_route_returns_browser_preview_url(tmp_path: Path) -> None:
    """The asset grid needs an HTTP URL; local file paths cannot render inside
    the plugin iframe."""

    app = FastAPI()
    plugin = Plugin()
    plugin.on_load(_FakeAPI(tmp_path, app))

    file_path = tmp_path / "uploads" / "images" / "forest.png"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"not-a-real-png")

    plugin._tm = _FakeTM(
        [
            {
                "id": "asset-1",
                "type": "image",
                "file_path": str(file_path),
                "original_name": "forest.png",
                "size_bytes": 14,
            }
        ]
    )

    response = TestClient(app).get("/assets")

    assert response.status_code == 200
    asset = response.json()["assets"][0]
    assert asset["preview_url"] == "/api/plugins/seedance-video/uploads/images/forest.png"


def test_assets_route_hides_preview_url_for_legacy_external_paths(tmp_path: Path) -> None:
    app = FastAPI()
    plugin = Plugin()
    plugin.on_load(_FakeAPI(tmp_path, app))
    plugin._tm = _FakeTM(
        [
            {
                "id": "legacy-1",
                "type": "image",
                "file_path": str(tmp_path.parent / "legacy.png"),
                "original_name": "legacy.png",
                "size_bytes": 1,
            }
        ]
    )

    response = TestClient(app).get("/assets")

    assert response.status_code == 200
    assert response.json()["assets"][0]["preview_url"] == ""


def test_asset_payload_returns_base64_for_upload_asset(tmp_path: Path) -> None:
    app = FastAPI()
    plugin = Plugin()
    plugin.on_load(_FakeAPI(tmp_path, app))

    file_path = tmp_path / "uploads" / "images" / "forest.png"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"image-bytes")

    plugin._tm = _FakeTM(
        [
            {
                "id": "asset-1",
                "type": "image",
                "file_path": str(file_path),
                "original_name": "forest.png",
                "size_bytes": 11,
            }
        ]
    )

    response = TestClient(app).get("/assets/asset-1/payload")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["kind"] == "image"
    assert body["original_name"] == "forest.png"
    assert body["preview_url"] == "/api/plugins/seedance-video/uploads/images/forest.png"
    assert body["base64"] == "data:image/png;base64,aW1hZ2UtYnl0ZXM="


def test_asset_payload_rejects_legacy_external_paths(tmp_path: Path) -> None:
    app = FastAPI()
    plugin = Plugin()
    plugin.on_load(_FakeAPI(tmp_path, app))
    plugin._tm = _FakeTM(
        [
            {
                "id": "legacy-1",
                "type": "image",
                "file_path": str(tmp_path.parent / "legacy.png"),
                "original_name": "legacy.png",
                "size_bytes": 1,
            }
        ]
    )

    response = TestClient(app).get("/assets/legacy-1/payload")

    assert response.status_code == 403


def test_asset_payload_returns_404_for_missing_asset(tmp_path: Path) -> None:
    app = FastAPI()
    plugin = Plugin()
    plugin.on_load(_FakeAPI(tmp_path, app))
    plugin._tm = _FakeTM([])

    response = TestClient(app).get("/assets/missing/payload")

    assert response.status_code == 404


def test_asset_payload_rejects_oversize_files(tmp_path: Path) -> None:
    app = FastAPI()
    plugin = Plugin()
    plugin.on_load(_FakeAPI(tmp_path, app))

    file_path = tmp_path / "uploads" / "videos" / "large.mp4"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"tiny")
    plugin._tm = _FakeTM(
        [
            {
                "id": "asset-1",
                "type": "video",
                "file_path": str(file_path),
                "original_name": "large.mp4",
                "size_bytes": 4,
            }
        ]
    )

    original_stat = Path.stat

    class _LargeStat:
        def __init__(self, original: Any) -> None:
            self._original = original

        st_size = 50 * 1024 * 1024 + 1

        def __getattr__(self, name: str) -> Any:
            return getattr(self._original, name)

    def fake_stat(self: Path):
        if self == file_path:
            return _LargeStat(original_stat(self))
        return original_stat(self)

    Path.stat = fake_stat
    try:
        response = TestClient(app).get("/assets/asset-1/payload")
    finally:
        Path.stat = original_stat

    assert response.status_code == 413
