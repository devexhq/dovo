"""Session fixtures, subprocess execution runners, and filesystem isolation for E2E tests."""

from __future__ import annotations

import fcntl
import os
import pty
import select
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest

E2E_ROOT = Path("/tmp/dovo-e2e")
DOVO_HOME = Path("/tmp/dovo-home")
E2E_FIXTURES = Path("/tmp/dovo-e2e-fixtures")
E2E_CACHES = Path("/tmp/dovo-e2e-caches")
LOCK_FILE = Path("/tmp/dovo-e2e.lock")
FIXTURES_LOCK_PATH = Path(__file__).parent / "fixtures.lock"
LOCAL_FIXTURES_CANDIDATE = Path("/home/luckner/workspace/devexhq/dovo-e2e-fixtures")


@dataclass(frozen=True)
class DovoRunResult:
    """Subprocess result captured from a dovo CLI invocation."""

    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float


@dataclass(frozen=True)
class DovoPtyResult:
    """Terminal transcript captured from an interactive PTY dovo CLI invocation."""

    transcript: str
    exit_code: int
    duration_seconds: float


def resolve_dovo_binary() -> str:
    """Locate the installed dovo binary on the current environment PATH or virtualenv."""
    binary_path = shutil.which("dovo")
    if binary_path is not None:
        return binary_path

    venv_binary = Path(sys.prefix) / "bin" / "dovo"
    if venv_binary.is_file() and os.access(venv_binary, os.X_OK):
        return str(venv_binary)

    pytest.fail("The 'dovo' CLI executable was not found. Install package in editable or wheel mode.")


class DovoRunner:
    """Runner executing the dovo CLI binary in a non-interactive subprocess."""

    def __init__(self, binary_path: str, default_cwd: Path, default_env: dict[str, str]) -> None:
        self._binary_path = binary_path
        self._default_cwd = default_cwd
        self._default_env = default_env

    def __call__(
        self,
        args: list[str],
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
        timeout: float = 30.0,
    ) -> DovoRunResult:
        target_cwd = Path(cwd) if cwd is not None else self._default_cwd
        execution_env = self._default_env.copy()
        if env is not None:
            execution_env.update(env)

        cmd = [self._binary_path, *args]
        start_time = time.monotonic()
        process = subprocess.run(
            cmd,
            cwd=target_cwd,
            input=input_text,
            text=True,
            capture_output=True,
            env=execution_env,
            timeout=timeout,
        )
        duration = time.monotonic() - start_time

        return DovoRunResult(
            stdout=process.stdout,
            stderr=process.stderr,
            exit_code=process.returncode,
            duration_seconds=duration,
        )


class DovoPtyRunner:
    """Runner executing the dovo CLI binary in a pseudo-terminal for interactive prompts."""

    def __init__(self, binary_path: str, default_cwd: Path, default_env: dict[str, str]) -> None:
        self._binary_path = binary_path
        self._default_cwd = default_cwd
        self._default_env = default_env

    def __call__(
        self,
        args: list[str],
        prompt_replies: list[tuple[str, str]] | None = None,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> DovoPtyResult:
        target_cwd = Path(cwd) if cwd is not None else self._default_cwd
        execution_env = self._default_env.copy()
        if env is not None:
            execution_env.update(env)

        cmd = [self._binary_path, *args]
        start_time = time.monotonic()

        master_fd, slave_fd = pty.openpty()
        try:
            process = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=target_cwd,
                env=execution_env,
                close_fds=True,
            )
        finally:
            os.close(slave_fd)

        try:
            transcript = self._communicate_pty(
                process=process,
                master_fd=master_fd,
                prompt_replies=list(prompt_replies or []),
                timeout=timeout,
            )
            exit_code = process.wait(timeout=5.0)
            return DovoPtyResult(
                transcript=transcript,
                exit_code=exit_code,
                duration_seconds=time.monotonic() - start_time,
            )
        finally:
            os.close(master_fd)

    def _read_fd_safe(self, file_descriptor: int) -> bytes:
        try:
            return os.read(file_descriptor, 4096)
        except OSError:
            return b""

    def _drain_fd(self, file_descriptor: int) -> list[str]:
        drain_chunks: list[str] = []
        while True:
            ready_fds, _, _ = select.select([file_descriptor], [], [], 0.02)
            if file_descriptor not in ready_fds:
                break
            data = self._read_fd_safe(file_descriptor)
            if not data:
                break
            drain_chunks.append(data.decode("utf-8", errors="replace"))
        return drain_chunks

    def _check_and_send_reply(
        self,
        master_fd: int,
        accumulated: str,
        prompt_replies: list[tuple[str, str]],
    ) -> None:
        if not prompt_replies:
            return
        expected_prompt, reply = prompt_replies[0]
        if expected_prompt in accumulated:
            os.write(master_fd, reply.encode("utf-8"))
            prompt_replies.pop(0)

    def _handle_pty_read(
        self,
        master_fd: int,
        accumulated: str,
        prompt_replies: list[tuple[str, str]],
    ) -> tuple[str, str]:
        ready_fds, _, _ = select.select([master_fd], [], [], 0.05)
        if master_fd not in ready_fds:
            return "", accumulated

        data = self._read_fd_safe(master_fd)
        if not data:
            return "", accumulated

        text = data.decode("utf-8", errors="replace")
        new_accumulated = accumulated + text
        self._check_and_send_reply(master_fd, new_accumulated, prompt_replies)
        return text, new_accumulated

    def _verify_prompts_satisfied(self, prompt_replies: list[tuple[str, str]], accumulated: str) -> None:
        if prompt_replies:
            expected_prompt = prompt_replies[0][0]
            raise AssertionError(f"Expected prompt '{expected_prompt}' was not encountered. Output:\n{accumulated}")

    def _communicate_pty(
        self,
        process: subprocess.Popen[bytes],
        master_fd: int,
        prompt_replies: list[tuple[str, str]],
        timeout: float,
    ) -> str:
        deadline = time.monotonic() + timeout
        chunks: list[str] = []
        accumulated = ""

        while process.poll() is None:
            if time.monotonic() > deadline:
                process.kill()
                process.wait()
                raise TimeoutError(f"PTY execution timed out after {timeout}s.\nTranscript:\n{accumulated}")

            text, accumulated = self._handle_pty_read(master_fd, accumulated, prompt_replies)
            if text:
                chunks.append(text)

        chunks.extend(self._drain_fd(master_fd))
        self._verify_prompts_satisfied(prompt_replies, accumulated)
        return "".join(chunks)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register CLI flags for E2E harness."""
    parser.addoption(
        "--keep-e2e-artifacts",
        action="store_true",
        default=False,
        help="Preserve /tmp/dovo-home and /tmp/dovo-e2e-caches across test sessions.",
    )


@pytest.fixture(scope="session", autouse=True)
def session_lock(request: pytest.FixtureRequest) -> Generator[None]:
    """Acquire exclusive session lock and manage lifecycle of isolated E2E state directories."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        os.close(file_descriptor)
        raise RuntimeError("Another E2E session is currently running (/tmp/dovo-e2e.lock is locked).") from exc

    shutil.rmtree(DOVO_HOME, ignore_errors=True)
    DOVO_HOME.mkdir(parents=True, exist_ok=True)

    E2E_CACHES.mkdir(parents=True, exist_ok=True)
    (E2E_CACHES / "uv").mkdir(parents=True, exist_ok=True)
    (E2E_CACHES / "npm").mkdir(parents=True, exist_ok=True)

    try:
        yield
    finally:
        keep_artifacts = bool(request.config.getoption("--keep-e2e-artifacts"))
        if not keep_artifacts:
            shutil.rmtree(DOVO_HOME, ignore_errors=True)
            shutil.rmtree(E2E_CACHES, ignore_errors=True)
            shutil.rmtree(E2E_FIXTURES, ignore_errors=True)
            shutil.rmtree(E2E_ROOT, ignore_errors=True)

        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(file_descriptor)
        LOCK_FILE.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def clean_e2e_env() -> Generator[Path]:
    """Recreate per-test scratch directory before every test."""
    shutil.rmtree(E2E_ROOT, ignore_errors=True)
    E2E_ROOT.mkdir(parents=True, exist_ok=True)
    yield E2E_ROOT


@pytest.fixture(scope="session")
def default_subprocess_env() -> dict[str, str]:
    """Produce the standard isolated subprocess environment for dovo CLI invocations."""
    env = os.environ.copy()
    env["DOVO_HOME"] = str(DOVO_HOME)
    env["NO_COLOR"] = "1"
    env["COLUMNS"] = "160"
    env["PYTHONIOENCODING"] = "utf-8"
    env["UV_CACHE_DIR"] = str(E2E_CACHES / "uv")
    env["NPM_CONFIG_CACHE"] = str(E2E_CACHES / "npm")
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


@pytest.fixture(scope="session")
def dovo_binary_path() -> str:
    """Resolve the dovo CLI binary path once per test session."""
    return resolve_dovo_binary()


@pytest.fixture
def run_dovo(dovo_binary_path: str, default_subprocess_env: dict[str, str]) -> DovoRunner:
    """Provide a callable runner to execute non-interactive dovo CLI commands."""
    return DovoRunner(
        binary_path=dovo_binary_path,
        default_cwd=E2E_ROOT,
        default_env=default_subprocess_env,
    )


@pytest.fixture
def run_dovo_pty(dovo_binary_path: str, default_subprocess_env: dict[str, str]) -> DovoPtyRunner:
    """Provide a callable runner to execute interactive dovo CLI commands via pseudo-terminal."""
    return DovoPtyRunner(
        binary_path=dovo_binary_path,
        default_cwd=E2E_ROOT,
        default_env=default_subprocess_env,
    )


def load_fixtures_lock(lock_path: Path) -> tuple[str, str]:
    """Read repository URL and commit SHA from fixtures.lock."""
    if not lock_path.is_file():
        pytest.fail(f"Fixtures lockfile missing at {lock_path}")

    with open(lock_path, "rb") as f:
        data = tomllib.load(f)

    repo_url = str(data.get("repository", ""))
    commit_sha = str(data.get("commit", ""))
    if not repo_url or not commit_sha:
        pytest.fail(f"Invalid fixtures lockfile at {lock_path}: must declare repository and commit")

    return repo_url, commit_sha


def clone_and_checkout_fixtures(target_dir: Path, repo_url: str, commit_sha: str) -> None:
    """Clone the fixture repository once and pin it to the locked commit SHA."""
    source_url = os.environ.get("DOVO_E2E_FIXTURES_DIR")
    if not source_url and LOCAL_FIXTURES_CANDIDATE.is_dir():
        source_url = str(LOCAL_FIXTURES_CANDIDATE)
    if not source_url:
        source_url = repo_url

    shutil.rmtree(target_dir, ignore_errors=True)
    subprocess.run(["git", "clone", source_url, str(target_dir)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(target_dir), "checkout", commit_sha], check=True, capture_output=True)


@pytest.fixture(scope="session")
def fixture_cache() -> Path:
    """Provide the cloned and pinned external fixture repository cache directory."""
    if not (E2E_FIXTURES / ".git").is_dir():
        repo_url, commit_sha = load_fixtures_lock(FIXTURES_LOCK_PATH)
        clone_and_checkout_fixtures(E2E_FIXTURES, repo_url, commit_sha)
    return E2E_FIXTURES


def init_git_baseline(target_dir: Path) -> None:
    """Initialize a git repository with standard test committer identity and commit baseline files."""
    subprocess.run(["git", "init", "-b", "main"], cwd=target_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Dovo E2E Test"], cwd=target_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "dovo-e2e@devexhq.com"], cwd=target_dir, check=True, capture_output=True
    )
    subprocess.run(["git", "config", "commit.gpgSign", "false"], cwd=target_dir, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=target_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: initial fixture baseline"], cwd=target_dir, check=True, capture_output=True
    )


def execute_fixture_bootstrap(target_dir: Path) -> bool:
    """Execute optional bootstrap commands declared in fixture.toml. Returns git_baseline setting."""
    manifest_path = target_dir / "fixture.toml"
    if not manifest_path.is_file():
        return True

    with open(manifest_path, "rb") as f:
        data = tomllib.load(f)

    raw_bootstrap = data.get("bootstrap", [])
    commands: list[list[str]] = []
    if raw_bootstrap and isinstance(raw_bootstrap[0], list):
        commands = raw_bootstrap
    elif raw_bootstrap and isinstance(raw_bootstrap[0], str):
        commands = [raw_bootstrap]

    for cmd in commands:
        subprocess.run(cmd, cwd=target_dir, check=True, capture_output=True)

    return bool(data.get("git_baseline", True))


def materialize_fixture(
    fixture_cache: Path,
    fixture_id: str,
    target_dir: Path,
) -> Path:
    """Copy a fixture from the cache, run bootstrap commands, and initialize git baseline."""
    source_dir = fixture_cache / "fixtures" / fixture_id
    if not source_dir.is_dir():
        source_dir = fixture_cache / fixture_id
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Fixture '{fixture_id}' not found in {fixture_cache}")

    shutil.copytree(source_dir, target_dir)
    should_init_git = execute_fixture_bootstrap(target_dir)
    if should_init_git:
        init_git_baseline(target_dir)

    return target_dir


@pytest.fixture
def git_project(
    fixture_cache: Path,
    clean_e2e_env: Path,
    request: pytest.FixtureRequest,
) -> Path:
    """Materialize a clean Git repository from a fixture without dovo initialization."""
    fixture_id = getattr(request, "param", "minimal-git-project")
    safe_test_name = request.node.name.replace("[", "_").replace("]", "_")
    target_dir = clean_e2e_env / f"proj_{fixture_id}_{safe_test_name}"
    return materialize_fixture(fixture_cache, fixture_id, target_dir)


@pytest.fixture
def initialized_project(git_project: Path, run_dovo: DovoRunner) -> Path:
    """Materialize a Git repository and run dovo init, returning the initialized project path."""
    result = run_dovo(["init"], cwd=git_project)
    if result.exit_code != 0:
        raise RuntimeError(f"dovo init failed during setup: {result.stderr or result.stdout}")
    subprocess.run(["git", "add", "-A"], cwd=git_project, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "chore: initialize dovo"], cwd=git_project, check=True, capture_output=True)
    return git_project
