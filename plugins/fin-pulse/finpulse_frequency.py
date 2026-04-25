"""Keyword matching DSL with the ``deepcopy`` hardening called out in
§13.2 of the fin-pulse plan.

File syntax (one group per blank-line-separated block):

* Lines starting with ``#`` are comments.
* A line ``[GLOBAL_FILTER]`` flips the parser into global-exclude mode.
* Inside a group, each line is one keyword:
    - ``+must``   — token must appear (all ``+`` tokens are AND'd).
    - ``!block``  — blocks the match (aggregated across all groups).
    - ``plain``   — any-of (all plain tokens within a group are OR'd;
                   groups themselves are OR'd).
    - ``/pattern/`` — regex pattern (case-insensitive), usable as a
                   normal or ``+`` required token.
    - ``@N``    — per-group max hits (e.g. ``@5`` caps this group to
                   5 matched articles). ``0`` = unlimited (default).
    - ``@alias`` — display alias (non-numeric), not used by the matcher.

Matching algorithm (``FrequencyMatcher.match``):

1. If the title hits any ``GLOBAL_FILTER`` line → return False.
2. With no groups defined → return True (matches everything).
3. If the title hits the aggregated block list → return False.
4. For each group: all ``required`` tokens must be present AND at least
   one ``normal`` (or ``required``) token must be present. Matched if
   any group matches.

Hardening:

* ``filter_words`` is deepcopy'd when returned so downstream mutations
  never leak back into the parsed model.
* ``word_groups`` length is capped at 100 groups with ≤ 200 tokens each
  to defend against pathological pastes.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Final, Optional, Union


MAX_GROUPS: Final[int] = 100
MAX_TOKENS_PER_GROUP: Final[int] = 200


# A token is either a plain substring or a compiled regex.
Token = Union[str, "re.Pattern[str]"]


def _token_matches(tok: Token, text_lower: str) -> bool:
    """Test whether *tok* matches inside *text_lower* (already lowered)."""
    if isinstance(tok, re.Pattern):
        return bool(tok.search(text_lower))
    return tok.lower() in text_lower


def _token_label(tok: Token) -> str:
    """Human-readable label for a token (used by ``matched_terms``)."""
    if isinstance(tok, re.Pattern):
        return f"/{tok.pattern}/"
    return tok


@dataclass
class WordGroup:
    required: list[Token] = field(default_factory=list)
    normal: list[Token] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    max_items: int = 0

    def all_terms(self) -> list[Token]:
        return self.required + self.normal


@dataclass
class ParsedRules:
    groups: list[WordGroup] = field(default_factory=list)
    filter_words: list[Token] = field(default_factory=list)
    global_filters: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, list]:
        return {
            "groups": [
                {
                    "required": [_token_label(t) for t in g.required],
                    "normal": [_token_label(t) for t in g.normal],
                    "aliases": list(g.aliases),
                    "max_items": g.max_items,
                }
                for g in self.groups
            ],
            "filter_words": [_token_label(t) for t in self.filter_words],
            "global_filters": list(self.global_filters),
        }


_REGEX_RE = re.compile(r"^/(.+)/([a-z]*)$")


def _try_compile_regex(body: str) -> Optional[re.Pattern]:
    """Attempt to compile a ``/pattern/`` token; return None on failure."""
    m = _REGEX_RE.match(body)
    if not m:
        return None
    try:
        return re.compile(m.group(1), re.IGNORECASE)
    except re.error:
        return None


def _classify_token(line: str) -> tuple[str, Token]:
    """Return ``(kind, token)`` for a raw line.

    Supports ``/pattern/`` regex tokens and ``@N`` per-group max-items
    directives.
    """
    line = line.strip()
    if not line:
        return ("empty", "")
    if line.startswith("#"):
        return ("comment", "")
    if line.startswith("+"):
        inner = line[1:].strip()
        pat = _try_compile_regex(inner)
        if pat is not None:
            return ("required", pat)
        return ("required", inner)
    if line.startswith("!"):
        inner = line[1:].strip()
        pat = _try_compile_regex(inner)
        if pat is not None:
            return ("block", pat)
        return ("block", inner)
    if line.startswith("@"):
        rest = line[1:].strip()
        if rest.isdigit():
            return ("max_items", rest)
        return ("alias", rest)
    pat = _try_compile_regex(line)
    if pat is not None:
        return ("normal", pat)
    return ("normal", line)


def parse_rules(text: str) -> ParsedRules:
    """Parse the frequency DSL text into a :class:`ParsedRules` model.

    The ``deepcopy`` in §13.2 is applied at the accessor — this function
    only *returns* the parsed object. Callers that need a detached copy
    should reach for :func:`snapshot_rules` below.
    """
    rules = ParsedRules()
    in_global = False
    current: WordGroup | None = None
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        if not stripped:
            if current and (current.required or current.normal):
                if len(rules.groups) < MAX_GROUPS:
                    rules.groups.append(current)
                current = None
            continue
        if stripped.upper() == "[GLOBAL_FILTER]":
            in_global = True
            continue
        if stripped.upper() == "[END]":
            in_global = False
            continue
        kind, token = _classify_token(stripped)
        if kind in {"empty", "comment"}:
            continue
        if kind == "max_items":
            if current is None:
                current = WordGroup()
            current.max_items = int(str(token))
            continue
        if not token:
            continue
        if in_global:
            if kind == "block" or kind == "normal":
                rules.global_filters.append(str(token) if isinstance(token, str) else token.pattern)
            continue
        if current is None:
            current = WordGroup()
        if kind == "required" and len(current.required) < MAX_TOKENS_PER_GROUP:
            current.required.append(token)
        elif kind == "normal" and len(current.normal) < MAX_TOKENS_PER_GROUP:
            current.normal.append(token)
        elif kind == "alias":
            current.aliases.append(str(token))
        elif kind == "block":
            rules.filter_words.append(token)
    if current and (current.required or current.normal):
        if len(rules.groups) < MAX_GROUPS:
            rules.groups.append(current)
    return rules


def snapshot_rules(rules: ParsedRules) -> ParsedRules:
    """Deepcopy hardening — see plan §13.2.

    Returned object is fully independent of ``rules`` so the consumer
    can mutate in place (e.g. the UI editor scratchpad) without side
    effects on the cached rules.
    """
    return ParsedRules(
        groups=copy.deepcopy(rules.groups),
        filter_words=copy.deepcopy(rules.filter_words),
        global_filters=copy.deepcopy(rules.global_filters),
    )


@dataclass
class FrequencyMatcher:
    """Matcher wrapping a :class:`ParsedRules`."""

    rules: ParsedRules

    def match(self, title: str) -> bool:
        text = (title or "").lower()
        if not text:
            return False
        for gf in self.rules.global_filters:
            if gf.lower() in text:
                return False
        if not self.rules.groups:
            return True
        for fw in self.rules.filter_words:
            if _token_matches(fw, text):
                return False
        for group in self.rules.groups:
            required_ok = all(
                _token_matches(tok, text) for tok in group.required
            )
            if not required_ok:
                continue
            if group.normal:
                normal_ok = any(_token_matches(tok, text) for tok in group.normal)
                if not normal_ok:
                    continue
            return True
        return False

    def matched_group(self, title: str) -> WordGroup | None:
        """Return the first group that matches *title*, or None."""
        text = (title or "").lower()
        if not text:
            return None
        for gf in self.rules.global_filters:
            if gf.lower() in text:
                return None
        for fw in self.rules.filter_words:
            if _token_matches(fw, text):
                return None
        for group in self.rules.groups:
            required_ok = all(
                _token_matches(tok, text) for tok in group.required
            )
            if not required_ok:
                continue
            if group.normal:
                normal_ok = any(_token_matches(tok, text) for tok in group.normal)
                if not normal_ok:
                    continue
            return group
        return None

    def matched_terms(self, title: str) -> list[str]:
        """Return every group term that appeared in ``title`` — used by
        the Radar preview card.
        """
        text = (title or "").lower()
        hits: list[str] = []
        for group in self.rules.groups:
            for tok in group.all_terms():
                label = _token_label(tok)
                if _token_matches(tok, text) and label not in hits:
                    hits.append(label)
        return hits


def compile_matcher(text: str) -> FrequencyMatcher:
    return FrequencyMatcher(rules=snapshot_rules(parse_rules(text)))


# Defensive: reject plainly non-sensical patterns early so the UI can
# surface an inline error instead of silently eating the save.
_SAFE_TOKEN_RE = re.compile(r"^[^\r\n]{1,64}$")


def validate_token(token: str) -> bool:
    return bool(_SAFE_TOKEN_RE.match(token or ""))


__all__ = [
    "FrequencyMatcher",
    "MAX_GROUPS",
    "MAX_TOKENS_PER_GROUP",
    "ParsedRules",
    "WordGroup",
    "compile_matcher",
    "parse_rules",
    "snapshot_rules",
    "validate_token",
]
