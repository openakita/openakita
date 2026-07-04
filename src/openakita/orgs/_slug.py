"""Slug generator for org template ids (F-4 §A-1).

Auto-generated template ids (from
:py:meth:`openakita.orgs.manager.OrgManager.save_as_template`
fallback) must be URL-safe ASCII so they roundtrip cleanly through
HTTP path params, SDK URL builders, and log-scrape pipelines that
key on ASCII slugs.

Falls back to a deterministic MD5-derived suffix when the input has
no ASCII characters at all (e.g. pure CJK names like
"内容运营团队"); the original human-readable text is preserved
separately via the ``display_name`` field added to
``list_templates()`` output (F-4 §A-2).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Anything outside the kebab-case allowed set gets dropped after the
# NFKD ASCII-encode pass.
_DROP_RE = re.compile(r"[^a-z0-9_-]")
_COLLAPSE_DASH_RE = re.compile(r"-+")
_WHITESPACE_RE = re.compile(r"\s+")


def slugify_template_id(name: str) -> str:
    """Return an ASCII, URL-safe template id derived from ``name``.

    Algorithm:
      1. Strip leading/trailing whitespace from ``name``.
      2. Run :func:`unicodedata.normalize` ``NFKD``, then ASCII-encode
         with ``errors='ignore'`` to drop combining marks and any
         character with no ASCII equivalent.
      3. Lowercase.
      4. Collapse runs of whitespace into a single ``-``.
      5. Drop any character not in ``[a-z0-9_-]``.
      6. Collapse consecutive ``-`` into a single ``-``.
      7. Strip leading/trailing ``-``/``_``.
      8. If the result is empty (e.g. pure-CJK input where every char
         was dropped at step 2), fall back to ``"tpl-<md5_8>"`` where
         the digest is computed from the **original** UTF-8 bytes of
         ``name``, so the same human-readable name always maps to the
         same slug (deterministic; safe to retry).

    Examples (illustrative; see ``tests/runtime/orgs/test_slug.py``):

      >>> slugify_template_id("Content Ops Team")
      'content-ops-team'
      >>> slugify_template_id("software-team")
      'software-team'
      >>> # Pure CJK -> deterministic ASCII fallback (always same digest):
      >>> s1 = slugify_template_id("内容运营团队")
      >>> s2 = slugify_template_id("内容运营团队")
      >>> s1 == s2 and s1.startswith("tpl-") and len(s1) == 12
      True
    """
    s = (name or "").strip()
    if not s:
        # Pure-empty input: stable sentinel based on empty-bytes md5.
        digest = hashlib.md5(b"", usedforsecurity=False).hexdigest()[:8]
        return f"tpl-{digest}"

    normalized = unicodedata.normalize("NFKD", s)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    spaced = _WHITESPACE_RE.sub("-", ascii_only)
    cleaned = _DROP_RE.sub("", spaced)
    cleaned = _COLLAPSE_DASH_RE.sub("-", cleaned).strip("-_")

    if not cleaned:
        # CJK-only / symbol-only input -> hash fallback. Hash the
        # ORIGINAL name (not the stripped ascii_only) so identical
        # human-readable text always maps to the same slug.
        digest = hashlib.md5(s.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        return f"tpl-{digest}"
    return cleaned
