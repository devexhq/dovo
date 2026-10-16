---
name: dovo-rules-migrate
description: >-
  Migrate rules in docs/agents/rules_spec.yaml from prose (guideline plus evaluation_criteria) to an intent plus
  yes/no criteria, one rule at a time, with a Sonnet author agent and an independent Sonnet reviewer agent.
  Invoked as /dovo-rules-migrate <PREFIX|RULE-ID>...
disable-model-invocation: true
---

# dovo-rules-migrate

Rewrite each rule as an `intent` plus a list of yes/no `criteria` (the shape is documented at the top of
`docs/agents/rules_spec.yaml`). An author agent rewrites it; a different agent audits the result from the diff. The audit
file is the receipt, and `scripts/rules_audit.py check` rejects an incomplete or tampered one.

## Hard rules

- **Author and reviewer are always Sonnet.** Dispatch both with `model: "sonnet"` and `subagent_type: "claude"`, set
  explicitly, never inherited.
- **The author never sees the audit file or its questions.** The reviewer never edits the rule.
- **Nobody approves a policy change except a human.** `approved_by` stays null until the human fills it in.
- **Agents never run `scripts/compile_rules.py` without `--check`.** The driver regenerates the compiled files once,
  after the last rule of a prefix.
- **Do not commit or push** from inside the loop.

## Commands

```bash
uv run python scripts/rules_audit.py generate --prefix <PREFIX>   # blank audit files under .agentic/rules-audit/
uv run python scripts/rules_audit.py merge <RULE-ID>              # splice fragments/<RULE-ID>.yaml into the spec
uv run python scripts/rules_audit.py check <RULE-ID>              # validate a filled audit file
```

`merge` validates the fragment against the schema, replaces only that rule's text in `rules_spec.yaml` (under a lock, so
authors may run in parallel), proves no other rule changed, then archives the previous review under
`.agentic/rules-audit/history/` and prefills the rule's audit file from the merged spec.

## Driver loop

1. `generate --prefix <PREFIX>`.
2. For each rule, dispatch an **author** (prompt below). Authors for different rules or prefixes may run in parallel.
3. When the author reports that `merge` succeeded, dispatch a **reviewer** (prompt below) for that rule.
4. Run `check <RULE-ID>`. On any FAIL or error, send the reviewer's findings back to the same author (SendMessage),
   which rewrites the fragment and runs `merge` again, then re-review. **At most two fix rounds**; anything still
   failing goes in the final report.
5. After the last rule of the prefix: `uv run python scripts/compile_rules.py`, then
   `uv run python scripts/compile_rules.py --check` and `check --prefix <PREFIX>`.
6. Show the human every `policy_changes` entry and every BLOCKED slot, grouped by rule. Wording-only changes need no
   approval; a new or removed requirement, a severity, scope or domain change, or an added exemption does. The human
   fills `approved_by`; then `check --prefix <PREFIX> --require-approval` must pass before anything is committed.

## Author prompt

Give the author the rule id, the path `.agentic/rules-audit/fragments/<RULE-ID>.yaml`, and this brief:

- Rewrite the rule into the migrated shape. Read the schema comment at the top of `docs/agents/rules_spec.yaml` and one
  already-migrated rule as a worked example. Write the whole rule as a one-item YAML list starting with `- id:` to the
  fragment path, then run `uv run python scripts/rules_audit.py merge <RULE-ID>` and fix any error it prints.
- Preserve the rule. Every sentence of the old `guideline` and every clause of the old `evaluation_criteria` must end up
  in the `intent`, a criterion or an example. Report anything dropped, weakened or added, and why.
- Keep permissions as permissions and preferences as preferences. Do not turn "allowed" or "preferred" into a pass/fail
  obligation, and do not add requirements.
- `intent`: one or two sentences, 40 words or fewer, saying what the rule protects, with no checkable details.
- Each criterion is one yes/no question about one fact, starting with Does, Do, Is, Are, Was, Were, Has or Have, phrased
  so that YES means compliant, with an `na_when` if it can be inapplicable and its own example pair.
- **Verify every example against the repo before writing it.** Every symbol, field, enum value, path and test name an
  example mentions must exist, and every statement it makes about existing code must be true. If an example would
  describe something hypothetical, say so in the example text.
- Leave `severity`, `scope`, `domain`, `category` and `citation` unchanged unless you report it as a policy change.
- Do not open `.agentic/rules-audit/rules/`. Do not edit any other rule, `scripts/` or `tests/`.
- Report: what changed, every dropped/weakened/added item, every policy change, and anything a stricter reviewer
  might reject.

## Reviewer prompt

Give the reviewer the rule id and the path `.agentic/rules-audit/rules/<PREFIX>/<RULE-ID>.json`, and say: you are the
reviewer, read `shared.json` and follow its `instructions` exactly, fill in the audit file, then run
`uv run python scripts/rules_audit.py check <RULE-ID>` and fix any structural error it reports. Do not edit the rule or
the fragment. Reply with the FAIL and BLOCKED slots, their evidence, and the policy changes you listed.

## Notes

- A BLOCKED slot means the fix is outside the rule text (a doc section, a skill, a citation target). It never blocks
  the loop, but the human must resolve it before the PR.
- `check` verifies that every `path:line` citation exists, that quoted text appears on that line, and that every
  `@<id>.<field>` reference resolves. It cannot verify `ran:` evidence, so spot-check a few.
