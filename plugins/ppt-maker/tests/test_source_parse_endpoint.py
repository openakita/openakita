from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _StubAPI:
    """Minimal PluginAPI stub that captures the router and exposes data_dir."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self.router = None

    def get_data_dir(self) -> Path:
        return self._data_dir

    def register_api_routes(self, router) -> None:
        self.router = router

    def register_tools(self, *args: Any, **kwargs: Any) -> None:
        return None

    def log(self, *args: Any, **kwargs: Any) -> None:
        return None


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    import plugin

    instance = plugin.Plugin()
    stub_api = _StubAPI(tmp_path)
    instance.on_load(stub_api)
    assert stub_api.router is not None
    app = FastAPI()
    app.include_router(stub_api.router)
    return TestClient(app)


def test_parse_source_persists_full_text_and_metadata(tmp_path: Path, client: TestClient) -> None:
    upload_path = tmp_path / "uploads" / "brief.md"
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_text(
        "# 季度回顾\n\n本季度销售额突破 1000 万，同比增长 18%。\n\n## 风险\n\n供应链周期拉长。",
        encoding="utf-8",
    )

    with upload_path.open("rb") as buf:
        response = client.post(
            "/upload",
            files={"file": ("brief.md", buf, "text/markdown")},
            data={"collection_name": "Q2 复盘"},
        )
    assert response.status_code == 200, response.text
    source = response.json()["source"]
    assert source["status"] == "uploaded"

    parse_response = client.post(f"/sources/{source['id']}/parse")
    assert parse_response.status_code == 200, parse_response.text
    payload = parse_response.json()
    parsed_path = Path(payload["parsed"]["parsed_path"])
    text_path = Path(payload["parsed"]["parsed_text_path"])

    assert parsed_path.exists()
    assert text_path.exists()
    full_text = text_path.read_text(encoding="utf-8")
    assert "季度回顾" in full_text
    assert "供应链周期拉长" in full_text

    updated_source = payload["source"]
    assert updated_source["status"] == "parsed"
    metadata = updated_source["metadata"]
    assert metadata["parsed_path"] == str(parsed_path)
    assert metadata["parsed_text_path"] == str(text_path)
    assert metadata["parsed"]["text_length"] == len(full_text)


def test_delete_source_with_files_cleans_up_parsed_artifacts(
    tmp_path: Path, client: TestClient
) -> None:
    upload_path = tmp_path / "uploads" / "notes.md"
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_text("一段非常短的资料文本。", encoding="utf-8")

    with upload_path.open("rb") as buf:
        upload_response = client.post(
            "/upload",
            files={"file": ("notes.md", buf, "text/markdown")},
            data={"collection_name": "调研"},
        )
    source = upload_response.json()["source"]

    parsed = client.post(f"/sources/{source['id']}/parse").json()["parsed"]
    parsed_path = Path(parsed["parsed_path"])
    text_path = Path(parsed["parsed_text_path"])
    assert parsed_path.exists() and text_path.exists()

    delete_response = client.request(
        "DELETE",
        f"/sources/{source['id']}",
        json={"delete_files": True},
    )
    assert delete_response.status_code == 200, delete_response.text
    body = delete_response.json()
    assert body["deleted"] is True
    deleted_files = set(body["files_deleted"])
    assert str(parsed_path) in deleted_files
    assert str(text_path) in deleted_files
    assert not parsed_path.exists()
    assert not text_path.exists()
