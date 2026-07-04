"""Account-code normalization (P1, migrated from ``tmp_spike/04_account_code``).

中国企业会计准则一级科目都是 4 位（``1001`` 库存现金、``1002`` 银行存款 …）；
明细科目的实际写法五花八门，归一化策略详见 spike summary（13/13 通过）：

* 含 ``.`` 分隔符的 → 左边裁到 4 位作 parent、右边作 child
* 纯数字 ≤ 4 位 → parent 左 padding 到 4 位、child=None
* 纯数字 > 4 位 → 前 4 位 parent、剩余作 child
* 含非数字前缀 → 提取前导数字段后再走前面的分支
* 空字符串 → ``("", None)``

The implementation here is functionally identical to the spike's
``normalize.py``; we just drop the test driver so the production module is
import-clean.  The 13-case golden table from the spike is moved into the
plugin's test directory (M1 W2) for regression coverage.
"""

from __future__ import annotations

import re

LEVEL1_LEN = 4
_DOT_RE = re.compile(r"^(\d{3,4})[.](.+)$")
_LEADING_DIGITS = re.compile(r"^(\d+)")


def normalize_account_code(raw: str) -> tuple[str, str | None]:
    """Return ``(parent_code, child_code_or_None)``.

    Empty input collapses to ``("", None)`` so callers can detect "no code at
    all" without a separate exception path.
    """
    s = (raw or "").strip()
    if not s:
        return ("", None)

    m = _DOT_RE.match(s)
    if m:
        parent_raw, child = m.group(1), m.group(2)
        parent = parent_raw.zfill(LEVEL1_LEN)[:LEVEL1_LEN]
        child = (child or "").strip().rstrip(".")
        return (parent, child or None)

    if s.isdigit():
        if len(s) <= LEVEL1_LEN:
            return (s.zfill(LEVEL1_LEN), None)
        return (s[:LEVEL1_LEN], s[LEVEL1_LEN:])

    digits_match = _LEADING_DIGITS.match(s)
    if digits_match:
        digits = digits_match.group(1)
        rest = s[len(digits):].lstrip(".")
        if len(digits) <= LEVEL1_LEN:
            return (digits.zfill(LEVEL1_LEN), rest or None)
        composed_child = digits[LEVEL1_LEN:]
        if rest:
            composed_child = (composed_child + "." + rest) if composed_child else rest
        return (digits[:LEVEL1_LEN], composed_child or None)

    return (s, None)


def join_full_code(parent: str, child: str | None) -> str:
    """Render parent + child into a single canonical string (used in DB)."""
    if not parent:
        return ""
    if not child:
        return parent
    return f"{parent}.{child}"
