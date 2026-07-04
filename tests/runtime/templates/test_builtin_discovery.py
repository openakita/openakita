"""End-to-end discovery test for the ``runtime.templates.builtin``
sub-package.

Closes the loop between the ``@template`` decorator, the
``discover_builtins`` helper, and a private :class:`TemplateRegistry`:
every module under ``runtime/templates/builtin/`` (excluding
``_*.py``) must contribute at least one :class:`TemplateSpec` that
passes validation.
"""

from __future__ import annotations

import sys
from pathlib import Path

from openakita.runtime.templates import (
    TemplateRegistry,
    discover_builtins,
)
from openakita.runtime.templates import registry as registry_mod


def _builtin_module_names() -> set[str]:
    """Return the set of submodule names under ``builtin/``.

    Mirrors the rule ``discover_builtins`` uses: anything that does
    not start with ``_`` and is a ``*.py`` file in the directory.
    """
    pkg_path = Path(registry_mod.__file__).parent / "builtin"
    return {
        p.stem
        for p in pkg_path.glob("*.py")
        if not p.name.startswith("_")
    }


def test_discovery_imports_every_builtin_module(monkeypatch) -> None:
    monkeypatch.setattr(registry_mod, "_PENDING", [], raising=False)
    for mod in list(sys.modules):
        if mod.startswith("openakita.runtime.templates.builtin"):
            sys.modules.pop(mod)

    imported = discover_builtins()
    expected = _builtin_module_names()
    assert imported == len(expected), (
        f"discover_builtins reported {imported} modules but the directory "
        f"contains {len(expected)} non-underscore submodules: {sorted(expected)}"
    )


def test_every_builtin_template_validates_and_instantiates(
    monkeypatch,
) -> None:
    monkeypatch.setattr(registry_mod, "_PENDING", [], raising=False)
    for mod in list(sys.modules):
        if mod.startswith("openakita.runtime.templates.builtin"):
            sys.modules.pop(mod)

    discover_builtins()
    reg = TemplateRegistry()
    drained = reg.bootstrap()
    assert drained == len(_builtin_module_names())

    for spec in reg.list():
        spec.validate()
        org = reg.instantiate(spec.id, name=f"smoke-{spec.id}")
        assert len(org.nodes) == len(spec.nodes)
        assert len(org.edges) == len(spec.edges)
        # Every freshly-minted org must have non-overlapping NodeIds
        # with the role-handle ids in the spec.
        spec_ids = {n.id for n in spec.nodes}
        org_ids = {n.id for n in org.nodes}
        assert spec_ids.isdisjoint(org_ids), (
            f"template {spec.id!r} leaked role-handle ids into the OrgV2"
        )


def test_known_builtin_ids_are_present(monkeypatch) -> None:
    """Sanity guard so a future commit cannot silently drop a template."""
    monkeypatch.setattr(registry_mod, "_PENDING", [], raising=False)
    for mod in list(sys.modules):
        if mod.startswith("openakita.runtime.templates.builtin"):
            sys.modules.pop(mod)
    discover_builtins()
    reg = TemplateRegistry()
    reg.bootstrap()
    assert "aigc_video_studio" in reg
    assert "software_team" in reg
    assert "startup_company" in reg
    assert "content_ops" in reg
