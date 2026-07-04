"""Unit tests for slugify_template_id (F-4 §A-1)."""

from __future__ import annotations

from openakita.orgs._slug import slugify_template_id


def test_ascii_passthrough_with_kebab_case():
    """A clean kebab-case ASCII id is its own slug (idempotent)."""
    assert slugify_template_id("software-team") == "software-team"
    assert slugify_template_id("content-ops") == "content-ops"


def test_ascii_with_spaces_and_mixed_case():
    """Spaces collapse to ``-``; uppercase lowercases."""
    assert slugify_template_id("Content Ops Team") == "content-ops-team"
    assert slugify_template_id("  My  Org   ") == "my-org"
    assert slugify_template_id("Already-Has-Dashes") == "already-has-dashes"


def test_cjk_only_falls_back_to_hash_prefix():
    """Pure-CJK input has no ASCII chars; deterministic hash fallback."""
    # The legitimate "regression sample" that triggered F-4 in the
    # smoke report: a user-saved template with a pure-CJK name.
    slug = slugify_template_id("内容运营团队")
    assert slug.startswith("tpl-"), slug
    assert len(slug) == 12, slug  # "tpl-" + 8 hex chars
    assert slug.isascii(), slug

    # Determinism: same input -> same slug across calls (so a retry
    # of save_as_template on the same org name does not collide).
    assert slugify_template_id("内容运营团队") == slug

    # Different CJK inputs -> different slugs (hash space disambiguates).
    assert slugify_template_id("内容运营团队") != slugify_template_id("软件研发团队")


def test_mixed_ascii_and_cjk_keeps_ascii_part():
    """If some ASCII chars survive, use them; do NOT fall back to hash."""
    # The CJK chars are stripped; the ASCII tail remains.
    assert slugify_template_id("Team 内容运营") == "team"
    assert slugify_template_id("foo-内容-bar") == "foo--bar".replace("--", "-")  # collapse


def test_empty_and_whitespace_only_input():
    """Empty / whitespace-only fall back to stable empty-bytes hash."""
    # md5(b"")[:8] == "d41d8cd9"
    expected = "tpl-d41d8cd9"
    assert slugify_template_id("") == expected
    assert slugify_template_id("   ") == expected
    assert slugify_template_id("\t\n") == expected


def test_emoji_only_falls_back_to_hash():
    """Pure-emoji / symbol input also routes to hash fallback."""
    slug = slugify_template_id("✨🚀")
    assert slug.startswith("tpl-") and slug.isascii()


def test_unicode_letters_with_diacritics_become_ascii():
    """NFKD strips accents so "Cafe" survives from "Café"."""
    # "Café" -> NFKD -> "Cafe" + combining acute -> "cafe" after ASCII-only.
    assert slugify_template_id("Café") == "cafe"
    assert slugify_template_id("Über Org") == "uber-org"


def test_special_chars_dropped():
    """Punctuation that is not ``-`` / ``_`` is dropped."""
    assert slugify_template_id("foo!bar?baz") == "foobarbaz"
    assert slugify_template_id(r"a/b\c") == "abc"


def test_result_is_always_url_safe_ascii():
    """Property: every output is non-empty and ASCII-safe."""
    samples = [
        "Hello World",
        "内容运营团队",
        "software-team",
        "Café",
        "✨emoji✨",
        "",
        "🚀",
        "a b   c",
        "Already-Slugged",
    ]
    for sample in samples:
        slug = slugify_template_id(sample)
        assert slug, f"empty slug for {sample!r}"
        assert slug.isascii(), f"non-ascii slug {slug!r} for {sample!r}"
        # No leading/trailing dashes/underscores leak through.
        assert slug == slug.strip("-_"), f"trim leak in {slug!r}"
