"""Helpers for formatting assertion failure values.

Truncation behavior is adapted from CPython's ``unittest.util``
(``safe_repr``, ``_shorten``, ``_common_shorten_repr``) so long strings and
large objects stay readable in failure messages without importing private
unittest APIs.

Reference implementation (CPython 3.13.13):
https://github.com/python/cpython/blob/v3.13.13/Lib/unittest/util.py
"""

from __future__ import annotations

from os.path import commonprefix
from typing import Any

_MAX_LENGTH = 80
_PLACEHOLDER_LEN = 12
_MIN_BEGIN_LEN = 5
_MIN_END_LEN = 5
_MIN_COMMON_LEN = 5
_MIN_DIFF_LEN = _MAX_LENGTH - (_MIN_BEGIN_LEN + _PLACEHOLDER_LEN + _MIN_COMMON_LEN + _PLACEHOLDER_LEN + _MIN_END_LEN)


def safe_repr(value: Any, *, short: bool = False) -> str:
    """Return ``repr(value)``, optionally hard-truncated when longer than ``_MAX_LENGTH``.

    Falls back to ``object.__repr__`` if ``repr`` raises. When ``short`` is true
    and the result exceeds ``_MAX_LENGTH``, keeps the first ``_MAX_LENGTH``
    characters and appends `` [truncated]...``.
    """
    try:
        result = repr(value)
    except Exception:
        result = object.__repr__(value)
    if not short or len(result) < _MAX_LENGTH:
        return result
    return result[:_MAX_LENGTH] + " [truncated]..."


def shorten(text: str, prefix_len: int, suffix_len: int) -> str:
    """Collapse the middle of ``text`` into a ``[N chars]`` placeholder.

    Keeps ``prefix_len`` leading and ``suffix_len`` trailing characters. If the
    omitted span is not longer than ``_PLACEHOLDER_LEN``, returns ``text``
    unchanged (placeholder would not save space).
    """
    skip = len(text) - prefix_len - suffix_len
    if skip > _PLACEHOLDER_LEN:
        return f"{text[:prefix_len]}[{skip} chars]{text[len(text) - suffix_len :]}"
    return text


def common_shorten_repr(left: Any, right: Any) -> tuple[str, str]:
    """Return paired reprs shortened around their first differing region.

    When either full ``repr`` is longer than ``_MAX_LENGTH``, shares a common
    shortened prefix and keeps a window of differing characters so failure
    messages stay comparable side-by-side (unittest-style).
    """
    left_repr = safe_repr(left)
    right_repr = safe_repr(right)
    max_len = max(len(left_repr), len(right_repr))
    if max_len <= _MAX_LENGTH:
        return left_repr, right_repr

    prefix = commonprefix([left_repr, right_repr])
    prefix_len = len(prefix)
    common_len = _MAX_LENGTH - (max_len - prefix_len + _MIN_BEGIN_LEN + _PLACEHOLDER_LEN)
    if common_len > _MIN_COMMON_LEN:
        shortened_prefix = shorten(prefix, _MIN_BEGIN_LEN, common_len)
        return (
            shortened_prefix + left_repr[prefix_len:],
            shortened_prefix + right_repr[prefix_len:],
        )

    shortened_prefix = shorten(prefix, _MIN_BEGIN_LEN, _MIN_COMMON_LEN)
    return (
        shortened_prefix + shorten(left_repr[prefix_len:], _MIN_DIFF_LEN, _MIN_END_LEN),
        shortened_prefix + shorten(right_repr[prefix_len:], _MIN_DIFF_LEN, _MIN_END_LEN),
    )


def short_repr(value: Any) -> str:
    """Single-value repr truncated like unittest failure output."""
    return safe_repr(value, short=True)


def short_pair(actual: Any, expected: Any) -> tuple[str, str]:
    """Paired reprs shortened around the first differing region (unittest-style)."""
    return common_shorten_repr(actual, expected)
