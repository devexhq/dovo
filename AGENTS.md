# AGENTS.md

`Dovo` (`dovo`) is a Typer-based CLI providing isolated Git worktree developer
workflows and AI agent workspaces, backed by a local `.dovo/` state directory.

The codebase is the main specification. Read the code around your change and
the [exemplars](#exemplars) before writing anything, and make new code look like
them. The rules below cover what the code cannot show and what is expensive to
get wrong. Mechanical checks live in `tests/lint/`, ruff and basedpyright, so
they are not repeated here.

## How work flows

1. **Plan** (`/dovo-plan`) anything that adds surface: a command, flag, result
   model, status enum, service, formatter, config key, schema field or table.
   The plan sizes the work and splits it into phases. Skip it for a single-file
   change that adds no surface.
2. **One phase, one PR.** Each phase is reviewable in one sitting (roughly 400
   changed lines or less), passes the quality gate on its own and ships
   something. When a change replaces an internal, phase it as add, switch
   callers, delete, with the deletion phase planned up front (see R10).
3. **Implement** a phase (`/dovo-code`), then **review** it (`/dovo-review`) in a
   fresh session, then a human reviews and merges the PR.

## Rules

The coding agent follows these. The review agent reports each violation by its
ID, with the `path:line` that breaks it.

### Architecture

- **R1. Imports flow one way: common → core → engine → cli.** Within `core/`,
  packages follow `core/project → core/{db,git,worktree,catalog,inputs,sessions,status,artifacts,interaction} → core/agents → core/diagnostics`.
  *Why:* each layer can be tested and changed without the ones above it.
  `tests/lint/test_layer_direction.py` enforces the table; design with it rather
  than discovering it at the gate.
- **R2. CLI handlers parse arguments, call one domain entrypoint and dispatch
  its result.** No business logic, SQL or filesystem scans in `cli/`. *Why:*
  every behaviour must be reachable and testable without Typer.
- **R3. Domain facts originate in `core/` or `common/` models.** Status, error
  category, warnings, remediation text and health rules come from the result;
  `cli/ui/` only reshapes them for display. *Why:* terminal and JSON output must
  agree, so both derive from the same result.
- **R4. Business failures are returned, not raised.** Return a `*Result`
  subclassing `BaseResult` whose `status` and `errors` (or `.ok`, where the
  result defines it) the caller branches on. Raise only for programmer errors
  and broken invariants. *Why:* exit codes, the JSON envelope and every caller
  branch on the result, and an exception skips all of them.
- **R5. New work follows its neighbours and the exemplars.** Match the layout,
  naming and idiom of the package you are in. When two existing patterns
  disagree, follow the exemplar and say so in the PR. *Why:* agents copy what
  they see, so drift compounds.

### State and safety

- **R6. Writes under `.dovo/` go through `Filesystem.atomic_write_*`, and
  mutations that can run in more than one process hold `WorkspaceLock`.** Report
  `LockTimeoutError`; never swallow it. *Why:* concurrent `dovo` runs share one
  workspace, and a torn or racing write corrupts it.
- **R7. Secrets come only from environment variables, read at call time.** Never
  write them to `config.json`, `data.db`, logs, artifacts or prompt arguments.
  *Why:* those files are shared, committed or uploaded.
- **R8. Never edit, rename or delete a released migration; every new one is
  reversible.** A revision in a `v*` tag is fixed forward with a new revision.
  Each revision stays dialect-neutral (`op.batch_alter_table`, no SQLite-only
  `PRAGMA`) and implements `upgrade()` and `downgrade()` with tests for both in
  `tests/core/db/test_migrations.py`. *Why:* users' databases are stamped with
  released revisions.

### Compatibility and change shape

- **R9. Never break the public surface:** CLI flags and arguments, the meaning
  of `config.json` and blueprint keys, and the JSON event envelope. Changing one
  needs an issue that mandates it. *Why:* scripts and blueprints outside this
  repo depend on them.
- **R10. Replace internals outright.** No aliases, re-exports, fallbacks, dual
  paths or deprecation windows; update every caller in the same change, or in
  the planned phase that follows. *Why:* shims outlive their reason and the
  next agent copies them.
- **R11. No production parameter, default or hook exists only for tests.**
  Patch or inject at the boundary in the test instead. *Why:* a test seam is a
  production path nothing exercises.

### Tests

- **R12. Tests assert published contracts:** the result model, JSON output, exit
  code, files on disk and git state. Never private members, the call order of
  internals or module layout. Pin each contract once, at the lowest tier that
  can observe it, plus one wiring test above it. *Why:* contract tests survive
  refactors; implementation tests block them.
- **R13. Every command has runner tests for success, a failure path,
  `--format json` and each option it binds.** *Why:* the CLI runner is the only
  tier that proves a flag is wired through.
- **R14. Formatter tests pin the transform view and the exact JSON dict.**
  Render tests assert that the view's values appear, not layout, colour or
  widths. *Why:* values and JSON are the contract; layout is cosmetic.
- **R15. Test doubles subclass a real production type or Protocol**, like
  `FakeAgentProvider` in `tests/harness/fakes.py`. *Why:* an invented double
  keeps passing after the real interface changes.
- **R16. Inject clocks, sleeps and ID generators, and assert literal values.**
  No `time.sleep` or wall-clock waits. The one exception is an OS event with no
  virtual equivalent (a process group reaped after `SIGKILL`): wait for it with
  `wait_until` in `tests/harness/poll.py`, which owns the only sleep. *Why:*
  flaky tests get ignored, and then they hide real failures.

### Docs

- **R17. Document only shipped behaviour, and fix every doc or docstring
  sentence your change makes false**, in the same change. *Why:* agents trust
  docs, so a false sentence spreads.

## Exemplars

Model new work on these.

| Kind | Exemplar |
|---|---|
| Read command | `src/dovo/cli/session/commands/list.py`, wired in `src/dovo/cli/session/app.py` |
| Mutating command | `src/dovo/cli/worktree/commands/worktree_create.py`; the lock is taken in `src/dovo/core/worktree/worktree.py` |
| Formatter and view model | `src/dovo/cli/ui/formatters/session/session_list.py`, `session_views.py` |
| Core package | `src/dovo/core/sessions/` (`models.py`, `sessions.py`, `services/`) |
| Repository | `src/dovo/core/db/repositories/worktrees.py` on `base.py` |
| Migration | `src/dovo/core/db/alembic/versions/0004_session_discard.py`, tested in `tests/core/db/test_migrations.py` |
| Command tests | `tests/cli/worktree/test_worktree_show.py` |
| Formatter tests | `tests/cli/ui/formatters/session/test_session_list.py`, using `tests/harness/formatter.py` |
| Service tests | `tests/core/sessions/services/test_read_logs.py` |
| Test double | `FakeAgentProvider` in `tests/harness/fakes.py` |

## Essential commands

```bash
uv sync --all-extras            # install dependencies
uv run dovo run tests --path tests/core/   # scoped tests while iterating (also --fast-fail, --coverage)
uv run dovo run quality         # all quality gates against the working tree
uv run ruff format .            # format (the gate only checks it)
uv run ruff check .             # lint
uv run basedpyright src tests   # typecheck (errors must be 0)
```

## Quality gate

`uv run dovo run quality` must pass before every commit. It runs, in order,
and stops at the first failure: `ruff format --check`, `ruff check`,
`basedpyright src tests --level error`, `mkdocs build --strict`, `complexipy`
on the files changed against `origin/main` (no touched function over 10), and
the full test suite with coverage (at least 80%). CI runs the same blueprint.
Fix a failure in the code, never with a suppression or a lowered threshold.

## Docs

| Doc | When to use |
|-----|-------------|
| [docs/agents/code-conventions.md](docs/agents/code-conventions.md) | Python style, model placement, `Any` and type-ignore rules |
| [docs/agents/testing.md](docs/agents/testing.md) | Test tiers, harness helpers, naming, parameterization |
| [docs/agents/planning.md](docs/agents/planning.md) | Planning and phasing an issue (`/dovo-plan`) |
| [docs/agents/documentation.md](docs/agents/documentation.md) | Which user-facing docs a change must update |
| [docs/cli/](docs/cli/) | Per-command user reference |

End a unit of work with a summary, the quality gate result and a semantic commit
message (`type(scope): imperative summary`, on a `<username>/<short-description>` branch).
