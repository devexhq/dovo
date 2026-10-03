# Recipe: Test-Driven Development (TDD) Loop

This recipe demonstrates a generic blueprint loop that asks an agent step to fix failing tests and reruns them until they pass. The agent step runs in the worktree, so the loop needs a Git worktree.

---

## The Blueprint

Create `.dovo/catalog/blueprints/tdd-cycle.yml`:

```yaml
name: tdd-cycle
description: Iterative test-driven development cycle
summary: Agent-step fixes while rerunning tests
version: 1
use_worktree: true

inputs:
  test_file:
    type: string
    description: Target test file containing failing tests
    required: true
    aliases: ["-t", "--test"]

  max_attempts:
    type: integer
    description: Maximum iterations
    default: 5
    aliases: ["-m", "--max-attempts"]

steps:
  - id: verify-initial-tests
    name: Run initial tests (expect failure)
    run: pytest ${{ inputs.test_file }}
    on_failure: continue

  - id: tdd-loop
    name: Test observation loop
    type: loop
    max_iterations: 5
    until:
      - steps.run-test-suite.exit_code == 0
    on_max_iterations: prompt_user
    do:
      - id: ai-code-patcher
        name: Fix failing tests
        type: agent
        prompt: "Make the tests in ${{ inputs.test_file }} pass by changing the implementation, not the tests."
        timeout_seconds: 180

      - id: run-test-suite
        name: Run test suite
        run: pytest ${{ inputs.test_file }}
        assert:
          exit_code: 0
```

---

## Running the Recipe

1. Write a failing test in `tests/test_calculator.py`.
2. Execute the TDD blueprint:

```bash
dovo run tdd-cycle --test tests/test_calculator.py
```

### Execution Flow:
1. Dovo spins up an isolated worktree.
2. The agent step sends its prompt to the configured provider and applies any change inside the worktree.
3. The test suite runs after each attempt.
4. As soon as all assertions pass (`steps.run-test-suite.exit_code == 0`), the loop terminates with success.
