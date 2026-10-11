# Installation

Install Dovo CLI (`dovo`) with a Python package manager.

## Install

With `pip`:

```bash
pip install devexhq-dovo
```

With `pipx`:

```bash
pipx install devexhq-dovo
```

With `uv`:

```bash
uv tool install devexhq-dovo
```

## Install from Source

From a clone of the repository, install in editable mode with development dependencies:

```bash
uv sync --all-extras
# Or using uv pip:
# uv pip install -e ".[dev]"
```

## Verification

Check that `dovo` is installed:

```bash
dovo --version
```

Output:

```text
dovo version <installed-version>
```
