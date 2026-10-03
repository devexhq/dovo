"""Contract tests for the direct-mode agent attempt pipeline."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from dovo.core.agents import (
    AgentAttempt,
    AgentRequest,
    AgentResponse,
    AgentResponseStatus,
    BaseAgentProvider,
    CliDirectMutationAdapter,
    CliMutationOutcome,
    CliMutationRunRequest,
    ResolvedAgentSettings,
    run_direct_attempt,
)
from dovo.core.git import GitPlumbingTimeoutError, GitRunner
from tests.harness import AGENT_ADAPTER_FACTORY, FakeAgentProvider, new_file_diff

_NO_OP_SUMMARY = "Inspected the repository; no edits were required."


def _settings(provider: str = "ollama") -> ResolvedAgentSettings:
    return ResolvedAgentSettings(provider=provider, model="m", endpoint="http://e", temperature=0.7, max_tokens=512)


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: BaseAgentProvider) -> list[str]:
    requested: list[str] = []

    def _factory(token: str) -> BaseAgentProvider:
        requested.append(token)
        return provider

    monkeypatch.setattr(AGENT_ADAPTER_FACTORY, _factory)
    return requested


def _attempt(worktree: Path, provider: str = "ollama") -> AgentAttempt:
    return run_direct_attempt(
        instruction="Plan the change", settings=_settings(provider), worktree_path=worktree, timeout_seconds=45
    )


def _porcelain(repo: Path) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True).stdout


def _patch(name: str = "a.txt", summary: str | None = None) -> AgentResponse:
    return AgentResponse(status=AgentResponseStatus.PROPOSED_PATCH, unified_diff=new_file_diff(name), summary=summary)


def _no_op(summary: str | None = _NO_OP_SUMMARY, raw_text: str | None = None) -> AgentResponse:
    return AgentResponse(status=AgentResponseStatus.NO_OP, summary=summary, raw_text=raw_text)


def _forbid_git_apply(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def _record(name: str) -> Callable[..., tuple[int, str, str]]:
        def _spy(*args: object, **kwargs: object) -> tuple[int, str, str]:
            calls.append(name)
            raise AssertionError(f"GitRunner.{name} must not run")

        return _spy

    monkeypatch.setattr(GitRunner, "apply_check", staticmethod(_record("apply_check")))
    monkeypatch.setattr(GitRunner, "apply", staticmethod(_record("apply")))
    return calls


class RunDirectAttemptRequestTests:
    def test_request_carries_instruction_settings_worktree_and_timeout(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] run_direct_attempt: ollama settings and timeout_seconds 45 ask the factory for 'ollama' once and propose_fix receives the exact direct AgentRequest."""
        provider = FakeAgentProvider(_no_op())
        requested = _use_provider(monkeypatch, provider)

        _attempt(git_repo)

        assert requested == ["ollama"]
        assert provider.requests == [
            AgentRequest(
                mode="direct",
                instruction="Plan the change",
                payload=None,
                worktree_path=git_repo,
                timeout_seconds=45,
                model="m",
                endpoint="http://e",
                temperature=0.7,
                max_tokens=512,
                max_files=None,
                max_patch_kb=None,
                reject_binary_changes=None,
            )
        ]

    def test_unregistered_provider_returns_provider_error_attempt(self, git_repo: Path) -> None:
        """[tier-1/unit] run_direct_attempt: provider 'openai' returns PROVIDER_ERROR with a diagnostic containing "Unsupported agent provider 'openai' (AGENT_PROVIDER_UNSUPPORTED)"."""
        attempt = _attempt(git_repo, "openai")

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert "Unsupported agent provider 'openai' (AGENT_PROVIDER_UNSUPPORTED)" in attempt.diagnostics[0]

    def test_provider_exception_returns_provider_error_attempt(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] run_direct_attempt: propose_fix raising RuntimeError('boom') returns PROVIDER_ERROR with diagnostics == ['Agent provider error: boom']."""

        class _Exploding(BaseAgentProvider):
            def propose_fix(self, request: AgentRequest) -> AgentResponse:
                raise RuntimeError("boom")

        _use_provider(monkeypatch, _Exploding())

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert attempt.diagnostics == ["Agent provider error: boom"]


class RunDirectAttemptDiffReturningTests:
    def test_clean_patch_applies_unstaged_and_completes(self, git_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """[tier-1/unit] run_direct_attempt: a clean new-file patch from an 'ollama'-token fake returns PROPOSED_PATCH with touched_files == ['a.txt'], writes a.txt unstaged, and stages nothing."""
        _use_provider(monkeypatch, FakeAgentProvider(_patch("a.txt")))

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.PROPOSED_PATCH
        assert attempt.touched_files == ["a.txt"]
        assert (git_repo / "a.txt").read_text(encoding="utf-8") == "hello\n"
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=git_repo, check=True, capture_output=True, text=True
        ).stdout
        assert staged == ""

    @pytest.mark.parametrize(
        ("diff_kind", "message_fragment"),
        [
            pytest.param("empty", "Agent returned a proposed patch without a diff.", id="empty-diff"),
            pytest.param("unsafe_path", "escapes the worktree", id="unsafe-path"),
            pytest.param("not_applicable", "Patch does not apply cleanly:", id="not-applicable"),
        ],
    )
    def test_unusable_patch_returns_provider_error_without_changing_worktree(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, diff_kind: str, message_fragment: str
    ) -> None:
        """[tier-1/unit] run_direct_attempt: an empty, '../x'-targeting, or context-mismatched PROPOSED_PATCH returns PROVIDER_ERROR with the fragment in diagnostics, touched_files == [], and an unchanged git status --porcelain."""
        diffs = {
            "empty": "",
            "unsafe_path": new_file_diff("../x"),
            "not_applicable": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1 @@\n"
                "-this line is not in the file\n"
                "+replacement\n"
            ),
        }
        _use_provider(
            monkeypatch,
            FakeAgentProvider(AgentResponse(status=AgentResponseStatus.PROPOSED_PATCH, unified_diff=diffs[diff_kind])),
        )
        before = _porcelain(git_repo)

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert message_fragment in "\n".join(attempt.diagnostics)
        assert attempt.touched_files == []
        assert _porcelain(git_repo) == before

    def test_apply_failure_after_successful_check_returns_provider_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] run_direct_attempt: GitRunner.apply returning (1, '', 'boom') after a passing apply_check returns PROVIDER_ERROR with diagnostics == ['Patch application failed: boom']."""
        _use_provider(monkeypatch, FakeAgentProvider(_patch()))
        monkeypatch.setattr(GitRunner, "apply", staticmethod(lambda path, diff_text: (1, "", "boom")))

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert attempt.diagnostics == ["Patch application failed: boom"]

    def test_git_error_during_apply_returns_provider_error(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] run_direct_attempt: GitRunner.apply_check raising GitPlumbingTimeoutError returns PROVIDER_ERROR whose first diagnostic starts with 'Patch application failed:'."""
        _use_provider(monkeypatch, FakeAgentProvider(_patch()))

        def _timeout(path: Path, diff_text: str) -> tuple[int, str, str]:
            raise GitPlumbingTimeoutError("git apply timed out")

        monkeypatch.setattr(GitRunner, "apply_check", staticmethod(_timeout))

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert attempt.diagnostics[0].startswith("Patch application failed:")

    @pytest.mark.parametrize(
        ("summary", "raw_text", "expected_summary"),
        [
            pytest.param(_NO_OP_SUMMARY, None, _NO_OP_SUMMARY, id="summary"),
            pytest.param(None, "plan:\n1. do x", "plan:\n1. do x", id="raw-text-fallback"),
        ],
    )
    def test_no_op_completes_without_touching_worktree(
        self,
        git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        summary: str | None,
        raw_text: str | None,
        expected_summary: str,
    ) -> None:
        """[tier-1/unit] run_direct_attempt: a NO_OP response returns NO_OP with summary == summary or the raw_text fallback, an unchanged working tree, and zero apply_check/apply calls."""
        _use_provider(monkeypatch, FakeAgentProvider(_no_op(summary=summary, raw_text=raw_text)))
        apply_calls = _forbid_git_apply(monkeypatch)
        before = _porcelain(git_repo)

        attempt = _attempt(git_repo)

        assert attempt.status == AgentResponseStatus.NO_OP
        assert attempt.summary == expected_summary
        assert _porcelain(git_repo) == before
        assert apply_calls == []


class _EditingAdapter(CliDirectMutationAdapter):
    """Real direct-mutation adapter that writes the given files instead of invoking a tool."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def _default_run(self, request: CliMutationRunRequest) -> CliMutationOutcome:
        for rel, content in self._files.items():
            (request.worktree_path / rel).write_text(content, encoding="utf-8")
        return CliMutationOutcome(status="finished", result_text="done")


class RunDirectAttemptDirectMutationTests:
    def test_accepted_mutation_records_sorted_touched_paths_without_reapplying(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] run_direct_attempt: a 'cursor'-token fake that writes b.txt and a.txt then returns PROPOSED_PATCH returns touched_files == ['a.txt', 'b.txt'] and never calls apply or apply_check."""

        def _write_files(request: AgentRequest) -> None:
            (request.worktree_path / "b.txt").write_text("hello\n", encoding="utf-8")
            (request.worktree_path / "a.txt").write_text("hello\n", encoding="utf-8")

        diff = new_file_diff("b.txt") + new_file_diff("a.txt")
        provider = FakeAgentProvider(
            AgentResponse(status=AgentResponseStatus.PROPOSED_PATCH, unified_diff=diff), on_call=_write_files
        )
        requested = _use_provider(monkeypatch, provider)
        apply_calls = _forbid_git_apply(monkeypatch)

        attempt = _attempt(git_repo, "cursor")

        assert requested == ["cursor"]
        assert attempt.status == AgentResponseStatus.PROPOSED_PATCH
        assert attempt.touched_files == ["a.txt", "b.txt"]
        assert apply_calls == []

    def test_mutation_over_shared_gate_limit_returns_provider_error_and_rolls_back(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """[tier-1/unit] run_direct_attempt: a real CliDirectMutationAdapter subclass writing 31 files (default max_files 30) returns PROVIDER_ERROR and leaves git status --porcelain empty."""
        files = {f"f{index}.txt": "x\n" for index in range(31)}
        _use_provider(monkeypatch, _EditingAdapter(files))

        attempt = _attempt(git_repo, "cursor")

        assert attempt.status == AgentResponseStatus.PROVIDER_ERROR
        assert _porcelain(git_repo) == ""

    @pytest.mark.parametrize(
        "status",
        [
            pytest.param(AgentResponseStatus.TIMEOUT, id="timeout"),
            pytest.param(AgentResponseStatus.UNFIXABLE, id="unfixable"),
            pytest.param(AgentResponseStatus.PROVIDER_ERROR, id="provider-error"),
        ],
    )
    def test_failed_statuses_with_partial_edits_map_straight_through(
        self, git_repo: Path, monkeypatch: pytest.MonkeyPatch, status: AgentResponseStatus
    ) -> None:
        """[tier-1/unit] run_direct_attempt: a 'cursor'-token fake leaving an edit then returning TIMEOUT/UNFIXABLE/PROVIDER_ERROR returns that status with touched_files == [] and the edit still on disk."""

        def _leave_edit(request: AgentRequest) -> None:
            (request.worktree_path / "partial.txt").write_text("partial\n", encoding="utf-8")

        _use_provider(monkeypatch, FakeAgentProvider(AgentResponse(status=status), on_call=_leave_edit))

        attempt = _attempt(git_repo, "cursor")

        assert attempt.status == status
        assert attempt.touched_files == []
        assert (git_repo / "partial.txt").read_text(encoding="utf-8") == "partial\n"
