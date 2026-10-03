# Recipe: CI/CD Automation & GitHub Actions

Dovo blueprints can be executed inside Continuous Integration (CI) pipelines to standardize local developer runs and remote CI validation.

---

## Key CLI Flags for CI/CD

When executing Dovo in automated environments:
* `--no-sandbox`: Disables Git worktree branch creation and executes steps directly in the runner workspace.
* `--no-tty`: Ensures `prompt_user` failure directives degrade safely to `abort` rather than hanging on standard input.

```bash
dovo run build-and-test --no-sandbox --no-tty
```

---

## Example: GitHub Actions Workflow

Create `.github/workflows/verify-blueprints.yml`:

```yaml
name: Verify Dovo Blueprints

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  validate:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout Code
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Install Dependencies
        run: |
          pip install uv
          uv sync --all-extras

      - name: Initialize Dovo Workspace
        run: dovo init

      - name: Validate Dovo Config
        run: dovo config validate

      - name: Execute Full Verification Blueprint
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: |
          dovo run lint-and-test --no-sandbox --no-tty
```

---

## Automated Failure Diagnostics

In CI, when a step assertion fails, Dovo prints formatted diagnostics and non-zero exit codes that integrate with CI log viewers:

```text
Step 'run-tests' failed assertions:
  [FAIL] Expected exit_code 0, got 1
  [FAIL] Output did not contain '0 errors'
```
