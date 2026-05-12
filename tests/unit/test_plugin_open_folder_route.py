from fastapi import FastAPI
from fastapi.testclient import TestClient
from zipfile import ZipFile

from openakita.api.routes.plugins import router
from openakita.config import settings


def test_open_plugin_folder_returns_nested_payload(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "data" / "plugins" / "avatar-studio"
    plugin_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "project_root", str(tmp_path))

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/plugins/avatar-studio/_admin/open-folder")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["path"] == str(plugin_dir.resolve())


def test_export_plugin_returns_zip_payload(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "data" / "plugins" / "avatar-studio"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text("id: avatar-studio\nname: Avatar Studio\n", encoding="utf-8")
    (plugin_dir / "README.md").write_text("# Avatar Studio\n", encoding="utf-8")
    monkeypatch.setattr(settings, "project_root", str(tmp_path))

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/plugins/avatar-studio/_admin/export")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="avatar-studio.zip"' in response.headers["content-disposition"]

    zip_path = tmp_path / "avatar-studio.zip"
    zip_path.write_bytes(response.content)
    with ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "avatar-studio/plugin.yaml" in names
    assert "avatar-studio/README.md" in names
