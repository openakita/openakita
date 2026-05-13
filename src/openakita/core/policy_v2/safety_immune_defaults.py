"""OpenAkita built-in safety_immune paths (9 semantic categories).

These are paths that **must always** be treated as immune (i.e. trigger a
CONFIRM in trust mode and a DENY in strict mode) regardless of whether
the user explicitly listed them in ``POLICIES.yaml``. Users may **add**
to the immune set via ``security.safety_immune.paths`` (config) or
``ctx.safety_immune_paths`` (per-call), but they cannot remove anything
listed here — that is the difference between an "opt-in safety net" and
"baseline self-protection".

Why hard-code instead of YAML defaults?

- ``loader._deep_merge_defaults`` is a list-replace merge (loader.py docs):
  if these paths lived in the schema's ``default_factory``, the moment a
  user wrote ``safety_immune: {paths: [foo]}`` to override one entry the
  other 8 categories would silently disappear.
- We want OpenAkita's own identity files / audit trails / scheduler state
  to be invariably protected even from a misconfigured fork.
- Keeping the list in code lets ``ruff`` + tests audit the categories;
  YAML drift is hard to detect.

Categories (designed to be cross-platform; ``${CWD}`` is expanded against
``Path.cwd()`` at engine init, ``~`` against ``Path.home()``):

1. **Identity (agent soul / brain)** — SOUL/AGENT/USER/MEMORY + compiled
   prompt cache + POLICIES.yaml itself. Touching these mid-run rewrites
   the agent's behaviour.
2. **Audit logs** — append-only decision/evolution/plugin trails.
   Editing existing entries breaks audit integrity.
3. **Checkpoints / snapshots** — file rollback safety net.
4. **Sessions persistence** — sessions.json + group_policy.json. Editing
   these can take over an active conversation.
5. **Scheduler state** — cron tasks + execution history + pending
   approvals + crash-recovery locks.
6. **User credentials / keys** — SSH/GPG/AWS/cloud creds + LLM endpoint
   keys + OpenAkita's own user manager profiles.
7. **OS system binaries / config** — Windows, /etc, /usr, /bin, /sbin,
   /lib, /boot, /System, /Library.
8. **Kernel / runtime pseudo-fs** — /proc, /sys, /dev. Writing here
   typically requires root and can wedge the host.
9. **Package install dirs** — Program Files / Program Files (x86) /
   ProgramData / /opt. Modifying these poisons system tooling.

Each entry uses ``/**`` glob anchor where applicable (engine's
``_path_under`` strips the anchor and treats the prefix as a directory
boundary, so ``identity/runtime/**`` and ``identity/runtime`` behave
identically — the anchor is purely an authoring convention to make
intent obvious).
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Category 1: Identity (agent soul / brain)
# ---------------------------------------------------------------------------
_CATEGORY_1_IDENTITY: tuple[str, ...] = (
    "${CWD}/identity/SOUL.md",
    "${CWD}/identity/AGENT.md",
    "${CWD}/identity/USER.md",
    "${CWD}/identity/MEMORY.md",
    "${CWD}/identity/POLICIES.yaml",
    "${CWD}/identity/runtime/**",
)

# ---------------------------------------------------------------------------
# Category 2: Audit logs (append-only integrity)
# ---------------------------------------------------------------------------
_CATEGORY_2_AUDIT: tuple[str, ...] = (
    "${CWD}/data/audit/**",
)

# ---------------------------------------------------------------------------
# Category 3: Checkpoints (file rollback safety net)
# ---------------------------------------------------------------------------
_CATEGORY_3_CHECKPOINTS: tuple[str, ...] = (
    "${CWD}/data/checkpoints/**",
)

# ---------------------------------------------------------------------------
# Category 4: Sessions persistence
# ---------------------------------------------------------------------------
_CATEGORY_4_SESSIONS: tuple[str, ...] = (
    "${CWD}/data/sessions/**",
)

# ---------------------------------------------------------------------------
# Category 5: Scheduler state (cron / executions / pending approvals / locks)
# ---------------------------------------------------------------------------
_CATEGORY_5_SCHEDULER: tuple[str, ...] = (
    "${CWD}/data/scheduler/**",
    "${CWD}/.openakita/system_tasks.lock",
)

# ---------------------------------------------------------------------------
# Category 6: User credentials / API keys
# ---------------------------------------------------------------------------
_CATEGORY_6_CREDENTIALS: tuple[str, ...] = (
    "~/.ssh/**",
    "~/.gnupg/**",
    "~/.aws/credentials",
    "~/.aws/config",
    "~/.kube/config",
    "~/.docker/config.json",
    "${CWD}/data/llm_endpoints.json",
    "${CWD}/data/users/**",
    "${CWD}/data/plugin_state.json",
)

# ---------------------------------------------------------------------------
# Category 7: OS system binaries / config (cross-platform)
# ---------------------------------------------------------------------------
_CATEGORY_7_OS_SYSTEM: tuple[str, ...] = (
    # POSIX
    "/etc/**",
    "/bin/**",
    "/sbin/**",
    "/usr/bin/**",
    "/usr/sbin/**",
    "/usr/lib/**",
    "/usr/lib64/**",
    "/lib/**",
    "/lib64/**",
    "/boot/**",
    # macOS
    "/System/**",
    "/Library/**",
    "/private/etc/**",
    # Windows
    "C:/Windows/**",
    "C:/Windows/System32/**",
    "C:/Windows/SysWOW64/**",
)

# ---------------------------------------------------------------------------
# Category 8: Kernel / runtime pseudo-filesystems (POSIX)
# ---------------------------------------------------------------------------
_CATEGORY_8_KERNEL_FS: tuple[str, ...] = (
    "/proc/**",
    "/sys/**",
    "/dev/**",
)

# ---------------------------------------------------------------------------
# Category 9: Package install dirs (third-party tooling integrity)
# ---------------------------------------------------------------------------
_CATEGORY_9_PACKAGE_DIRS: tuple[str, ...] = (
    "C:/Program Files/**",
    "C:/Program Files (x86)/**",
    "C:/ProgramData/**",
    "/opt/**",
    "/usr/local/**",
)


# ---------------------------------------------------------------------------
# Public: ordered tuple (preserves intent grouping; engine dedupes anyway)
# ---------------------------------------------------------------------------
BUILTIN_SAFETY_IMMUNE_PATHS: tuple[str, ...] = (
    *_CATEGORY_1_IDENTITY,
    *_CATEGORY_2_AUDIT,
    *_CATEGORY_3_CHECKPOINTS,
    *_CATEGORY_4_SESSIONS,
    *_CATEGORY_5_SCHEDULER,
    *_CATEGORY_6_CREDENTIALS,
    *_CATEGORY_7_OS_SYSTEM,
    *_CATEGORY_8_KERNEL_FS,
    *_CATEGORY_9_PACKAGE_DIRS,
)


# Per-category breakdown (for tests / docs / SecurityView debug panel)
BUILTIN_SAFETY_IMMUNE_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    "identity": _CATEGORY_1_IDENTITY,
    "audit": _CATEGORY_2_AUDIT,
    "checkpoints": _CATEGORY_3_CHECKPOINTS,
    "sessions": _CATEGORY_4_SESSIONS,
    "scheduler": _CATEGORY_5_SCHEDULER,
    "credentials": _CATEGORY_6_CREDENTIALS,
    "os_system": _CATEGORY_7_OS_SYSTEM,
    "kernel_fs": _CATEGORY_8_KERNEL_FS,
    "package_dirs": _CATEGORY_9_PACKAGE_DIRS,
}


def expand_builtin_immune_paths(cwd: Path | None = None) -> tuple[str, ...]:
    """Return the 9-category builtin list with ``${CWD}`` / ``~`` expanded.

    The engine calls this at construction time so the values are stable
    for the lifetime of the engine instance (rebuild on policy reload).
    Caller-provided ``cwd`` lets tests pin a workspace (otherwise we use
    ``Path.cwd()``).
    """
    base = Path(cwd) if cwd is not None else Path.cwd()
    cwd_str = str(base).replace("\\", "/")

    expanded: list[str] = []
    for raw in BUILTIN_SAFETY_IMMUNE_PATHS:
        if raw.startswith("~"):
            expanded.append(str(Path(raw).expanduser()).replace("\\", "/"))
        elif "${CWD}" in raw:
            expanded.append(raw.replace("${CWD}", cwd_str))
        else:
            expanded.append(raw)
    return tuple(expanded)


__all__ = [
    "BUILTIN_SAFETY_IMMUNE_BY_CATEGORY",
    "BUILTIN_SAFETY_IMMUNE_PATHS",
    "expand_builtin_immune_paths",
]
