---
name: ship
description: Deliver one actionable GitHub issue through assessment, optional investigation or planning, implementation, validation, review, and one pull request. Use for single-ticket delivery (`dovo run ship -i <issue>` or `/ship <issue>`), never for multi-issue slicing or merging.
---

# Ship one ticket (`/ship`)

Deliver one GitHub issue (and optionally an existing pull request) to one reviewed pull request using the deterministic state controller in [ship_state.py](../../../.dovo/catalog/scripts/sdlc/ship/ship_state.py) and the stage prompts under `.dovo/catalog/prompts/sdlc/ship/`. Pull request approval and merging remain with a human reviewer.

## Inputs

- `<issue>`: GitHub issue number (required)
- `--pull-request <n>`: Optional existing open pull request number to continue

## Stage workflow

### 1. Prepare and dispatch (`prepare` and `next`)

 work inside an isolated Dovo worktree (`dovo worktree create --name ship-<issue> --base-ref origin/main`) and capture the live contract:

```bash
uv run python .dovo/catalog/scripts/sdlc/ship/ship_state.py prepare --issue <issue> [--pull-request <pr>]
uv run python .dovo/catalog/scripts/sdlc/ship/ship_state.py next
```

- `prepare` writes `.agentic/ship/contract.json` and initializes or preserves `.agentic/ship/progress.json`. When continuing an existing PR, it verifies no kept worktree on the PR branch is dirty or ahead of remote `head_sha`, then aligns `HEAD` with `FETCH_HEAD`.
- `next` clears any stale `.agentic/ship/stage.json` and emits the next `action` (`assess`, `investigate`, `plan`, `implement`, `validate`, `review`, or `ask`).

### 2. Execute the dispatched stage and record (`record`)

Run the selected stage, write `.agentic/ship/stage.json` (with `stage`, `disposition`, `summary`, `body_sha`, `head_sha`, and `report_path`), and record the transition:

```bash
uv run python .dovo/catalog/scripts/sdlc/ship/ship_state.py record --action <action>
```

- **`assess`**: Follow [ticket-route.md](../../../.dovo/catalog/prompts/sdlc/ship/ticket-route.md) to inspect `.agentic/ship/contract.json` and the checkout, write `.agentic/ship/assess.md`, and select `implement`, `investigate`, `plan`, `validate`, `review`, `ask`, `no_action`, or `blocked`.
- **`investigate`**: Follow [ticket-investigate.md](../../../.dovo/catalog/prompts/sdlc/ship/ticket-investigate.md) to answer a specific causal question, write `.agentic/ship/investigate.md`, and route to `implement`, `plan`, `assess`, `ask`, or `blocked`.
- **`plan`**: Follow [ticket-plan.md](../../../.dovo/catalog/prompts/sdlc/ship/ticket-plan.md) to write a proportional single-PR plan in `.agentic/ship/plan.md` without creating sub-issues or requesting routine plan approval.
- **`implement`**: Follow [ticket-implement.md](../../../.dovo/catalog/prompts/sdlc/ship/ticket-implement.md) and [AGENTS.md](../../../AGENTS.md) to implement the ticket (or fix validation/review findings), commit the changes to the worktree branch, write `.agentic/{commit-msg,pr-title,pr-description}` and `.agentic/ship/implement.md`, and route to `validate`.
- **`validate`**: Run the repository quality gates (`ruff format --check .`, `ruff check .`, `ty check src tests e2e .dovo/catalog/scripts`, `mkdocs build --strict`, `complexipy` on changed Python files with `-mx 10`, and `pytest`), record the output in `.agentic/ship/validate.md`, and write `.agentic/ship/stage.json` with `disposition: "passed"` or `"checks_failed"`.
- **`review`**: Follow [ticket-review.md](../../../.dovo/catalog/prompts/sdlc/ship/ticket-review.md) to review the committed diff against `.agentic/ship/contract.json` and the rules in [AGENTS.md](../../../AGENTS.md), write `.agentic/review.md`, `.agentic/review.json`, and `.agentic/ship/stage.json` (`ready`, `changes_requested`, `plan`, `investigate`, `ask`, or `blocked`). The recorder caps review/fix rounds at `3` and stops repeated identical failures as `incomplete`. Before it records a `ready` review, the recorder runs `scripts/publish_review.py --check`. A review that check refuses goes back to `review` with the refusal in `last_review`, and the same refusal twice at one `HEAD` ends the run `incomplete`.
- **`ask`**: Ask a human only by exception (when a choice changes an accepted outcome or constraint, a consequential trade-off is unsettled, or an external action is required). Present one question at a time from `.agentic/ship/question.md` and record the answer with:
  ```bash
  uv run python .dovo/catalog/scripts/sdlc/ship/ship_state.py record --action ask --question-id <id> --answer "<answer>"
  ```

Repeat `next` → stage → `record` until `record` emits a terminal `outcome` (`ready`, `no_action`, `blocked`, or `incomplete`).

### 3. Gate, publish, and report (`publication-gate`, `publish`, `report`)

1. Run the publication gate:
   ```bash
   uv run python .dovo/catalog/scripts/sdlc/ship/ship_state.py publication-gate
   ```
   Publication is permitted only when `outcome` is `ready` (the live GitHub issue `body_sha` still matches `.agentic/ship/contract.json`, and both `last_validation` (`passed`) and `last_review` (`ready`) match current `HEAD`).
2. When `publication-gate` emits `outcome=ready`, publish the single pull request and review comment (`scripts/pr.py` and `scripts/publish_review.py --pr <pr-url>`), write `.agentic/ship/pr.json` (`url`, `head_sha`), and never merge the pull request.
3. Generate the final delivery artifacts (`.agentic/ship/result.json` and `.agentic/ship/report.md`):
   ```bash
   uv run python .dovo/catalog/scripts/sdlc/ship/ship_state.py report
   ```
