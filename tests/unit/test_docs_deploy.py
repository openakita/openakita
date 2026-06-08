from __future__ import annotations

import json

from openakita.api import server


def _write_docs_dist(root, *, index: str = "hello") -> None:
    assets = root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(index, encoding="utf-8")
    (root / "hashmap.json").write_text("{}", encoding="utf-8")
    (root / "versions.html").write_text("<html>versions</html>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('docs')", encoding="utf-8")


def test_deploy_docs_reuses_matching_version_without_sync(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled-docs"
    _write_docs_dist(bundled)
    monkeypatch.setattr(server, "_find_docs_dist", lambda: bundled)

    docs_root = server._deploy_docs(tmp_path / "data", "1.2.3+local")

    assert docs_root == tmp_path / "data" / "docs"
    assert (docs_root / "v1.2.3" / "index.html").read_text("utf-8") == "hello"

    def fail_sync(*_args, **_kwargs):
        raise AssertionError("matching docs should not be redeployed")

    monkeypatch.setattr(server, "_sync_docs_tree", fail_sync)

    assert server._deploy_docs(tmp_path / "data", "1.2.3+local") == docs_root
    assert json.loads((docs_root / "versions.json").read_text("utf-8")) == ["1.2.3"]


def test_deploy_docs_refreshes_changed_version_and_removes_stale_files(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled-docs"
    _write_docs_dist(bundled)
    monkeypatch.setattr(server, "_find_docs_dist", lambda: bundled)

    docs_root = server._deploy_docs(tmp_path / "data", "1.2.3")
    version_dir = docs_root / "v1.2.3"
    stale_asset = version_dir / "assets" / "old.js"
    stale_asset.write_text("stale", encoding="utf-8")
    (bundled / "index.html").write_text("updated", encoding="utf-8")

    assert server._deploy_docs(tmp_path / "data", "1.2.3") == docs_root
    assert (version_dir / "index.html").read_text("utf-8") == "updated"
    assert not stale_asset.exists()


def test_deploy_docs_returns_existing_version_when_refresh_is_blocked(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled-docs"
    _write_docs_dist(bundled)
    monkeypatch.setattr(server, "_find_docs_dist", lambda: bundled)
    docs_root = server._deploy_docs(tmp_path / "data", "1.2.3")

    (bundled / "index.html").write_text("updated", encoding="utf-8")
    monkeypatch.setattr(
        server,
        "_sync_docs_tree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("locked")),
    )

    assert server._deploy_docs(tmp_path / "data", "1.2.3") == docs_root
    assert (docs_root / "v1.2.3" / "index.html").read_text("utf-8") == "hello"


def test_deploy_docs_returns_none_when_initial_deploy_is_blocked(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled-docs"
    _write_docs_dist(bundled)
    monkeypatch.setattr(server, "_find_docs_dist", lambda: bundled)
    monkeypatch.setattr(
        server,
        "_sync_docs_tree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("locked")),
    )

    assert server._deploy_docs(tmp_path / "data", "1.2.3") is None
