"""Tests for session id generation."""

from __future__ import annotations

import re

import pytest

from dovo.common.session_id import new_session_id


class NewSessionIdTests:
    @pytest.mark.parametrize(
        ("kind", "pattern"),
        [
            pytest.param("blueprint", r"^blueprint_[0-9a-f]{8}$", id="blueprint"),
            pytest.param("dovo", r"^dovo_[0-9a-f]{8}$", id="dovo"),
            pytest.param("my_kind", r"^my_kind_[0-9a-f]{8}$", id="kind_with_underscore"),
        ],
    )
    def test_new_session_id_returns_kind_prefix_and_eight_lowercase_hex(self, kind: str, pattern: str) -> None:
        """[tier-1/unit] new_session_id: returns a string fully matching `<kind>_<8 lowercase hex>` for plain and underscore-containing kinds."""
        assert re.fullmatch(pattern, new_session_id(kind))
