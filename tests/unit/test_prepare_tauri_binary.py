from pathlib import Path

import scripts.prepare_tauri_binary as module


def test_prepare_binary_copies_cargo_name_to_tauri_main_binary(tmp_path: Path, monkeypatch) -> None:
    tauri_dir = tmp_path / "src-tauri"
    release_dir = tauri_dir / "target" / "release"
    release_dir.mkdir(parents=True)
    (tauri_dir / "tauri.conf.json").write_text(
        '{"mainBinaryName":"openakita-desktop"}', encoding="utf-8"
    )
    suffix = ".exe" if module.sys.platform == "win32" else ""
    source = release_dir / f"openakita-setup-center{suffix}"
    source.write_bytes(b"cargo-binary")
    monkeypatch.setattr(module, "TAURI_DIR", tauri_dir)

    destination = module.prepare_binary()

    assert destination.name == f"openakita-desktop{suffix}"
    assert destination.read_bytes() == b"cargo-binary"


def test_prepare_binary_accepts_restored_destination(tmp_path: Path, monkeypatch) -> None:
    tauri_dir = tmp_path / "src-tauri"
    release_dir = tauri_dir / "target" / "custom-target" / "release"
    release_dir.mkdir(parents=True)
    (tauri_dir / "tauri.conf.json").write_text(
        '{"mainBinaryName":"openakita-desktop"}', encoding="utf-8"
    )
    suffix = ".exe" if module.sys.platform == "win32" else ""
    expected = release_dir / f"openakita-desktop{suffix}"
    expected.write_bytes(b"cached-binary")
    monkeypatch.setattr(module, "TAURI_DIR", tauri_dir)

    assert module.prepare_binary("custom-target") == expected
