# Architecture decisions

Settled design decisions the code cannot show, one file per area. Companion to [AGENTS.md](../../../AGENTS.md), whose rules link the entries here.

## Folder rules

- **Decisions only.** An entry states a rule and its reason. No inventories: no file trees, class lists or module maps.
- **One entry per decision**, in the format below. Each entry stands on its own: its Decision bullets state the whole rule, edge cases included.
- **Code that predates a rule.** A decision binds all new and changed code. Existing code may predate it until the issue that converts it lands. Known violations are tracked in issues, not in these docs.
- **Current state only.** When a decision changes, rewrite or delete its entry and cite the issue that changed it in **Since**. Never append history.
- **A new area file needs an issue or plan behind it.** Add an area only once it has a settled decision. No placeholder files.
- **One home per rule.** `AGENTS.md` keeps the one-line rule and links here; the entry holds the detail. File-level models stay in `AGENTS.md`'s exemplars table.

## Entry format

Every level-2 heading in an area file is one decision. Its body holds only these field bullets, each at most once, in this order.

- `- **Decision:**` the rule. Nested bullets state its parts and edge cases. Required.
- `- **Why:**` the reason, in one or two sentences. Required.
- `- **Since:**` the issue that set or last changed the decision. Required.
- `- **Example:**` a short code snippet, only where the rule is about code shape. Optional, and last.
- No "enforced by" field: a lint check that enforces an entry cites it in its failure message as `docs/agents/architecture/<file>.md#<heading>`.

## Format limits

`tests/lint/test_architecture_docs.py` enforces these for every file in this folder, this one included.

- Headings use levels 1 to 3. A decision entry has no sub-headings and no paragraphs, only its field bullets.
- Any other section opens with at most one paragraph of 40 words or fewer; after it come only bullets, sub-headings and code blocks.
- No code block, nested ones included, is longer than 10 lines.
- Every backticked repo path and every relative link resolves, and paths carry no `:<line>` suffix, because line numbers rot.
- Every `docs/agents/architecture/<file>.md#<heading>` cited under `tests/` names a real file and heading.
- Each file is under 150 lines.
- Every area file is listed in the index below.

## Index

- [exceptions.md](exceptions.md): domain errors, `catch_failure`, error codes, lock timeouts and declined confirmations.
- [remediations.md](remediations.md): how a remediation's message and command are written.
- [diagnostics.md](diagnostics.md): diagnostic-log records, their levels and where they are reported.
- [results.md](results.md): entrypoint results, the result header, `ok`, `status` and helper return values.
