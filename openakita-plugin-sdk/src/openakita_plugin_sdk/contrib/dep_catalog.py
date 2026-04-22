"""Built-in catalog of system dependencies handled by ``DependencyGate``.

Each entry is the canonical, security-reviewed install recipe for one
binary across Windows / macOS / Linux. To add a new dependency:

1. Append a ``SystemDependency`` here (NEVER inline new ``InstallMethod``
   tuples in plugin code — the gate's whitelist is *this file only*).
2. Re-export it from ``__init__`` and document it in
   ``docs/dependency-gate.md``.
3. Open a PR; security review verifies the install commands cannot escape
   their argv (no ``shell=True``, no user-supplied parameters).

Linux note
----------
Most package managers need root. We expose ``apt``/``dnf`` methods only when
the running process can elevate (host route checks ``os.geteuid()`` before
spawning). For desktop installs without sudo, the UI falls back to ``manual``
and shows the upstream documentation link — never silently fails.
"""

from __future__ import annotations

from .dep_gate import InstallMethod, SystemDependency

# ── FFmpeg ──────────────────────────────────────────────────────────────
# Used by: seedance-video, highlight-cutter, subtitle-maker, video-translator,
# tts-studio (audio mixing). Must always be reachable from PATH.

FFMPEG = SystemDependency(
    id="ffmpeg",
    display_name="FFmpeg",
    description=(
        "Video / audio processing toolkit used by every media plugin "
        "(transcoding, concatenation, subtitle burn-in, audio mixing)."
    ),
    probes=("ffmpeg",),
    version_argv=("ffmpeg", "-version"),
    version_regex=r"ffmpeg version\s+(\S+)",
    homepage="https://ffmpeg.org/download.html",
    install_methods=(
        InstallMethod(
            platform="windows",
            strategy="winget",
            command=("winget", "install", "--id", "Gyan.FFmpeg", "-e", "--accept-source-agreements", "--accept-package-agreements"),
            description="Install FFmpeg via Windows Package Manager (winget).",
            requires_sudo=False,
            requires_confirm=True,
            estimated_seconds=120,
        ),
        InstallMethod(
            platform="macos",
            strategy="brew",
            command=("brew", "install", "ffmpeg"),
            description="Install FFmpeg via Homebrew.",
            requires_sudo=False,
            requires_confirm=True,
            estimated_seconds=180,
        ),
        InstallMethod(
            platform="linux",
            strategy="apt",
            command=("apt-get", "install", "-y", "ffmpeg"),
            description="Install FFmpeg via apt (Debian / Ubuntu). Requires root.",
            requires_sudo=True,
            requires_confirm=True,
            estimated_seconds=120,
        ),
        InstallMethod(
            platform="linux",
            strategy="dnf",
            command=("dnf", "install", "-y", "ffmpeg"),
            description="Install FFmpeg via dnf (Fedora / RHEL). Requires root.",
            requires_sudo=True,
            requires_confirm=True,
            estimated_seconds=120,
        ),
        InstallMethod(
            platform="linux",
            strategy="manual",
            command=None,
            description="No package manager available — download a static build.",
            requires_sudo=False,
            requires_confirm=False,
            manual_url="https://johnvansickle.com/ffmpeg/",
        ),
    ),
)


# ── whisper.cpp ─────────────────────────────────────────────────────────
# Used by: subtitle-maker, video-translator, tts-studio (input transcription).
# Catalog binary name is ``whisper-cli`` (the CMake build target).

WHISPER_CPP = SystemDependency(
    id="whisper.cpp",
    display_name="whisper.cpp",
    description=(
        "Local speech-to-text engine. Used by subtitle / translation / "
        "voice plugins for offline transcription. Distributed as a single "
        "binary plus model files."
    ),
    # The binary lives in PATH as ``whisper-cli`` (newer CMake builds).
    # We deliberately do NOT probe legacy ``main`` because shutil.which
    # would happily match unrelated system files like Windows ``main.cpl``.
    # Users on legacy builds should rename / symlink to ``whisper-cli``.
    probes=("whisper-cli",),
    version_argv=("whisper-cli", "--version"),
    version_regex=r"whisper(?:\.cpp|-cli)?\s+v?(\d+\.\d+(?:\.\d+)?)",
    homepage="https://github.com/ggerganov/whisper.cpp",
    install_methods=(
        InstallMethod(
            platform="windows",
            strategy="manual",
            command=None,
            description=(
                "Download a prebuilt whisper-cli release and add it to PATH; "
                "Windows has no first-party package today."
            ),
            requires_confirm=False,
            manual_url="https://github.com/ggerganov/whisper.cpp/releases",
        ),
        InstallMethod(
            platform="macos",
            strategy="brew",
            command=("brew", "install", "whisper-cpp"),
            description="Install whisper.cpp via Homebrew.",
            requires_sudo=False,
            requires_confirm=True,
            estimated_seconds=180,
        ),
        InstallMethod(
            platform="linux",
            strategy="manual",
            command=None,
            description=(
                "Build from source (see upstream README) or use the bundled "
                "release tarball; no major distro ships whisper.cpp yet."
            ),
            requires_confirm=False,
            manual_url="https://github.com/ggerganov/whisper.cpp#quick-start",
        ),
    ),
)


# ── yt-dlp ──────────────────────────────────────────────────────────────
# Used by: video-translator (input download), highlight-cutter (URL ingest).

YT_DLP = SystemDependency(
    id="yt-dlp",
    display_name="yt-dlp",
    description=(
        "Universal video downloader (YouTube, Bilibili, X, …). Required "
        "for any plugin feature that ingests media from a URL."
    ),
    probes=("yt-dlp",),
    version_argv=("yt-dlp", "--version"),
    version_regex=r"(\d{4}\.\d{2}\.\d{2})",
    homepage="https://github.com/yt-dlp/yt-dlp",
    install_methods=(
        InstallMethod(
            platform="windows",
            strategy="winget",
            command=("winget", "install", "--id", "yt-dlp.yt-dlp", "-e", "--accept-source-agreements", "--accept-package-agreements"),
            description="Install yt-dlp via Windows Package Manager (winget).",
            requires_sudo=False,
            requires_confirm=True,
            estimated_seconds=60,
        ),
        InstallMethod(
            platform="macos",
            strategy="brew",
            command=("brew", "install", "yt-dlp"),
            description="Install yt-dlp via Homebrew.",
            requires_sudo=False,
            requires_confirm=True,
            estimated_seconds=60,
        ),
        InstallMethod(
            platform="linux",
            strategy="pip",
            command=("python3", "-m", "pip", "install", "--user", "--upgrade", "yt-dlp"),
            description="Install yt-dlp into the user's pip site-packages.",
            requires_sudo=False,
            requires_confirm=True,
            estimated_seconds=60,
        ),
    ),
)


CATALOG: tuple[SystemDependency, ...] = (FFMPEG, WHISPER_CPP, YT_DLP)
"""All built-in dependencies. Hosts pass this to ``DependencyGate``."""

CATALOG_BY_ID: dict[str, SystemDependency] = {dep.id: dep for dep in CATALOG}


__all__ = ["CATALOG", "CATALOG_BY_ID", "FFMPEG", "WHISPER_CPP", "YT_DLP"]
