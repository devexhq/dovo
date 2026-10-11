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

## Continuous Integration (CI)

**Relevant sources:** `.github/workflows/ci.yml`

Four CI jobs run on pushes to `main` and on pull requests:
- **test**: `uv sync --all-extras` and `dovo run quality --no-tty` (the quality blueprint, including the strict docs build and coverage with `fail_under = 80` in `pyproject.toml`).
- **prek**: `prek` run against the PR base ref (`origin/${GITHUB_BASE_REF}`) on pull requests, or `--all-files` on `main`.
- **rules**: `compile_rules.py --check` verifying generated agent rules and checklists match `rules_spec.yaml`.
- **ci**: Gate job requiring `test`, `prek`, and `rules` to succeed.

---

## Database Migration Hygiene

**Relevant sources:** `src/dovo/core/db/`, `src/dovo/core/db/alembic/`

- Every new table or column must have real producer and consumer call sites in `src/` in the same PR.
- One-time data backfills must be versioned Alembic revisions (`op.execute()`), not ad-hoc raw SQL in models.
- Database repositories and domain entrypoints must be constructed once per command invocation rather than inside loops.

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
