"""
L3 Integration Tests: pyproject.toml wheel-packaging configuration.

These tests assert that the identity templates the setup wizard expects
to find under the installed ``openakita`` package directory are actually
shipped into the wheel via ``[tool.hatch.build.targets.wheel.force-include]``.

Why this matters
================
``setup.wizard._resolve_identity_template`` has two resolution strategies:

1. Walk up from the running ``openakita`` package directory looking for a
   sibling ``identity/`` (the repo-checkout / ``pip install -e .`` path).
2. Fall back to ``<openakita-package-root>/identity/<file>`` (the
   wheel-install path, populated by hatch's force-include block).

If the wheel does not actually carry the identity templates, strategy (2)
silently fails and wheel-installed users end up with an empty ``SOUL.md``
plus a 5-line ``_STATIC_FALLBACKS['identity_core']`` prompt instead of the
full templated SOUL document.

This file pins the contract by parsing the project's ``pyproject.toml``
directly (no need to actually build a wheel) and asserting that the
relevant ``force-include`` entries exist, point at files that are present
in the source tree, and place those files under the expected
``openakita/identity/`` subtree.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


@pytest.fixture(scope="module")
def force_include() -> dict[str, str]:
    """Load the ``force-include`` mapping from pyproject.toml once per module."""
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    targets = data["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert isinstance(targets, dict)
    return targets


REQUIRED_IDENTITY_SOURCES = [
    "identity/SOUL.md.example",
    "identity/AGENT.md.example",
    "identity/USER.md.example",
    "identity/MEMORY.md.example",
    "identity/POLICIES.yaml",
    "identity/CREDITS.md",
    "identity/SYSTEM_TASKS.yaml.template",
    "identity/personas/boyfriend.md",
    "identity/personas/business.md",
    "identity/personas/butler.md",
    "identity/personas/default.md",
    "identity/personas/family.md",
    "identity/personas/girlfriend.md",
    "identity/personas/jarvis.md",
    "identity/personas/tech_expert.md",
    "identity/personas/user_custom.md.example",
    "identity/prompts/policies.md",
]

# These are gitignored user-state files that MUST NOT be shipped in the
# wheel, because they contain dev-machine-specific data and would clobber
# a wheel-installed user's runtime state. Listed here as a regression
# guard.
FORBIDDEN_IDENTITY_SOURCES = [
    "identity/SOUL.md",
    "identity/AGENT.md",
    "identity/USER.md",
    "identity/MEMORY.md",
    "identity/MEMORY.md.bak",
    "identity/personas/user_custom.md",
    "identity/runtime/.compiled_at",
    "identity/runtime/user.summary.md",
    "identity/runtime/persona.custom.md",
]


class TestIdentityTemplatesAreShipped:
    @pytest.mark.parametrize("source_path", REQUIRED_IDENTITY_SOURCES)
    def test_identity_template_is_force_included(
        self, force_include: dict[str, str], source_path: str
    ) -> None:
        """Every identity template the wizard needs must be in force-include."""
        assert source_path in force_include, (
            f"{source_path!r} is missing from pyproject.toml "
            f"[tool.hatch.build.targets.wheel.force-include]. "
            f"Without this entry, wheel-installed users will not be able "
            f"to seed identity/<this file> on first run."
        )

    @pytest.mark.parametrize("source_path", REQUIRED_IDENTITY_SOURCES)
    def test_identity_template_source_file_exists(self, source_path: str) -> None:
        """The source path referenced by force-include must exist in the repo."""
        full_path = REPO_ROOT / source_path
        assert full_path.is_file(), (
            f"{source_path!r} is listed in pyproject.toml force-include but "
            f"the source file does not exist at {full_path}. "
            f"hatch will fail to build the wheel."
        )

    @pytest.mark.parametrize("source_path", REQUIRED_IDENTITY_SOURCES)
    def test_identity_template_target_under_openakita_identity(
        self, force_include: dict[str, str], source_path: str
    ) -> None:
        """Wheel-side target must live under ``openakita/identity/`` so the
        package-relative lookup in ``_resolve_identity_template`` finds it."""
        if source_path not in force_include:
            pytest.skip(f"{source_path} not in force-include; covered by other test")
        target = force_include[source_path]
        assert target.startswith("openakita/identity/"), (
            f"{source_path!r} → {target!r}: target must be under "
            f"'openakita/identity/' so it ends up next to the "
            f"installed openakita package directory."
        )
        relative = source_path[len("identity/") :]
        assert target == f"openakita/identity/{relative}", (
            f"{source_path!r} → {target!r}: target should mirror the "
            f"source layout exactly (expected 'openakita/identity/{relative}')."
        )


class TestUserStateIsNotShipped:
    @pytest.mark.parametrize("forbidden_path", FORBIDDEN_IDENTITY_SOURCES)
    def test_user_state_not_force_included(
        self, force_include: dict[str, str], forbidden_path: str
    ) -> None:
        """Gitignored user-state files must NOT be in force-include.

        force-include in hatch is absolute: it ships files even if they
        are gitignored or otherwise excluded. So we explicitly assert
        the wheel-builder is not told to ship them.
        """
        assert forbidden_path not in force_include, (
            f"{forbidden_path!r} is gitignored user state but is listed "
            f"in pyproject.toml force-include. Wheel-installed users "
            f"would receive dev-machine-specific data, which is a "
            f"packaging regression."
        )


class TestWizardCanResolveBundledTemplate:
    """Sanity check: the wizard's lookup logic does find SOUL.md.example
    in the current repo checkout. This is the dev-install path; the
    wheel-install path can only be exercised under an actual wheel
    install, which we cannot do from inside the test."""

    def test_resolves_soul_md_example_in_repo(self) -> None:
        from openakita.setup.wizard import _resolve_identity_template

        result = _resolve_identity_template("SOUL.md.example")
        assert result is not None, (
            "_resolve_identity_template returned None for SOUL.md.example "
            "even from a dev checkout. Wizard would fail to seed identity."
        )
        assert result.is_file()
        assert result.name == "SOUL.md.example"
