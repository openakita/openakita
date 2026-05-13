"""Shared path boundary validation for tool handlers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PathSafetyResult:
    ok: bool
    resolved: Path | None = None
    reason: str = ""
    safe_ref: str = ""


def resolve_within_root(path: str, roots: list[str | Path], *, max_len: int = 4096) -> PathSafetyResult:
    """Resolve a path and ensure it stays inside at least one allowed root."""
    raw = str(path or "")
    if not raw:
        return PathSafetyResult(False, reason="empty_path")
    if len(raw) > max_len:
        return PathSafetyResult(False, reason="path_too_long")
    if any(ord(ch) < 32 for ch in raw):
        return PathSafetyResult(False, reason="control_char")
    if raw.startswith("\\\\"):
        return PathSafetyResult(False, reason="unc_path")

    try:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        return PathSafetyResult(False, reason=f"resolve_error:{exc}")

    for root in roots:
        try:
            resolved_root = Path(root).resolve(strict=False)
            if resolved == resolved_root or resolved.is_relative_to(resolved_root):
                try:
                    safe_ref = str(resolved.relative_to(resolved_root))
                except ValueError:
                    safe_ref = resolved.name
                return PathSafetyResult(True, resolved=resolved, safe_ref=safe_ref)
        except (OSError, RuntimeError, ValueError):
            continue

    digest = hashlib.sha256(str(resolved).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return PathSafetyResult(False, resolved=resolved, reason="outside_allowed_roots", safe_ref=f"sha256:{digest}")
