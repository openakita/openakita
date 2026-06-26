"""Unit tests for :mod:`openakita.api.host_resolution`.

Covers the 3-layer priority chain:

1. Explicit ``API_HOST`` env var wins.
2. ``api_lan_mode=True`` OR headless detection -> 0.0.0.0.
3. Default -> 127.0.0.1.
"""

from __future__ import annotations

import pytest

from openakita.api.host_resolution import is_headless, resolve_api_host


class TestIsHeadless:
    def test_linux_no_display_is_headless(self):
        assert is_headless("linux", {}) is True

    def test_linux_with_display_is_not_headless(self):
        assert is_headless("linux", {"DISPLAY": ":0"}) is False

    def test_linux_with_wayland_is_not_headless(self):
        assert is_headless("linux", {"WAYLAND_DISPLAY": "wayland-0"}) is False

    def test_linux_with_empty_display_is_headless(self):
        assert is_headless("linux", {"DISPLAY": "  "}) is True

    def test_darwin_is_never_headless(self):
        assert is_headless("darwin", {}) is False

    def test_win32_is_never_headless(self):
        assert is_headless("win32", {}) is False

    @pytest.mark.parametrize("plat", ["freebsd14", "openbsd7", "netbsd9"])
    def test_bsd_platforms_treated_as_unix(self, plat):
        assert is_headless(plat, {}) is True
        assert is_headless(plat, {"DISPLAY": ":0"}) is False

    def test_empty_platform_is_not_headless(self):
        assert is_headless("", {}) is False

    def test_uppercase_platform_normalised(self):
        assert is_headless("Linux", {}) is True


class TestResolveApiHost:
    def test_explicit_api_host_wins_over_everything(self):
        assert (
            resolve_api_host(
                {"API_HOST": "0.0.0.0", "DISPLAY": ":0"},
                api_lan_mode=True,
                platform="linux",
            )
            == "0.0.0.0"
        )

    def test_explicit_api_host_can_force_loopback_on_headless(self):
        assert (
            resolve_api_host(
                {"API_HOST": "127.0.0.1"},
                api_lan_mode=False,
                platform="linux",
            )
            == "127.0.0.1"
        )

    def test_api_lan_mode_forces_lan_on_desktop(self):
        assert (
            resolve_api_host(
                {"DISPLAY": ":0"},
                api_lan_mode=True,
                platform="linux",
            )
            == "0.0.0.0"
        )

    def test_headless_linux_defaults_to_lan(self):
        assert (
            resolve_api_host(
                {},
                api_lan_mode=False,
                platform="linux",
            )
            == "0.0.0.0"
        )

    def test_desktop_linux_defaults_to_loopback(self):
        assert (
            resolve_api_host(
                {"DISPLAY": ":0"},
                api_lan_mode=False,
                platform="linux",
            )
            == "127.0.0.1"
        )

    def test_macos_defaults_to_loopback(self):
        assert resolve_api_host({}, api_lan_mode=False, platform="darwin") == "127.0.0.1"

    def test_windows_defaults_to_loopback(self):
        assert resolve_api_host({}, api_lan_mode=False, platform="win32") == "127.0.0.1"

    def test_empty_explicit_api_host_is_ignored(self):
        assert (
            resolve_api_host(
                {"API_HOST": "   "},
                api_lan_mode=False,
                platform="linux",
            )
            == "0.0.0.0"
        )

    def test_empty_explicit_api_host_falls_back_to_loopback_on_desktop(self):
        assert (
            resolve_api_host(
                {"API_HOST": "", "DISPLAY": ":0"},
                api_lan_mode=False,
                platform="linux",
            )
            == "127.0.0.1"
        )

    def test_docker_linux_no_display_picks_lan(self):
        env = {"PATH": "/usr/bin", "HOSTNAME": "container"}
        assert resolve_api_host(env, api_lan_mode=False, platform="linux") == "0.0.0.0"

    def test_ssh_session_with_x_forwarding_picks_loopback(self):
        env = {"SSH_CONNECTION": "1.2.3.4 1234 5.6.7.8 22", "DISPLAY": "localhost:10.0"}
        assert resolve_api_host(env, api_lan_mode=False, platform="linux") == "127.0.0.1"
