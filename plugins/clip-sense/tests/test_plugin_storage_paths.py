"""Plugin storage path normalization (tilde → real dir for /storage/stats)."""

from __future__ import annotations

from pathlib import Path

from plugin import Plugin


def test_normalize_config_path_expands_tilde() -> None:
    raw = "~/clip-sense-output"
    out = Plugin._normalize_config_path(raw)
    assert not out.startswith("~")
    assert Path(out).is_absolute()
    assert Path(out).name == "clip-sense-output"


def test_normalize_config_path_absolute_unchanged_parent() -> None:
    home = str(Path.home())
    out = Plugin._normalize_config_path(home)
    assert Path(out).resolve() == Path(home).resolve()


def test_storage_defaults_uploads_placeholder_maps_to_data_dir(tmp_path: Path) -> None:
    p = Plugin.__new__(Plugin)
    p._data_dir = tmp_path / "plugin_data"
    d = Plugin._storage_defaults(
        p,
        {
            "uploads_dir": "<plugin-data>/uploads/",
            "output_dir": "",
            "tasks_dir": "",
        },
    )
    assert Path(d["uploads_dir"]) == p._data_dir / "uploads"
    assert Path(d["tasks_dir"]) == p._data_dir / "tasks"
    assert Path(d["output_dir"]) == p._data_dir / "tasks"


def test_storage_defaults_empty_output_uses_plugin_tasks_dir(tmp_path: Path) -> None:
    """Empty output_dir → same parent directory as legacy task layout."""
    p = Plugin.__new__(Plugin)
    p._data_dir = tmp_path / "plugin_data"
    d = Plugin._storage_defaults(p, {"output_dir": "", "uploads_dir": "", "tasks_dir": ""})
    assert Path(d["output_dir"]) == p._data_dir / "tasks"


def test_storage_defaults_rejects_localized_hint_as_output_dir(tmp_path: Path) -> None:
    """Blur-saved UI hint (Chinese) must not be treated as a filesystem path."""
    p = Plugin.__new__(Plugin)
    p._data_dir = tmp_path / "plugin_data"
    d = Plugin._storage_defaults(
        p,
        {
            "output_dir": "默认: <插件数据目录>/tasks/",
            "uploads_dir": "默认: <插件数据目录>/uploads/",
            "tasks_dir": "",
        },
    )
    assert Path(d["output_dir"]) == p._data_dir / "tasks"
    assert Path(d["uploads_dir"]) == p._data_dir / "uploads"


def test_storage_defaults_rejects_english_hint_as_output_dir(tmp_path: Path) -> None:
    p = Plugin.__new__(Plugin)
    p._data_dir = tmp_path / "plugin_data"
    d = Plugin._storage_defaults(
        p,
        {
            "output_dir": "Default: <plugin data dir>/tasks/",
            "uploads_dir": "",
            "tasks_dir": "",
        },
    )
    assert Path(d["output_dir"]) == p._data_dir / "tasks"
