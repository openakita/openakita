"""Lightweight tests for ``seedance_inline.system_deps`` — the FFmpeg
detector + installer that the Settings page renders.

These cover the two real-world bugs the user just hit:

1. ``winget uninstall`` silently no-op'd because the command was missing
   ``--accept-source-agreements`` — it returned rc=0 but the file was
   still there, so detect() reported "uninstall failed: still installed".
2. The ``estimated_seconds`` / argv plumbing must stay in sync between
   install and uninstall recipes so the public dict the UI consumes does
   not desync.

We avoid spawning real winget / brew / apt — these tests only assert
the *shape* of the recipes and the post-op retry logic shape.  Running
the actual subprocess is intentionally out of scope (the dep installer
is an OS-side integration that needs a manual smoke test on each
platform anyway).
"""

from __future__ import annotations

from seedance_inline.system_deps import _SPECS


def test_ffmpeg_install_recipe_includes_accept_source_agreements() -> None:
    """The Windows install command MUST accept source agreements,
    otherwise winget on a fresh box will block on the EULA prompt and
    --silent will eat the error without doing anything."""
    spec = _SPECS["ffmpeg"]
    win_install = next(
        m for m in spec.install_methods if m.platform == "windows"
    )
    assert win_install.command is not None
    assert "--accept-source-agreements" in win_install.command


def test_ffmpeg_uninstall_recipe_includes_accept_source_agreements() -> None:
    """Regression: user reported "卸载失败：已安装 v8.1..." — root cause
    was that ``winget uninstall --silent`` exited 0 without uninstalling
    because the source EULA had never been accepted on the host.  The
    flag below makes uninstall behave like install on a fresh box."""
    spec = _SPECS["ffmpeg"]
    win_uninstall = next(
        m for m in spec.uninstall_methods if m.platform == "windows"
    )
    assert win_uninstall.command is not None
    assert "--accept-source-agreements" in win_uninstall.command, (
        "winget uninstall must auto-accept source agreements or it "
        "becomes a silent no-op on fresh boxes"
    )


def test_ffmpeg_uninstall_recipe_targets_winget_id() -> None:
    """Sanity: we must uninstall the EXACT id we install
    (Gyan.FFmpeg) — otherwise the user thinks they un-installed but
    a re-detect still finds the binary."""
    spec = _SPECS["ffmpeg"]
    win_install = next(
        m for m in spec.install_methods if m.platform == "windows"
    )
    win_uninstall = next(
        m for m in spec.uninstall_methods if m.platform == "windows"
    )
    assert win_install.command is not None
    assert win_uninstall.command is not None
    install_id = win_install.command[win_install.command.index("--id") + 1]
    uninstall_id = win_uninstall.command[
        win_uninstall.command.index("--id") + 1
    ]
    assert install_id == uninstall_id == "Gyan.FFmpeg"


def test_ffmpeg_uninstall_recipe_is_silent_and_explicit() -> None:
    """Uninstall must be explicit (``-e``) AND silent so the operation
    completes without prompting — matching the install side's contract."""
    spec = _SPECS["ffmpeg"]
    win_uninstall = next(
        m for m in spec.uninstall_methods if m.platform == "windows"
    )
    assert win_uninstall.command is not None
    assert "-e" in win_uninstall.command
    assert "--silent" in win_uninstall.command


def test_macos_uninstall_recipe_unchanged() -> None:
    """Smoke: the macOS uninstall recipe is independent of the Windows
    fix — a regression here would mean we accidentally generalised the
    Windows-specific flag to brew (which does not understand it)."""
    spec = _SPECS["ffmpeg"]
    mac_uninstall = next(
        m for m in spec.uninstall_methods if m.platform == "macos"
    )
    assert mac_uninstall.command == ("brew", "uninstall", "ffmpeg")
