# Testing

Testing conventions, tiers, and harness patterns for Dovo CLI. Companion to rules R11–R16 in [AGENTS.md](../../AGENTS.md).

## 1:1 Source Parity Layout

Test structure mirrors `src/dovo/` 1:1 under `tests/`:
- `src/dovo/common/<m>.py` → `tests/common/test_<m>.py`
- `src/dovo/core/<domain>/<m>.py` → `tests/core/<domain>/test_<m>.py`
- `src/dovo/engine/<m>.py` → `tests/engine/test_<m>.py` (`executors/` and `services/` mirror 1:1)
- `src/dovo/cli/ui/formatters/<domain>/<name>.py` → `tests/cli/ui/formatters/<domain>/test_<name>.py`
- `src/dovo/cli/<command>/commands/<action>.py` → `tests/cli/<command>/test_<command>_<action>.py`

Every test directory has an `__init__.py`. `tests/core/**` never imports `dovo.cli.*`.

## Naming Conventions

- **Test classes:** `*Tests` (e.g. `ConfigLoaderTests`), or standalone `test_*` functions when a class buys no fixture reuse.
- **Test methods:** `test_<condition>_<outcome>`. The node id alone must convey the behavior.
- **Banned vague names:** `test_ok`, `test_success`, `test_present`, `test_missing`, `test_blank`, `test_basic`, `test_default`, `test_works`, `test_timeout`, `test_no_op`, `test_help`, or `should_*`.
- **Docstrings:** Optional; write a single imperative sentence only when the identifier cannot carry an exact literal or ordering contract.

## The Five Execution Tiers

### Tier 1 — Domain Behavior (`tests/core/`, `tests/engine/`, `tests/common/`)
Assert on returned `*Result` objects and real side effects (files, git refs, DB rows). Use real `tmp_path`, SQLite, and `GitWorkspaceHarness`; mock only genuine process/network boundaries.

### Tier 2 — Presentation Contracts (`tests/cli/ui/formatters/`)
Every formatter gets three tests built on `tests.harness.formatter.FormatterCase` (an identity formatter with no derived view model omits the Transform test):
1. **Transform:** `transform(model) == ExpectedView(...)`.
2. **JSON wire:** `to_json_serializable(model)` pinned against an exact literal dict (`assert_exact_json`).
3. **Rich render:** `render_rich(renderable, width=160)` asserting only values sourced from `case.view` (never labels, borders, widths, or full sentences).

### Tier 3 — CLI Wiring (`tests/cli/`)
Every command action gets a `*CliIntegrationTests` suite (`runner.invoke(app, [...])`) covering happy path exit 0, option/argument binding for every declared flag, failure path non-zero exit, and `--format json`. Add a direct-handler `*RootTests` suite only when the handler owns branch/coercion logic outside the domain layer.

### Tier 4 — Invariants (`tests/lint/`)
Static AST and architectural boundary checks: layer direction, output routing, atomic file writes, model strictness, type-ignore reasons, and `README.md` command parity.

### Tier 5 — End-to-End Scenarios (`e2e/`)
Each scenario runs the installed `dovo` binary as a subprocess (`run_dovo`, `start_dovo` or `run_dovo_pty` from `e2e/conftest.py`) against a materialized fixture repository and asserts only what a person gets: exit code, terminal text, JSON output, files on disk and git state. Never assert internals, or a contract a lower tier already pins; one scenario per user-visible path is enough. An outcome that changes what a person types or sees from the binary (a command, a flag, an exit code, a terminal text or a JSON field) gets its scenario added or updated in the same pull request (R13); an outcome a person cannot observe from the binary gets none. The suite runs serially (`uv run pytest e2e -n 0`), outside `dovo run quality`, and gates CI on its own job.

## Harness, Fixtures, and Parameterization

- **Shared fixtures (`tests/conftest.py` & `tests/cli/conftest.py`):** `isolated_workspace(tmp_path)`, `git_repo(tmp_path)`, `cli_runner()` (`NO_COLOR=1`, `COLUMNS=160`), `cli_workspace`, `workspace_config`.
- **Environment isolation (`tests/conftest.py`):** Every test starts with no inherited `DOVO_*` variable and `DOVO_HOME` at a per-test temp directory; set any variable a test needs with `monkeypatch`. Without `--basetemp`, a temp root inside a `.dovo` or git ancestor (a `dovo run` step's `TMPDIR`) is dropped for the system default, so the suite behaves the same in an agent step as in a plain shell.
- **Builders & Doubles (`tests/harness/`):** Use fluent builders (`tests/harness/builders/`) and typed fakes (`tests/harness/fakes.py`).
- **Determinism (R16):** Inject clocks and ID generators; use `wait_until` in `tests/harness/poll.py` only for OS process-group reaping.
- **Permission failures:** Simulate a read-only directory or an unreadable file with `deny_entry_creation` or `deny_read` in `tests/harness/permissions.py`, never with `chmod`: root ignores permission bits, so a `chmod` test fails or must be skipped in containers that run as root.
- **Parameterization:** Use `@pytest.mark.parametrize` with `pytest.param(..., id="descriptive_case_id")` for input/error matrices. Never loop with `for` inside a test or branch with `if/else` on divergent assertions.
