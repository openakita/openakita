"""Validators package — W3 Stage 1 (parse issues) + Stage 3 (cross-period).

Each module is independent and stateless (pure functions / dataclasses).  The
service layer in ``routes.py`` wires the detector outputs into the SQLite
``parse_issues`` table and triggers the learning-sample auto-apply pass.

Public entry points
-------------------

* :func:`parse_issue_detector.detect_parse_issues` — runs the 6-class L1 rule
  set against the freshly parsed ``ParsedRow`` list.
* :func:`parse_issue_detector.make_pattern_signature` — stable fingerprint
  used by the learning-sample matcher.
* :func:`cross_period.validate_cross_period` (W3 Stage 3) — diffs two
  trial-balance imports and emits ``CROSS_PERIOD_MISMATCH`` issues.
"""

from __future__ import annotations
