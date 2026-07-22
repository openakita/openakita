from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
MOBILE = ROOT / ".github" / "workflows" / "mobile.yml"
PREPARE = ROOT / ".github" / "actions" / "desktop-build-prepare" / "action.yml"


def test_release_workflows_never_clobber_existing_assets() -> None:
    for path in (RELEASE, MOBILE):
        source = path.read_text(encoding="utf-8")
        assert "gh release upload" in source
        assert "--clobber" not in source


def test_release_workflows_enforce_release_contract() -> None:
    release_source = RELEASE.read_text(encoding="utf-8")
    mobile_source = MOBILE.read_text(encoding="utf-8")

    assert "scripts/release_contract.py" in release_source
    assert "scripts/release_contract.py" in mobile_source
    assert "--require-release" in mobile_source
    assert "--expected-commit" in release_source
    assert "--expected-commit" in mobile_source


def test_mobile_release_waits_for_draft_creation_without_an_independent_tag_trigger() -> None:
    release_workflow = yaml.load(RELEASE.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    mobile_workflow = yaml.load(MOBILE.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    mobile_triggers = mobile_workflow["on"]
    assert "workflow_call" in mobile_triggers
    assert "workflow_dispatch" in mobile_triggers
    assert "push" not in mobile_triggers

    mobile_job = release_workflow["jobs"]["mobile_release"]
    assert mobile_job["needs"] == ["create_release"]
    assert mobile_job["permissions"] == {"contents": "write"}
    assert mobile_job["uses"] == "./.github/workflows/mobile.yml"
    assert mobile_job["secrets"] == "inherit"
    assert "github.event_name == 'push'" in mobile_job["if"]

    release_contract_job = mobile_workflow["jobs"]["release_contract"]
    assert release_contract_job["permissions"] == {"contents": "write"}


def test_packaging_verifies_checkout_identity_and_chat_api() -> None:
    workflow_sources = [
        path.read_text(encoding="utf-8")
        for path in (
            RELEASE,
            ROOT / ".github" / "workflows" / "release-dryrun.yml",
            ROOT / ".github" / "workflows" / "ci.yml",
        )
    ]
    prepare_source = PREPARE.read_text(encoding="utf-8")

    for source in workflow_sources:
        assert "--expected-git-hash" in source
        assert "--check-chat-api" in source
    assert 'OPENAKITA_BUILD_GIT_HASH="$(git rev-parse HEAD)"' in prepare_source


def test_changed_workflow_yaml_is_valid() -> None:
    paths = (
        RELEASE,
        MOBILE,
        ROOT / ".github" / "workflows" / "release-dryrun.yml",
        ROOT / ".github" / "workflows" / "ci.yml",
        PREPARE,
    )
    for path in paths:
        assert yaml.safe_load(path.read_text(encoding="utf-8"))
