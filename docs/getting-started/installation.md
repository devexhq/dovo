# Installation

Dovo CLI (`dovo`) can be installed using Python package managers.

## Recommended Installation

### Python / Pip

Install the package directly via `pip`:

```bash
pip install devexhq-dovo
```

Using `pipx` (isolated application environments):

```bash
pipx install devexhq-dovo
```

Using `uv`:

```bash
uv tool install devexhq-dovo
```

### Local Development / Source Installation

To install in editable mode with development dependencies:

```bash
uv sync --all-extras
# Or using uv pip:
# uv pip install -e ".[dev]"
```

To install with documentation dependencies:

```bash
uv sync --extra docs
# Or: uv pip install -e ".[docs]"
```

## Verification

Verify that `dovo` is installed and check your version:

```bash
dovo --version
```

Output:

```text
dovo version <installed-version>
```
