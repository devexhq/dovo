"""Tier 1 unit tests for process isolation and platform primitives."""

from __future__ import annotations

import subprocess
import sys

import pytest

from dovo.common.process import get_isolated_process_kwargs

WINDOWS_CREATION_FLAGS: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

PLATFORM_ISOLATION_CASES = [
    pytest.param("linux", {"start_new_session": True}, id="posix_linux"),
    pytest.param("darwin", {"start_new_session": True}, id="posix_darwin"),
    pytest.param("win32", {"creationflags": WINDOWS_CREATION_FLAGS}, id="windows_win32"),
]


class ProcessIsolationPlatformTests:
    """Unit tests verifying subprocess process group isolation flags across platforms."""

    @pytest.mark.parametrize(("platform_name", "expected_kwargs"), PLATFORM_ISOLATION_CASES)
    def test_isolated_process_flags_match_platform_primitives(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform_name: str,
        expected_kwargs: dict[str, object],
    ) -> None:
        """Verify get_isolated_process_kwargs returns correct session/creation flags per platform."""
        monkeypatch.setattr(sys, "platform", platform_name)
        assert get_isolated_process_kwargs() == expected_kwargs

    def test_ambient_platform_isolation_flags_match_system(self) -> None:
        """Verify get_isolated_process_kwargs returns valid flags for the ambient host platform without monkeypatching."""
        expected = {"creationflags": WINDOWS_CREATION_FLAGS} if sys.platform == "win32" else {"start_new_session": True}
        assert get_isolated_process_kwargs() == expected
