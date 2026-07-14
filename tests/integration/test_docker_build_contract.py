from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_docker_builder_copies_hatch_build_inputs_before_install() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    install_offset = dockerfile.index("pip install --no-cache-dir")
    builder_setup = dockerfile[:install_offset]

    assert "COPY pyproject.toml README.md VERSION hatch_build.py ./" in builder_setup
    assert (
        "COPY scripts/write_build_version.py scripts/write_build_version.py" in builder_setup
    )
    assert "ARG OPENAKITA_BUILD_GIT_HASH=dev" in builder_setup


def test_docker_publish_passes_the_checked_out_commit_hash() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert "OPENAKITA_BUILD_GIT_HASH=${{ github.sha }}" in workflow
