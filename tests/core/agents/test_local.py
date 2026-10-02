"""Tests for the local subprocess agent adapter."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

from worktree.core.agents import AgentRequest, AgentResponseStatus, LocalAgentAdapter
from worktree.core.agents.local import LOCAL_AGENT_CMD_ENV

_RECORDING_SCRIPT = """\
import sys
from pathlib import Path

Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())
print('{"summary": "planned"}')
"""


class LocalAgentAdapterTests:
    def test_stdin_json_carries_instruction_mode_and_null_payload(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] LocalAgentAdapter.propose_fix: with WORKTREE_LOCAL_AGENT_CMD pointing at a python script that records stdin, a direct request delivers JSON with mode 'direct', the instruction, payload null, and the sandbox path; the reply maps to NO_OP."""
        script = tmp_path / "record.py"
        script.write_text(_RECORDING_SCRIPT, encoding="utf-8")
        recorded = tmp_path / "stdin.json"
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        monkeypatch.setenv(
            LOCAL_AGENT_CMD_ENV,
            " ".join(shlex.quote(part) for part in (sys.executable, str(script), str(recorded))),
        )
        request = AgentRequest(mode="direct", instruction="Plan the change", sandbox_path=sandbox, timeout_seconds=30)

        response = LocalAgentAdapter().propose_fix(request)

        assert response.status == AgentResponseStatus.NO_OP
        assert response.summary == "planned"
        sent = json.loads(recorded.read_text(encoding="utf-8"))
        assert sent["mode"] == "direct"
        assert sent["instruction"] == "Plan the change"
        assert sent["payload"] is None
        assert sent["sandbox_path"] == str(sandbox)
