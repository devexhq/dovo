# Author brief

You are rewriting one rule in `docs/agents/rules_spec.yaml` into the migrated shape. Another agent will review your work
afterwards; you will not see its checklist.

1. Read the schema comment at the top of `docs/agents/rules_spec.yaml` and one already-migrated rule there (for example
   `DRY-001` or `FS-002`) as a worked example.
2. Write the whole rule as a one-item YAML list starting with `- id: <RULE-ID>` to
   `.agentic/rules-audit/fragments/<RULE-ID>.yaml` (create the directory if needed).
3. Run `uv run python scripts/rules_audit.py merge <RULE-ID>`. It validates the fragment and splices it into the spec;
   fix any error it prints and run it again until it succeeds. The spec changes only through this command.

## Rules for the rewrite

- Preserve the rule. Every sentence of the old `guideline` and every clause of the old `evaluation_criteria` must end up
  in the `intent`, a criterion or an example. Report anything you drop, weaken or add, and why.
- Keep permissions as permissions and preferences as preferences. Do not turn "allowed" or "preferred" into a pass/fail
  obligation, and do not add requirements.
- `intent`: one or two sentences, 40 words or fewer, saying what the rule protects. Say why, not how: no mechanism,
  paths, counts or names that a criterion checks.
- Each criterion is one yes/no question about one fact, starting with Does, Do, Is, Are, Was, Were, Has or Have, phrased
  so that YES means compliant. Give it an `na_when` if it can be inapplicable, and its own example pair.
- **Verify every example against the repo before writing it.** Every symbol, field, enum value, path and test name an
  example mentions must exist, and every statement it makes about existing code must be true. If an example describes
  something hypothetical, say so in the example text. An old example that is false should be replaced, and you should
  report it.
- Leave `severity`, `scope`, `domain`, `category` and `citation` unchanged unless you report it as a policy change.

## Constraints

- Do not open anything under `.agentic/rules-audit/rules/` or `.agentic/rules-audit/shared.json`.
- Edit no other rule, nothing under `scripts/` or `tests/`, and do not edit the spec directly.
- Use the Edit or Write tool for files; never sed, awk or inline Python to modify files.
- Never run `scripts/compile_rules.py` without `--check`. Do not commit, push, stash, checkout, reset or otherwise change
  git state. Other uncommitted changes are not yours.
- If something is unclear, make the most reasonable choice, record it, and keep going.

## Report

Reply with: (1) a summary of the rewrite, (2) every sentence or clause dropped, weakened or added, (3) any policy change
(severity, scope, domain, exemption, requirement added, removed or tightened), (4) anything unclear in the instructions or
tooling, or that a stricter reviewer might reject, and (5) where your tool calls went.
