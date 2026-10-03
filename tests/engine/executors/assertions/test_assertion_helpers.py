"""Contract tests for engine/executors/assertions/assertion_helpers.py: failure-value formatting."""

from __future__ import annotations

from dovo.engine.executors.assertions.assertion_helpers import safe_repr, short_pair, short_repr, shorten


class _BrokenRepr:
    def __repr__(self) -> str:
        raise RuntimeError("no repr")


class SafeReprTests:
    def test_returns_repr_unchanged_by_default(self) -> None:
        """[tier-1/unit] safe_repr: without short=True the full repr is returned, however long."""
        value = "x" * 200

        assert safe_repr(value) == repr(value)

    def test_short_mode_truncates_long_reprs_with_marker(self) -> None:
        """[tier-1/unit] safe_repr: with short=True a repr of 80 or more characters keeps its first 80 and appends ' [truncated]...'."""
        value = "x" * 200

        assert safe_repr(value, short=True) == repr(value)[:80] + " [truncated]..."

    def test_falls_back_to_object_repr_when_repr_raises(self) -> None:
        """[tier-1/unit] safe_repr: a value whose __repr__ raises is rendered with object.__repr__ instead of propagating."""
        value = _BrokenRepr()

        assert safe_repr(value) == object.__repr__(value)


class ShortenTests:
    def test_collapses_a_long_middle_into_a_char_count(self) -> None:
        """[tier-1/unit] shorten: text whose omitted span exceeds 12 characters keeps its prefix and suffix around '[N chars]'."""
        assert shorten("a" * 5 + "m" * 20 + "z" * 5, 5, 5) == "aaaaa[20 chars]zzzzz"

    def test_leaves_text_unchanged_when_collapsing_saves_nothing(self) -> None:
        """[tier-1/unit] shorten: an omitted span of 12 characters or fewer returns the text unchanged."""
        text = "a" * 5 + "m" * 12 + "z" * 5

        assert shorten(text, 5, 5) == text


class ShortReprTests:
    def test_short_values_are_returned_as_plain_repr(self) -> None:
        """[tier-1/unit] short_repr: a short value renders as its plain repr."""
        assert short_repr({"a": 1}) == "{'a': 1}"

    def test_long_values_are_truncated(self) -> None:
        """[tier-1/unit] short_repr: a long value is hard-truncated with the truncation marker."""
        assert short_repr("y" * 200).endswith(" [truncated]...")


class ShortPairTests:
    def test_short_pair_returns_plain_reprs_when_both_fit(self) -> None:
        """[tier-1/unit] short_pair: two values whose reprs fit in 80 characters come back unshortened."""
        assert short_pair(1, 2) == ("1", "2")

    def test_long_pair_keeps_the_differing_region_visible(self) -> None:
        """[tier-1/unit] short_pair: two long reprs sharing a prefix are shortened, both stay under the unshortened length, and the differing tails remain."""
        actual = "p" * 300 + "ACTUAL"
        expected = "p" * 300 + "WANTED"

        shortened_actual, shortened_expected = short_pair(actual, expected)

        assert len(shortened_actual) < len(repr(actual))
        assert "[" in shortened_actual
        assert "ACTUAL" in shortened_actual
        assert "WANTED" in shortened_expected
