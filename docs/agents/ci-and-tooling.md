# CI and Tooling

Guidelines and requirements for local quality gates and continuous integration.

---

## Quality Gate Blueprints

**Relevant sources:** `.dovo/catalog/blueprints/quality.yml`, `.dovo/catalog/blueprints/tests.yml`

Both blueprints run against the working tree (`use_worktree: false`) and need `uv sync --all-extras`.

- `dovo run quality` is the full gate that CI also runs. It runs `ruff format --check`, `ruff check`,
  `basedpyright src tests --level error`, `mkdocs build --strict`, `complexipy` on the Python files changed
  against `--base` (default `origin/main`), then the whole test suite with coverage (`fail_under`). It stops at
  the first failing step and never rewrites files: apply `ruff format .` or `ruff check --fix .` first.
- `dovo run tests` is the scoped run for quick feedback, never a substitute for `quality`:
  ```bash
  uv run dovo run tests                                 # whole suite
  uv run dovo run tests --path tests/core/              # file, directory or node id
  uv run dovo run tests --path tests/core/ --fast-fail  # stop at the first failure
  uv run dovo run tests --coverage                      # print a coverage report (no floor enforced)
  ```
- Pytest runs on 4 xdist workers (`-n 4` in `pyproject.toml`); override with `-n <count>` (for example `-n 0` to run
  in-process). CI sets `PYTEST_ADDOPTS="-n auto"` in `.github/workflows/ci.yml` to use one worker per core.
- Both strip Dovo's per-step `DOVO_*` variables before invoking pytest, because tests assert they are unset.

---

## Lint and Format

**Relevant sources:** `pyproject.toml` (`[tool.ruff]`)

- Config lives in `pyproject.toml` (Python 3.13, line length 120; rule sets `E, W, F, I, B, C4, UP, RUF, FA, D` with Google docstrings).
- Commands:
  ```bash
  ruff check .              # lint check
  ruff check --fix .        # apply safe fixes
  ruff format --check .     # format check
  ruff format .             # apply formatting
  ```

---

## Type Checking

**Relevant sources:** `pyproject.toml` (`[tool.basedpyright]`)

- Config lives in `pyproject.toml` (`typeCheckingMode = "recommended"`, package `src/dovo/`).
- Commands:
  ```bash
  basedpyright src                # typecheck
  basedpyright src --level error   # error gate (must be 0 errors)
  ```
- **Suppression comments:** Must use `# pyright: ignore[reportRuleName]` with a
  reason. Bare `# type: ignore` is not honored. Which rules may be suppressed
  is in [code-conventions.md](code-conventions.md#type-checker-suppressions).
  `tests/lint/test_pyright_ignores.py` fails any ignore without a trailing reason.
- **Explicit `Any` is an error** (`reportExplicitAny`). The sites that predate the setting are
  recorded in `.basedpyright/baseline.json`, which basedpyright reads automatically, so only new
  ones fail. Never add to the baseline. After removing an `Any`, run
  `uv run python scripts/update_any_baseline.py` to prune its entry; the script refuses to grow
  the baseline.

---

## Complexity Gate

**Relevant sources:** `pyproject.toml` (`[tool.complexipy]`), `.dovo/catalog/blueprints/quality.yml`

- Gated via `complexipy` with threshold **max cognitive complexity <= 10**.
- Commands:
  ```bash
  uv run dovo run quality                                                         # full gate, includes changed files
  uv run complexipy src/dovo --max-complexity-allowed 10                          # whole tree
  uv run complexipy src/dovo/cli/run/app.py --max-complexity-allowed 10 --plain --failed   # scoped, failures only
  ```
- `dovo run quality` checks the files changed against `origin/main`, so touched files are gated before committing.

---

## Commit Message Hook

**Relevant sources:** `prek.toml`, `scripts/check_commit_msg.py`

`prek install` installs a `commit-msg` hook alongside `pre-commit`. It rejects a title without a
`type(scope): ` or `type: ` prefix (see [git-and-pr-conventions.md](git-and-pr-conventions.md)).
Merge, revert, `fixup!` and `squash!` titles pass.

---

## Continuous Integration (CI)

**Relevant sources:** `.github/workflows/ci.yml`

Three CI jobs run on pushes to `main` and on pull requests:
- **test**: `uv sync --all-extras` and `dovo run quality --no-tty` (the quality blueprint, including the strict docs build and coverage with `fail_under = 80` in `pyproject.toml`).
- **prek**: `prek` run against the PR base ref (`origin/${GITHUB_BASE_REF}`) on pull requests, or `--all-files` on `main`.
- **ci**: Gate job requiring `test` and `prek` to succeed.

---

## Database Migration Hygiene

**Relevant sources:** `src/dovo/core/db/`, `src/dovo/core/db/alembic/`

- Every new table or column must have real producer and consumer call sites in `src/` in the same PR.
- A `SQLModel` `Field` that declares `unique=True` must not also declare `index=True`: the unique constraint already creates the index.
- One-time data backfills must be versioned Alembic revisions (`op.execute()`), not ad-hoc raw SQL in models.
- Database repositories and domain entrypoints must be constructed once per command invocation rather than inside loops.

### Migration lifecycle

Migrations live in `src/dovo/core/db/alembic/versions/`, with revision constants in `src/dovo/core/db/migrations.py`. Users' databases persist across upgrades and carry an Alembic revision stamp, so a revision that has shipped must stay loadable.

- **Released revisions are immutable.** A revision file that exists in a release tag (`v*`) must not be edited, renamed or deleted. Fix a mistake with a new revision. The baseline is `0001_initial_schema` as shipped in `v0.6.0`.
- **Unreleased revisions can still change.** A revision that is in no tag may be edited or squashed. Once it has merged to `main`, prefer a new revision, because developer databases are already stamped with it.
- **Every revision implements `upgrade()` and `downgrade()`.** Downgrades are kept so a future move to another database (such as Postgres) can step back. Where a downgrade cannot restore dropped data, say so in the revision docstring.
- **Stay dialect-neutral.** Use Alembic operations and SQLAlchemy constructs. Use `op.batch_alter_table` for column and constraint changes. Do not use SQLite-only SQL (`PRAGMA`, `sqlite_master`) in a revision.
- **Test every revision** in `tests/core/db/test_migrations.py`: an upgrade from the previous revision that preserves existing rows, and a downgrade that restores the previous schema and keeps rows written after the upgrade.
- There is no automated check for edits to released revisions yet; reviewers enforce it.

---

## Enforcement Parity

**Relevant sources:** `tests/lint/`, `prek.toml`, `.github/workflows/`, `pyproject.toml`

- A declared gate must be able to fail. No setting disables it (a coverage floor of zero, a type
  checker in no hook and no workflow).
- A check asserts its input set is non-empty, so a glob or scan that matches nothing fails instead
  of passing vacuously.
- A check that lands green over existing violations carries an explicit burn-down allowlist. The
  allowlist only shrinks; adding an entry is a regression.

---

## Dead Code Removal

- Verify zero call sites in `src/` prior to removing obsolete modules.
- Remove related exception types, re-exports, and tests in the same change set.
- Do not write negative existence tests (e.g. `assert not hasattr(...)`) for removed symbols.
- Update documentation in the same PR to reflect removed subsystems.

---

## Versioning and Releases

**Relevant sources:** `pyproject.toml`, `.github/workflows/publish.yml`

- Dynamic package versioning via Hatchling (`hatch-vcs`) from git tags.
- Publish workflow builds and releases to PyPI when a GitHub Release is created.
