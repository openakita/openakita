"""Phase 0 skeleton tests for media-post (Gate 0 per §11 Phase 0).

These tests are intentionally minimal — they verify only the things
that must be true at the end of Phase 0:

1. Plugin module imports cleanly without pulling in mode files that do
   not exist yet.
2. ``Plugin`` subclasses ``PluginBase`` and exposes ``on_load`` /
   ``on_unload``.
3. ``plugin.json`` parses, contains the locked-down identity strings
   from §〇 of the plan, and declares SDK 0.7.0 + ``provides.tools`` =
   exactly the 4 tools the plan freezes (``media_post_create`` /
   ``_status`` / ``_list`` / ``_cancel``).
4. The 5-file UI kit is vendored verbatim from ``tongyi-image``
   (red-line §13 #2: no new CDN deps; UI kit must stay self-contained).
5. Red-line grep guards: ``plugin.py`` does NOT contain the literal
   ``/handoff/`` (no v2.0 routes leaked into v1.0) and does NOT contain
   ``shell=True`` (red-line §13 #4).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
TONGYI_DIR = PLUGIN_DIR.parent / "tongyi-image"


def test_plugin_module_imports() -> None:
    """``import plugin`` must not raise.

    Phase 0 has no mode modules yet, so plugin.py must keep its imports
    minimal — only ``openakita.plugins.api``. If this test starts failing
    after a new commit, suspect a premature import of ``mediapost_*``
    files that don't exist yet.
    """
    import plugin  # noqa: F401  (import-smoke only)

    assert hasattr(plugin, "Plugin"), "plugin.py must expose `Plugin` class"
    assert plugin.PLUGIN_ID == "media-post", "PLUGIN_ID must be 'media-post' per §〇"


def test_plugin_subclasses_pluginbase() -> None:
    """``Plugin`` MRO must include ``PluginBase`` and define lifecycle methods."""
    import plugin

    from openakita.plugins.api import PluginBase

    assert issubclass(plugin.Plugin, PluginBase), "Plugin must subclass PluginBase"
    assert callable(getattr(plugin.Plugin, "on_load", None))
    assert callable(getattr(plugin.Plugin, "on_unload", None))


def test_plugin_json_identity_locked() -> None:
    """``plugin.json`` must match the §〇 frozen identity table.

    Any drift here (id renamed, version bumped, sdk range loosened)
    breaks the rest of the plan because file prefixes, sqlite path,
    i18n namespace, and class name are all derived from these values.
    """
    raw = (PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8")
    manifest = json.loads(raw)

    assert manifest["id"] == "media-post"
    assert manifest["version"] == "0.1.0"
    assert manifest["type"] == "python"
    assert manifest["entry"] == "plugin.py"
    assert manifest["display_name_zh"] == "发布物料工坊"
    assert manifest["requires"]["sdk"] == ">=0.7.0,<0.8.0"
    assert manifest["requires"]["plugin_api"] == "~2"
    assert manifest["ui"]["entry"] == "ui/dist/index.html"
    assert manifest["ui"]["title_i18n"]["zh"] == "发布物料工坊"


def test_plugin_json_provides_four_tools() -> None:
    """The plan §1.4 freezes v1.0 to 4 tools (no `_handoff_*` until v2.0)."""
    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    tools = manifest["provides"]["tools"]
    assert sorted(tools) == sorted(
        [
            "media_post_create",
            "media_post_status",
            "media_post_list",
            "media_post_cancel",
        ]
    ), f"v1.0 must expose exactly the 4 tools; got {tools}"

    for tool in tools:
        assert "handoff" not in tool, f"v1.0 must not ship handoff tools (got {tool})"


@pytest.mark.parametrize(
    "asset",
    ["bootstrap.js", "styles.css", "icons.js", "markdown-mini.js", "i18n.js"],
)
def test_ui_assets_vendored_from_tongyi(asset: str) -> None:
    """UI kit must be byte-identical to ``tongyi-image``'s assets.

    Red-line §13 #2: no new CDN deps; UI kit stays self-contained and
    derives from the canonical ``tongyi-image`` source. Any local edit
    here must be made upstream in ``tongyi-image`` and re-vendored.
    """
    mine = (PLUGIN_DIR / "ui" / "dist" / "_assets" / asset).read_bytes()
    upstream = (TONGYI_DIR / "ui" / "dist" / "_assets" / asset).read_bytes()
    assert hashlib.sha256(mine).hexdigest() == hashlib.sha256(upstream).hexdigest(), (
        f"{asset} drifted from tongyi-image upstream — re-vendor instead of "
        f"editing in place (red-line §13 #2)."
    )


def test_no_handoff_route_literal() -> None:
    """v1.0 must not register any /handoff/* routes (§1.4 + red-line §13 #11)."""
    body = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    assert "/handoff/" not in body, (
        "v1.0 plugin.py must not contain '/handoff/' — handoff layer is v2.0"
    )


def test_no_shell_true_in_plugin() -> None:
    """Red-line §13 #4: never use ``shell=True`` in subprocess calls."""
    body = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    assert "shell=True" not in body, "Red-line §13 #4: shell=True is forbidden"


def test_no_archive_or_shared_imports() -> None:
    """§2.7 铁律 #3: no ``from _shared`` / ``from plugins-archive`` imports."""
    body = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    for forbidden in ("from _shared", "from plugins-archive", "sdk.contrib"):
        assert forbidden not in body, f"Red-line §2.7: '{forbidden}' is forbidden"
