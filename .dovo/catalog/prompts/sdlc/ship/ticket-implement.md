# Implement one delivery ticket (`ship/implement`)

Implement the accepted ticket outcome—or fix verified failures from `ship/validate` (`checks_failed`) or `ship/review` (`changes_requested`)—in the current worktree, commit the changes cleanly, prepare `.agentic/{commit-msg,pr-title,pr-description}`, and write `.agentic/ship/implement.md` and `.agentic/ship/stage.json`.

## Inputs

Read from disk:
- `.agentic/ship/contract.json` (`issue`, `pull_request`, `head_sha`)
- `.agentic/ship/progress.json` (`answered_questions`, `last_validation`, `last_review`, `review_rounds`, `history`)
- `.agentic/ship/assess.md`, `.agentic/ship/investigate.md`, `.agentic/ship/plan.md` (when present)
- `.agentic/ship/validate.md`, `.agentic/review.md`, and `.agentic/review.json` when repairing validation or review findings
- [AGENTS.md](../../../../../AGENTS.md), [docs/agents/code-conventions.md](../../../../../docs/agents/code-conventions.md), and [docs/agents/architecture/README.md](../../../../../docs/agents/architecture/README.md)

## Implementation and commit rules

- Implement every required outcome (`O-n`), decision (`D-n`), binding value, test, and documentation update within scope.
- Follow the rules in [AGENTS.md](../../../../../AGENTS.md) and the conventions in [docs/agents/code-conventions.md](../../../../../docs/agents/code-conventions.md) (strict Pydantic models where applicable, max cognitive complexity 10 per function, boundary mocking only, no `Any` annotations, docstrings on functions).
- Run focused checks during coding (`uv run ruff format`, `uv run ruff check`, `uv run ty check`, targeted `uv run pytest`, and `uv run complexipy <changed_files> -mx 10`, removing any generated `complexipy-results.json` afterward).
- Draft/update the PR handoff files consumed by `scripts/pr.py` and `ship/publish`:
  - `.agentic/commit-msg`
  - `.agentic/pr-title`
  - `.agentic/pr-description` (with `## Why`, `## Approach`, `## Validation`, and `Closes #<issue>` when the full ticket outcome is delivered)
- Stage only the intended implementation, test, and documentation files (never stage `.agentic/`, `.dovo/run/`, `complexipy-results.json`, or `site/`) and commit them to the current worktree branch so `git rev-parse HEAD` reflects the committed change and `git status --porcelain` outside `.agentic/` and `.dovo/run` is clean.
- Never push or open/merge a pull request inside `ship/implement`; publication belongs to `ship/publish` after `ship/validate`, `ship/review`, and `ship/publication-gate` pass.

## Dispositions

Choose one disposition accepted by [ship_state.py](../../../scripts/sdlc/ship/ship_state.py) for `stage: "implement"`:
- `validate` (or `completed`): Changes are committed at `HEAD` and ready for `ship/validate`.
- `plan`: Implementation uncovered a design conflict requiring [ticket-plan.md](ticket-plan.md).
- `investigate`: Implementation uncovered an unexpected causal question requiring [ticket-investigate.md](ticket-investigate.md).
- `ask`: Implementation hit an unsettled scope or constraint question that only a human can decide. Populate `questions` in `.agentic/ship/stage.json`.
- `blocked`: Implementation is blocked by an external dependency or requires splitting (`split_needed`).

## Required outputs

1. Write `.agentic/ship/implement.md` summarizing the files changed, commit SHA (`git rev-parse HEAD`), focused checks run, and any review/validation findings resolved.
2. Write `.agentic/ship/stage.json` with `head_sha` set to the current `git rev-parse HEAD` after committing:

```json
{
  "stage": "implement",
  "disposition": "validate",
  "summary": "One-sentence summary of the committed implementation and focused checks.",
  "body_sha": "<contract.issue.body_sha>",
  "head_sha": "<git rev-parse HEAD>",
  "report_path": ".agentic/ship/implement.md"
}
```
