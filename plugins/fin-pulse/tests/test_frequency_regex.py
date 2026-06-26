"""Regex keyword matching and @N per-group limit tests."""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from finpulse_frequency import compile_matcher, parse_rules


class TestRegexTokens:
    def test_regex_normal_token_matches(self) -> None:
        m = compile_matcher("/美联储|ECB/")
        assert m.match("美联储宣布加息") is True
        assert m.match("ECB announced rate cut") is True
        assert m.match("央行普通新闻") is False

    def test_regex_required_token(self) -> None:
        m = compile_matcher("+/fed|ecb/\n利率")
        assert m.match("Fed discusses 利率 policy") is True
        assert m.match("利率 policy update") is False

    def test_regex_block_token(self) -> None:
        m = compile_matcher("+央行\n!/广告|推广/")
        assert m.match("央行重要公告") is True
        assert m.match("央行推广活动") is False
        assert m.match("央行广告合作") is False

    def test_invalid_regex_falls_back_to_normal(self) -> None:
        rules = parse_rules("/[invalid/")
        assert len(rules.groups) == 1
        assert isinstance(rules.groups[0].normal[0], str)

    def test_regex_case_insensitive(self) -> None:
        m = compile_matcher("/fomc/")
        assert m.match("FOMC Statement Released") is True
        assert m.match("fomc minutes") is True


class TestMaxItemsDirective:
    def test_at_n_parsed_correctly(self) -> None:
        rules = parse_rules("+美联储\n加息\n@5")
        assert len(rules.groups) == 1
        assert rules.groups[0].max_items == 5

    def test_at_n_zero_means_unlimited(self) -> None:
        rules = parse_rules("+A\n@0")
        assert rules.groups[0].max_items == 0

    def test_at_alias_not_confused_with_max_items(self) -> None:
        rules = parse_rules("+A\n@fed_label")
        assert rules.groups[0].max_items == 0
        assert "fed_label" in rules.groups[0].aliases


class TestMatchedGroup:
    def test_returns_matching_group(self) -> None:
        m = compile_matcher("+Fed\n\n+ECB")
        g = m.matched_group("Fed statement")
        assert g is not None
        assert any((t if isinstance(t, str) else t.pattern) == "Fed" for t in g.required)

    def test_returns_none_for_no_match(self) -> None:
        m = compile_matcher("+Fed")
        assert m.matched_group("BoJ announcement") is None


class TestRegexMatchedTerms:
    def test_regex_terms_in_matched_terms(self) -> None:
        m = compile_matcher("/fed|ecb/\n利率")
        terms = m.matched_terms("Fed discusses 利率")
        assert "/fed|ecb/" in terms
        assert "利率" in terms
