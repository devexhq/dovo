"""Tests of the e2e command coverage check, without the binary."""

from __future__ import annotations

import pytest

from e2e.command_coverage import (
    CommandRecorder,
    CommandTree,
    CoverageReport,
    compare,
    parse_completion,
    read_command_tree,
    read_gap_list,
)

ROOT_REPLY = """_arguments '*: :(("logs":"Show a session''s timeline, or one step''s..."
"init":"Initialize Dovo CLI in the current directory."
"session":"List, inspect and resume blueprint..."
"run":"Execute any blueprint by name."))'"""
SESSION_REPLY = """_arguments '*: :(("list":"List past blueprint execution sessions."
"show":"Show detailed metadata and error messages..."))'"""
REPLIES = {
    (): ROOT_REPLY,
    ("session",): SESSION_REPLY,
}
TREE = CommandTree(
    children={
        (): ("logs", "init", "session", "run"),
        ("logs",): (),
        ("init",): (),
        ("session",): ("list", "show"),
        ("run",): (),
        ("session", "list"): (),
        ("session", "show"): (),
    }
)


class CommandCoverageTests:
    """The pure logic deciding which commands the e2e scenarios cover."""

    def test_parse_completion_returns_group_names_with_quotes_unescaped(self) -> None:
        reply = """_arguments '*: :(("it''s":"Has a quote."
"plain"
"list":"List things."))'"""

        assert parse_completion(reply) == ("it's", "plain", "list")

    @pytest.mark.parametrize("reply", [pytest.param("_files\n", id="leaf"), pytest.param("", id="empty")])
    def test_parse_completion_returns_nothing_for_a_leaf(self, reply: str) -> None:
        assert parse_completion(reply) == ()

    def test_read_command_tree_walks_every_group(self) -> None:
        tree = read_command_tree(lambda path: REPLIES.get(path, "_files"))

        assert tree == TREE

    def test_leaves_are_commands_and_exclude_parent_groups(self) -> None:
        assert TREE.leaves() == frozenset({"logs", "init", "run", "session list", "session show"})

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            pytest.param(["session", "list", "--format", "json"], "session list", id="subcommand"),
            pytest.param(["-p", "/x", "init"], "init", id="root_path_option"),
            pytest.param(["--path", "session", "init"], "init", id="root_path_value_names_a_command"),
            pytest.param(["-v", "session", "show", "id"], "session show", id="root_flag"),
            pytest.param(["run", "list", "--session-id", "s"], "run", id="leaf_argument_names_a_command"),
            pytest.param(["logs", "id"], "logs", id="shortcut"),
            pytest.param(["session"], None, id="bare_group"),
            pytest.param(["--help"], None, id="help"),
            pytest.param([], None, id="bare_binary"),
            pytest.param(["bogus"], None, id="unknown"),
        ],
    )
    def test_resolve_maps_argv_to_the_command_it_runs(self, argv: list[str], expected: str | None) -> None:
        assert TREE.resolve(argv) == expected

    def test_read_gap_list_ignores_comments_and_blank_lines(self) -> None:
        text = "# header\n\nstep delete\n  session  logs  # trailing comment\n"

        assert read_gap_list(text) == frozenset({"step delete", "session logs"})

    def test_compare_names_an_uncovered_command_not_listed(self) -> None:
        report = compare(frozenset({"init", "run"}), frozenset({"init"}), frozenset())

        assert report == CoverageReport(uncovered=("run",), stale_gaps=(), unknown_gaps=())
        assert not report.ok

    def test_compare_names_a_listed_command_that_gained_a_scenario(self) -> None:
        report = compare(frozenset({"init", "run"}), frozenset({"init", "run"}), frozenset({"run"}))

        assert report == CoverageReport(uncovered=(), stale_gaps=("run",), unknown_gaps=())
        assert not report.ok

    def test_compare_names_a_listed_command_the_binary_does_not_offer(self) -> None:
        report = compare(frozenset({"init"}), frozenset({"init"}), frozenset({"gone"}))

        assert report == CoverageReport(uncovered=(), stale_gaps=(), unknown_gaps=("gone",))
        assert not report.ok

    def test_compare_is_ok_when_every_command_is_covered_or_listed(self) -> None:
        report = compare(frozenset({"init", "run"}), frozenset({"init"}), frozenset({"run"}))

        assert report == CoverageReport(uncovered=(), stale_gaps=(), unknown_gaps=())
        assert report.ok

    def test_report_lines_name_each_command_under_its_group(self) -> None:
        report = CoverageReport(uncovered=("run",), stale_gaps=("init",), unknown_gaps=())

        assert report.lines("gaps.txt") == [
            "commands with no scenario (add a scenario, or a line in gaps.txt):",
            "  run",
            "commands in gaps.txt that now have a scenario (remove their lines):",
            "  init",
        ]

    def test_recorder_keeps_only_invocations_made_while_active(self) -> None:
        recorder = CommandRecorder()
        recorder.record(["init"])
        recorder.active = True
        recorder.record(["session", "list"])

        assert recorder.invocations == [("session", "list")]
        assert recorder.covered(TREE) == frozenset({"session list"})
