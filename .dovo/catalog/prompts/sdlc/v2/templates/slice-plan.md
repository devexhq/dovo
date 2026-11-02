# Slice plan example

A filled-in `.agentic/plan.md` as `/issue-plan --slice <id>` writes it ([issue-plan-slice.md](../issue-plan-slice.md)): slice S1 of a plain issue, #914, whose `plan.json` is the example in [issue-plan.md](../issue-plan.md) Step 5. Its sections, their order, their headings and the form of each line are the rule; its content is only an example. On a slice of an epic, the epic plan's `## Feature contract` comes before `## Slices`, and both are copied verbatim.

````markdown
# Issue #914: List discarded sessions

Slice S1 of #914.

Planning only. Grounded against `main` at `8eb591cd` (2026-10-29).

The issue's contract is not repeated here; read it on #914. This plan covers how each requirement in `.agentic/requirements.json` lands, in which phase, and how it is tested. The [Phases](#phases) table maps every requirement to its phase, and each test line cites the requirements it pins.

## Slices

| Slice | Title | Outcomes | Depends on | Blocked by | Pilot or closing docs | Estimate | Size |
|---|---|---|---|---|---|---|---|
| S1 | List discarded sessions | O-1, O-2 | none | none | neither | 180 src and docs, 220 tests | s |

## Changes to existing behaviour

1. **`dovo session list` hides discarded sessions (phase 1).** Today it lists every record of the project, discarded ones included (`src/dovo/core/db/repositories/sessions.py:182-200`). After, a session whose `discard` is set (`src/dovo/core/db/models.py:179`) is listed only with `--all` [See OQ-1.].

## Traps

- **JSON output must hide them too** (`src/dovo/cli/session/commands/list.py:28-34`). Terminal and JSON output render the same `HistoryListResult`, so filter in the core, never in the formatter.
- **`--limit` counts listed sessions** (phase 1, `src/dovo/core/db/repositories/sessions.py:197-198`). Filtering after the query's `LIMIT` returns fewer sessions than asked for; filter in the query.

## Owned elsewhere

- **#1197 owns renaming the `SessionStatus` values** (from the issue). This slice filters on `discard`, not on a status: no overlap.

## Phases

| Phase | Ships | Requirements | Depends on | Est. changed lines | Risk, and where to look |
|---|---|---|---|---|---|
| 1 | `SessionCollection.list` hides discarded sessions unless asked for them | O-1 (hidden) | none | 140 (60 src, 80 tests) | **Low.** The limit applies after the filter. |
| 2 | `dovo session list --all`, the empty-state line and the reference page | O-1 (empty state), O-2 | 1 | 260 (120 src and docs, 140 tests) | **Low.** The R13 matrix for `--all`, and `dovo session` forwarding it. |

The phases are sequential.

### Deletion ledger

Deletes nothing: new surface only.

## Artifact inventory

| Artifact | Path | New or changed | Phase |
|---|---|---|---|
| `SessionCollection.list` | `src/dovo/core/sessions/sessions.py:244` | changed: an `include_discarded` keyword | 1 |
| `SessionsRepository.list` | `src/dovo/core/db/repositories/sessions.py:182` | changed: leaves out discarded records in the query | 1 |
| `session_list_command`, `session_list`, `session_callback` | `src/dovo/cli/session/commands/list.py:11`, `src/dovo/cli/session/app.py:62`, `:30` | changed: `--all` | 2 |
| `_render_list_runs` empty state | `src/dovo/cli/ui/formatters/session/session_list.py:21` | changed: the line D-1 settles | 2 |
| `dovo session list` reference | `docs/cli/session.md:38-50` | changed: `--all` | 2 |

Not needed: migration (`discard` exists), `*Result` model, config key.

## Phase 1: Hide discarded sessions

### Code

```python
def list(
    self, *, limit: int | None = 20, status: str | None = None, include_discarded: bool = False
) -> HistoryListResult: ...
```

### Tests

- `SessionCollectionListTests.test_list_leaves_out_discarded_sessions` (`tests/core/sessions/test_sessions.py`) — O-1 (hidden): a discarded session is not in `sessions`, and a kept one is.
- `SessionCollectionListTests.test_list_with_include_discarded_lists_every_session` (`tests/core/sessions/test_sessions.py`) — O-1 (hidden): with `include_discarded`, the discarded session is listed with the kept one.
- `SessionCollectionListTests.test_list_limit_counts_only_listed_sessions` (`tests/core/sessions/test_sessions.py`) — O-1 (hidden): with more discarded sessions than the limit, the limit still fills with kept sessions.

## Phase 2: Show them with --all

### Code

None: the issue pins it (`#914, Outcomes O-2`).

### Decisions

- **D-1: the empty list reuses `dovo worktree list`'s empty-state line** (`src/dovo/cli/ui/formatters/worktree/worktree_list.py:21`), so the two lists read alike. Rejected: the issue's "No sessions." line.

### Tests

- `SessionListCliIntegrationTests.test_session_list_all_lists_discarded_sessions` (`tests/cli/session/test_session_list.py`) — O-2: parametrized over terminal and `--format json` output; with `--all` the discarded session is listed with its discarded status, and the command exits 0.
- `SessionListCliIntegrationTests.test_session_list_all_under_lock_timeout_exits_one` (`tests/cli/session/test_session_list.py`) — O-2: with the workspace lock held, `--all` fails as the plain list does.
- `SessionListCliIntegrationTests.test_bare_session_forwards_all` (`tests/cli/session/test_session_list.py`) — O-2: `dovo session --all` lists what `dovo session list --all` lists.
- `SessionListFormatterTests.test_rich_render_shows_the_empty_state` (`tests/cli/ui/formatters/session/test_session_list.py`) — O-1 (empty state): a list whose every session is hidden renders the empty-state line D-1 settles, not a table.

## Cross-cutting

- **Docs gates that fire:** `docs/cli/session.md` gains `--all` in its option table and its note on `dovo session` (phase 2).
- **Validation:** `uv run dovo run tests --path tests/core/sessions/ tests/cli/session/ tests/cli/ui/formatters/session/`, then `uv run dovo run quality`.
````
