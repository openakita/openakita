"""Pure helpers for deciding the HTTP API bind host.

A separate module so the decision logic is testable in isolation: it takes
``env`` / ``platform`` / ``api_lan_mode`` as arguments and returns the resolved
host without touching any module-level state. ``main.py`` calls this once at
startup; nothing else should import :data:`API_HOST` as a module constant.

Priority chain (first match wins):

1. Explicit ``API_HOST`` env var (set in ``.env``, shell, or by the desktop
   "Allow LAN access" toggle).
2. ``settings.api_lan_mode=True`` *or* headless host detected — bind
   ``0.0.0.0`` so the server is reachable from other machines on the LAN.
3. Fall back to ``127.0.0.1``.

The launcher-hint layer (``OPENAKITA_LAUNCHER=desktop``) was considered and
intentionally **not** added: it never changes the outcome because Tauri can't
run on Linux without ``DISPLAY``/``WAYLAND_DISPLAY``, and on macOS / Windows
the platform check already returns ``False`` for "headless". Adding it would
have been dead code.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = ["is_headless", "resolve_api_host"]


_HEADLESS_PLATFORM_PREFIXES = ("linux", "freebsd", "openbsd", "netbsd")


def is_headless(platform: str, env: Mapping[str, str]) -> bool:
    """Return True when the process appears to be running on a headless host.

    "Headless" here means: a Unix-like platform that exposes neither an X11
    display nor a Wayland display. macOS (``darwin``) and Windows (``win32``)
    are always considered to have a GUI available, even when there's no
    interactive desktop session — they are never reported as headless because
    binding to ``0.0.0.0`` on those platforms by default would be a regression
    for the established desktop / IDE-launcher experience.
    """
    plat = (platform or "").lower()
    if not any(plat.startswith(p) for p in _HEADLESS_PLATFORM_PREFIXES):
        return False
    if env.get("DISPLAY", "").strip():
        return False
    if env.get("WAYLAND_DISPLAY", "").strip():
        return False
    return True


def resolve_api_host(
    env: Mapping[str, str],
    api_lan_mode: bool,
    platform: str,
) -> str:
    """Decide the HTTP API bind host using the 3-layer priority chain.

    Args:
        env: A read-only mapping of environment variables (typically
            ``os.environ``). Tests pass plain ``dict`` instances.
        api_lan_mode: Value of :attr:`Settings.api_lan_mode`. Preserves the
            opt-in toggle from PR-L1 users without forcing migration.
        platform: ``sys.platform`` string.

    Returns:
        The resolved bind host: an explicit user-provided value, ``"0.0.0.0"``
        when LAN is wanted, or ``"127.0.0.1"`` as the safe default.
    """
    explicit = env.get("API_HOST", "").strip()
    if explicit:
        return explicit
    if api_lan_mode or is_headless(platform, env):
        return "0.0.0.0"
    return "127.0.0.1"
