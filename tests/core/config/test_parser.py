"""Unit tests for the config set value parser."""

from __future__ import annotations

import pytest

from dovo.core.config.parser import parse_config_value


class ParseConfigValueTests:
    """Unit tests pinning each coercion rule of parse_config_value."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param('"12345"', "12345", id="quoted-numeric"),
            pytest.param('"true"', "true", id="quoted-bool"),
            pytest.param('""', "", id="empty-quoted"),
            pytest.param('"', '"', id="lone-quote"),
            pytest.param("YES", True, id="yes-upper"),
            pytest.param("no", False, id="no"),
            pytest.param("42", 42, id="int"),
            pytest.param(" 12 ", 12, id="int-padded"),
            pytest.param("1_000", 1000, id="int-underscore"),
            pytest.param("1.5", 1.5, id="float"),
            pytest.param("1e3", 1000.0, id="float-exponent"),
            pytest.param('["A","B"]', ["A", "B"], id="json-list"),
            pytest.param('{"a": 1}', {"a": 1}, id="json-object"),
            pytest.param("[not json", "[not json", id="broken-json"),
            pytest.param("null", "null", id="null"),
            pytest.param("gpt-4.1", "gpt-4.1", id="plain-string"),
        ],
    )
    def test_parse_config_value_coerces_by_first_matching_rule(self, raw: str, expected: object) -> None:
        """Return the FR-1 value for raw, equal to expected and of exactly type(expected)."""
        result = parse_config_value(raw)

        assert result == expected
        assert type(result) is type(expected)
