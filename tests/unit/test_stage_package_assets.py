from __future__ import annotations

from pathlib import Path

import pytest

from scripts import stage_package_assets as stage_assets


def _write_dist(root: Path, marker: str) -> None:
    assets = root / "assets"
    assets.mkdir(parents=True)
    (root / "index.html").write_text(f"<html>{marker}</html>", encoding="utf-8")
    (assets / "app.js").write_text(f"console.log({marker!r})", encoding="utf-8")


def test_stage_package_assets_copies_web_and_docs_to_package_targets(tmp_path: Path) -> None:
    web_source = tmp_path / "web-source"
    docs_source = tmp_path / "docs-source"
    web_target = tmp_path / "package" / "web"
    docs_target = tmp_path / "package" / "docs_dist"
    _write_dist(web_source, "web")
    _write_dist(docs_source, "docs")

    result = stage_assets.stage_package_assets(
        web_source=web_source,
        docs_source=docs_source,
        web_target=web_target,
        docs_target=docs_target,
    )

    assert result == [("web frontend", 2), ("user docs", 2)]
    assert (web_target / "index.html").read_text(encoding="utf-8") == "<html>web</html>"
    assert (docs_target / "assets" / "app.js").read_text(encoding="utf-8") == (
        "console.log('docs')"
    )


def test_stage_package_assets_rejects_placeholder_only_assets(tmp_path: Path) -> None:
    web_source = tmp_path / "web-source"
    docs_source = tmp_path / "docs-source"
    web_source.mkdir()
    docs_source.mkdir()
    (web_source / ".keep").write_text("", encoding="utf-8")
    _write_dist(docs_source, "docs")

    with pytest.raises(RuntimeError, match="web frontend assets missing"):
        stage_assets.stage_package_assets(
            web_source=web_source,
            docs_source=docs_source,
            web_target=tmp_path / "web-target",
            docs_target=tmp_path / "docs-target",
        )


def test_clean_staged_assets_removes_package_targets(tmp_path: Path) -> None:
    web_target = tmp_path / "web"
    docs_target = tmp_path / "docs_dist"
    _write_dist(web_target, "web")
    _write_dist(docs_target, "docs")

    stage_assets.clean_staged_assets(web_target=web_target, docs_target=docs_target)

    assert not web_target.exists()
    assert not docs_target.exists()
