from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_git_proxy_validation_rejects_malformed_fullwidth_proxy(monkeypatch: pytest.MonkeyPatch):
    from openakita.setup_center import bridge

    monkeypatch.setenv("ALL_PROXY", "htpp：//127.0.0.1:7897")

    with pytest.raises(bridge.SkillInstallError) as exc:
        bridge._validate_git_proxy_environment(os.environ.copy())

    assert exc.value.code == "git_proxy_invalid"
    assert "代理配置格式错误" in exc.value.message


def test_install_github_repo_copies_from_temp_without_git_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from openakita.setup_center import bridge

    def fake_git_clone(args: list[str]) -> None:
        clone_dir = Path(args[-1])
        clone_dir.mkdir(parents=True)
        (clone_dir / ".git").mkdir()
        (clone_dir / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
        (clone_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: demo\n---\n# Demo\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(bridge, "_has_git", lambda: True)
    monkeypatch.setattr(bridge, "_git_clone", fake_git_clone)

    bridge.install_skill(str(tmp_path), "https://github.com/acme/demo")

    target = tmp_path / "skills" / "demo"
    assert (target / "SKILL.md").exists()
    assert not (target / ".git").exists()
    assert (target / ".openakita-source").read_text(encoding="utf-8") == (
        "https://github.com/acme/demo"
    )


def test_broken_residual_skill_dir_is_quarantined_when_delete_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from openakita.setup_center import bridge

    skills_dir = tmp_path / "skills"
    broken = skills_dir / "broken"
    broken.mkdir(parents=True)
    (broken / ".git").mkdir()

    def fail_remove(_: Path, *, retries: int = 3) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(bridge, "_remove_tree", fail_remove)
    bridge._ensure_target_available(broken, "github:owner/broken")

    assert not broken.exists()
    quarantined = list((skills_dir / ".openakita-broken").glob("broken-*"))
    assert len(quarantined) == 1
    assert (quarantined[0] / ".git").exists()


def test_shorthand_install_rechecks_target_after_failed_platform_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from openakita.setup_center import bridge

    def fake_platform_cache(_: str, dest_dir: Path) -> bool:
        dest_dir.mkdir(parents=True)
        (dest_dir / ".git").mkdir()
        return False

    def fake_git_clone(args: list[str]) -> None:
        clone_dir = Path(args[-1])
        skill_dir = clone_dir / "skills" / "demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: demo\ndescription: demo\n---\n# Demo\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(bridge, "_try_platform_skill_download", fake_platform_cache)
    monkeypatch.setattr(bridge, "_has_git", lambda: True)
    monkeypatch.setattr(bridge, "_git_clone", fake_git_clone)

    bridge.install_skill(str(tmp_path), "owner/repo@demo")

    target = tmp_path / "skills" / "demo"
    assert (target / "SKILL.md").exists()
    assert not (target / ".git").exists()
