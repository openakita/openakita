"""Unit tests for the F-4 §A-4 migration script.

Verifies:
  * Dry-run is the default and produces zero filesystem mutations.
  * --apply renames CJK-stem files to slug-stem files.
  * --apply writes _aliases.json with the legacy-id -> new-id mapping.
  * Idempotency: running --apply a second time on the now-clean
    directory exits 0 with "nothing to do".
  * Collision case: a pre-existing target file is skipped (the source
    file is NOT renamed, no data loss).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "migrate_non_ascii_template_ids.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    # Force the child Python to encode stdout/stderr as utf-8 so this
    # test capture works on Windows (where the default console codepage
    # is cp936/gbk and CJK template stems otherwise crash the parent
    # reader thread with UnicodeDecodeError, leaving r.stdout = None).
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _seed(td: Path) -> None:
    (td / "ascii-name.json").write_text(
        json.dumps({"name": "Ascii", "nodes": []}),
        encoding="utf-8",
    )
    (td / "内容运营团队.json").write_text(
        json.dumps({"name": "内容运营团队", "nodes": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_dry_run_is_default_no_mutation(tmp_path: Path) -> None:
    _seed(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    r = _run("--templates-dir", str(tmp_path))
    assert r.returncode == 0, r.stderr
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after, f"dry-run mutated tree: {before} -> {after}"
    assert "内容运营团队.json" in r.stdout
    assert "Run again with --apply" in r.stdout


def test_apply_renames_and_writes_alias(tmp_path: Path) -> None:
    _seed(tmp_path)
    r = _run("--templates-dir", str(tmp_path), "--apply")
    assert r.returncode == 0, r.stderr

    files = sorted(p.name for p in tmp_path.iterdir())
    assert "_aliases.json" in files
    assert "ascii-name.json" in files
    assert not any(name == "内容运营团队.json" for name in files), files
    # Some slug-stem file should exist (deterministic but we do not pin the
    # exact digest here; the slug helper has its own unit tests).
    slug_files = [n for n in files if n.startswith("tpl-") and n.endswith(".json")]
    assert len(slug_files) == 1, files

    aliases = json.loads((tmp_path / "_aliases.json").read_text(encoding="utf-8"))
    assert "内容运营团队" in aliases
    # The alias target stem (without .json) matches the actual renamed file.
    assert aliases["内容运营团队"] + ".json" == slug_files[0]


def test_apply_idempotent_after_first_run(tmp_path: Path) -> None:
    _seed(tmp_path)
    r1 = _run("--templates-dir", str(tmp_path), "--apply")
    assert r1.returncode == 0
    r2 = _run("--templates-dir", str(tmp_path), "--apply")
    assert r2.returncode == 0
    assert "nothing to do" in r2.stdout.lower(), r2.stdout


def test_collision_skips_source_no_data_loss(tmp_path: Path) -> None:
    # Pre-plant the target slug so the rename collides.
    from openakita.orgs._slug import slugify_template_id

    legacy = "内容运营团队"
    target_slug = slugify_template_id(legacy)
    (tmp_path / f"{legacy}.json").write_text(
        json.dumps({"name": legacy, "nodes": ["src"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / f"{target_slug}.json").write_text(
        json.dumps({"name": "preexisting", "nodes": ["target"]}),
        encoding="utf-8",
    )
    r = _run("--templates-dir", str(tmp_path), "--apply")
    assert r.returncode == 0, r.stderr
    # Source file must still exist (rename skipped).
    src_p = tmp_path / f"{legacy}.json"
    assert src_p.is_file(), "collision case lost the source file!"
    # Pre-existing target unchanged.
    target_p = tmp_path / f"{target_slug}.json"
    assert target_p.is_file()
    target_content = json.loads(target_p.read_text(encoding="utf-8"))
    assert target_content["name"] == "preexisting"
    # No alias was written for the skipped entry.
    alias_p = tmp_path / "_aliases.json"
    if alias_p.is_file():
        aliases = json.loads(alias_p.read_text(encoding="utf-8"))
        assert legacy not in aliases
    assert "skip" in r.stdout.lower(), r.stdout


def test_apply_on_empty_dir_is_noop(tmp_path: Path) -> None:
    r = _run("--templates-dir", str(tmp_path), "--apply")
    assert r.returncode == 0, r.stderr
    assert "nothing to do" in r.stdout.lower()
    assert list(tmp_path.iterdir()) == []
