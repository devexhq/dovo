# Recipe: CI/CD Automation & GitHub Actions

Run the same blueprints locally and in CI.

---

## Key CLI Flags for CI/CD

Flags for unattended runs:
* `--no-worktree`: Runs steps directly in the checkout instead of a worktree.
* `--no-tty`: Turns `prompt_user` into `abort` so the job never waits for input.

```bash
dovo run build-and-test --no-worktree --no-tty
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

      - name: Install Dovo
        run: |
          pip install devexhq-dovo

      - name: Initialize Dovo Workspace
        run: dovo init

      - name: Validate Dovo Config
        run: dovo config validate

      - name: Execute Full Verification Blueprint
        env:
          GH_TOKEN: ${{ secrets.GH_TOKEN }}
        run: |
          dovo run lint-and-test --no-worktree --no-tty
```

---

## Failure Output

When an assertion fails, Dovo prints the failures and exits non-zero:

```text
Step 'run-tests' failed assertions:
  [FAIL] Expected exit_code 0, got 1
  [FAIL] Output did not contain '0 errors'
```
