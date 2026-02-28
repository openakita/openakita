"""Runtime compatibility helpers for third-party Python packages."""

from __future__ import annotations

import logging


def patch_simplejson_jsondecodeerror(*, logger: logging.Logger | None = None) -> bool:
    """Ensure ``simplejson.JSONDecodeError`` exists.

    Some broken/legacy simplejson builds expose the module but miss
    ``JSONDecodeError``. ``requests`` imports that symbol when simplejson is
    available, which can break Feishu SDK (lark-oapi) import.

    Returns:
        True if a patch was applied, otherwise False.
    """
    try:
        import simplejson as _sj
    except Exception:
        return False

    if hasattr(_sj, "JSONDecodeError"):
        return False

    try:
        from json.decoder import JSONDecodeError as _JSONDecodeError
    except Exception:
        class _JSONDecodeError(ValueError):
            pass

    _sj.JSONDecodeError = _JSONDecodeError
    errors_mod = getattr(_sj, "errors", None)
    if errors_mod is not None and not hasattr(errors_mod, "JSONDecodeError"):
        try:
            errors_mod.JSONDecodeError = _JSONDecodeError
        except Exception:
            pass

    (logger or logging.getLogger(__name__)).warning(
        "Patched simplejson: added missing JSONDecodeError for requests compatibility"
    )
    return True
