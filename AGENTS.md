# AGENTS.md

`Dovo` (`dovo`) is a Typer-based CLI providing isolated Git worktree developer
workflows and AI agent workspaces, backed by a local `.dovo/` state directory.

The codebase is the main specification. Read the code around your change and
the [exemplars](#exemplars) before writing anything, and make new code look like
them. The rules below cover what the code cannot show and what is expensive to
get wrong. Mechanical checks live in `tests/lint/`, ruff and ty, so
they are not repeated here.

## How work flows

1. **Ready** the issue (`/dovo-issue ready <n>`, or `dovo run ready-issue -i <n>`
   to apply the approved edits and label it): it is re-grounded against main
   and re-checked against `/dovo-issue` and the rules below, and labelled
   `dovo:ready` with the sha it was checked at. `feature-dev` pauses on an issue without the label.
2. **Plan** (`/dovo-plan`) anything that adds surface: a command, flag, result
   model, status enum, service, formatter, config key, schema field or table.
   The plan sizes the work and splits it into phases. Skip it for a single-file
   change that adds no surface. If a plan was created, review it
   (`/dovo-plan-review`) and challenge its design (`/dovo-plan-challenge`),
   each in a fresh session, then walk the approver through it
   (`/dovo-plan-walkthrough`) before a human approves it.
3. **Phase the plan.** Each phase is reviewable in one sitting (roughly 400
   changed lines or less), passes the quality gate on its own and ships
   something. When a change replaces an internal, phase it as add, switch
   callers, delete, with the deletion phase planned up front (see R10).
4. **Implement** the plan (`/dovo-code`), which works through every phase in
   order and lands them in one PR, then **review** it (`/dovo-review`) in a
   fresh session, then a human reviews and merges the PR.

## Rules

The coding agent follows these. The review agent reports each violation by its
ID, with the `path:line` that breaks it.

### Architecture

- **R1. Imports flow one way: common → core → engine → cli.** Within `core/`,
  packages follow `core/{project,db,git,inputs} → core/{agents,catalog,sessions} → core/config → core/{worktree,status,artifacts} → core/interaction → core/{bootstrap,diagnostics}`.
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
- **R4. Business failures reach the caller as a result, not an exception.**
  A public entrypoint (a method of the entrypoint class in
  `core/<pkg>/<pkg>.py`, or an `engine/services/` runner, called from a layer
  above) returns a `*Result` subclassing `BaseResult`, and the caller branches
  on its `error_code`, or on `ok`, which is true exactly when `error_code` is
  null ([results.md](docs/agents/architecture/results.md)). Code behind an entrypoint returns plain values and signals a
  business failure by raising its package's one `DomainError` subclass, which
  carries an `ErrorCode`, a message and remediations; the entrypoint catches it
  once, with `catch_failure`, and copies the failure into its result.
  `catch_failure` also records any unexpected exception in the diagnostic log,
  so code behind an entrypoint does not catch broad `Exception` to report it.
  Only a site that recovers or collects per-item failures and continues
  catches there, and it records what it caught. Helpers get no `*Result` or `*Status`
  of their own (one may build its entrypoint's result), and no table or `match` maps one status or error code onto
  another. `error_code` is a failure's only classification: a status value
  never names a failure cause, so a new status enum or value holds only
  outcomes `error_code` cannot express. Each package has exactly one
  `DomainError` class: the code is passed at the raise, with no subclass per
  code, and code branches on `error_code`, never on the class
  ([exceptions.md](docs/agents/architecture/exceptions.md)). Raise
  anything else only for programmer errors and broken invariants. *Why:* exit
  codes, the JSON envelope and every caller branch on the entrypoint's result,
  and a failure that carries the `ErrorCode` it was raised with needs no
  re-encoding at each hop.
- **R5. New work follows its neighbours and the exemplars.** Match the layout,
  naming and idiom of the package you are in. When two existing patterns
  disagree, follow the exemplar and say so in the PR. *Why:* agents copy what
  they see, so drift compounds.

### State and safety

- **R6. Writes under `.dovo/` go through `Filesystem.atomic_write_*`, and
  mutations that can run in more than one process hold `WorkspaceLock`.** Every
  `LockTimeoutError` propagates to the top-level handler; no package turns it
  into an error code, a warning or a status
  ([exceptions.md](docs/agents/architecture/exceptions.md)). *Why:* concurrent `dovo` runs share one
  workspace, and a torn or racing write corrupts it.
- **R7. Secrets come only from environment variables, read at call time.** Never
  write them to `config.json`, `data.db`, logs, artifacts or prompt arguments.
  *Why:* those files are shared, committed or uploaded.
- **R8. Never edit, rename or delete a released migration; every new one is
  reversible.** A revision in a `v*` tag is fixed forward with a new revision.
  Each revision file stays dialect-neutral (`op.batch_alter_table`, no
  SQLite-only `PRAGMA` in revision files) and implements `upgrade()` and
  `downgrade()` with tests for both in `tests/core/db/test_migrations.py`.
  *Why:* users' databases are stamped with released revisions.

### Compatibility and change shape

- **R9. Dovo is greenfield: its public surface is not yet a contract.** CLI
  flags and arguments, the meaning of `config.json` and blueprint keys, and the
  JSON event envelope have no external consumers until the first public
  release. When one is wrong, fix it now: change it outright, update every
  caller, test and doc in the same change (R10, R17), and name the change in
  the PR. Never keep a wrong shape behind an alias, fallback or compatibility
  shim. R8 still applies to migrations. This rule becomes "never break the
  public surface" at the first public release (#914). *Why:* a contract
  settled before release is one users never migrate off, and a shim added now
  becomes permanent.
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
- **R18. User docs describe only what users type, see and configure.** Pages
  under `docs/` outside `docs/agents/`, `README.md` and `mkdocs.yml` name no
  classes, modules, source or test paths, internal libraries or the tests that
  check them, and follow the user-docs rules in
  [docs/agents/documentation.md](docs/agents/documentation.md#user-documentation).
  *Why:* users read these pages, and every internal named there becomes a
  rewrite when the code moves.

## Exemplars

Model new work on these.

| Kind | Exemplar |
|---|---|
| Read command | `src/dovo/cli/session/commands/list.py`, wired in `src/dovo/cli/session/app.py` |
| Mutating command | `src/dovo/cli/worktree/commands/worktree_create.py`; the lock is taken in `src/dovo/core/worktree/worktree.py` |
| Formatter and view model | `src/dovo/cli/ui/formatters/session/session_list.py`, `session_views.py` |
| Core package | `src/dovo/core/sessions/` (`models.py`, `sessions.py`, `services/`) |
| Domain error | `ProjectIdentityError` in `src/dovo/core/project/exceptions.py`, raised by `src/dovo/core/project/services/identity.py` and caught once with `catch_failure` in `src/dovo/core/bootstrap/services/initialize.py` |
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
uv run ty check src tests e2e   # typecheck (must report no diagnostics)
```

## Quality gate

`uv run dovo run quality` must pass before every commit. It runs, in order,
and stops at the first failure: `ruff format --check`, `ruff check`,
`ty check src tests e2e`, `mkdocs build --strict`, `complexipy`
on the files changed against `origin/main` (no touched function over 10), and
the full test suite with coverage (at least 80%). CI runs the same blueprint.
Fix a failure in the code, never with a suppression or a lowered threshold.

## Docs

| Doc | When to use |
|-----|-------------|
| [docs/agents/code-conventions.md](docs/agents/code-conventions.md) | Python style, model placement, `Any` and type-ignore rules |
| [docs/agents/architecture/](docs/agents/architecture/README.md) | Settled decisions: exceptions, remediations, diagnostics and results |
| [docs/agents/testing.md](docs/agents/testing.md) | Test tiers, harness helpers, naming, parameterization |
| [docs/agents/planning.md](docs/agents/planning.md) | Planning and phasing an issue (`/dovo-plan`) |
| [docs/agents/documentation.md](docs/agents/documentation.md) | Which user-facing docs a change must update |
| [docs/cli/](docs/cli/) | Per-command user reference |

End a unit of work with a summary, the quality gate result and a semantic commit
message (`type(scope): imperative summary`, on a `<username>/<short-description>` branch).
