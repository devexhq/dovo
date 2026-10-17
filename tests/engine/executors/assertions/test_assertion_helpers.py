"""Contract tests for engine/executors/assertions/assertion_helpers.py: failure-value formatting."""

from __future__ import annotations

import pytest

from dovo.engine.executors.assertions.assertion_helpers import safe_repr, short_pair, short_repr, shorten


class _BrokenRepr:
    def __repr__(self) -> str:
        raise RuntimeError("no repr")


class SafeReprTests:
    def test_falls_back_to_object_repr_when_repr_raises(self) -> None:
        value = _BrokenRepr()

        assert safe_repr(value) == object.__repr__(value)


class ShortenTests:
    @pytest.mark.parametrize(
        ("middle", "expected"),
        [
            pytest.param("m" * 20, "aaaaa[20 chars]zzzzz", id="collapses_long_middle"),
            pytest.param("m" * 12, "aaaaa" + "m" * 12 + "zzzzz", id="unchanged_when_collapsing_saves_nothing"),
        ],
    )
    def test_shorten_collapses_the_middle_only_when_it_saves_space(self, middle: str, expected: str) -> None:
        assert shorten("a" * 5 + middle + "z" * 5, 5, 5) == expected


class ShortReprTests:
    def test_long_values_are_truncated_at_80_characters(self) -> None:
        value = "y" * 200

        assert short_repr(value) == repr(value)[:80] + " [truncated]..."


class ShortPairTests:
    def test_short_pair_returns_plain_reprs_when_both_fit(self) -> None:
        assert short_pair(1, 2) == ("1", "2")

    def test_long_pair_keeps_the_differing_region_visible(self) -> None:
        actual = "p" * 300 + "ACTUAL"
        expected = "p" * 300 + "WANTED"

        shortened_actual, shortened_expected = short_pair(actual, expected)

        assert len(shortened_actual) < len(repr(actual))
        assert "[" in shortened_actual
        assert "ACTUAL" in shortened_actual
        assert "WANTED" in shortened_expected
