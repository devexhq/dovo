# Architecture decisions

Settled design decisions the code cannot show, one file per area. Companion to [AGENTS.md](../../../AGENTS.md), whose rules link the entries here.

## Folder rules

- **Decisions only.** An entry states a rule and its reason. No inventories: no file trees, class lists or module maps.
- **One entry per decision**, in the format below.
- **Current state only.** When a decision changes, rewrite or delete its entry and cite the issue that changed it in **Since**. Never append history.
- **A new area file needs an issue or plan behind it.** Add an area only once it has a settled decision. No placeholder files.
- **One home per rule.** `AGENTS.md` keeps the one-line rule and links here; the entry holds the detail.

## Entry format

Every level-2 heading in an area file is one decision. Its body is a bullet list of these fields, each at most once, in this order.

- `- **Decision:**` the rule. Nested bullets may list its parts. Required.
- `- **Why:**` the reason, in one or two sentences. Required.
- `- **Exemplar:**` a backticked repo path that fully follows the entry, or no field when none does.
- `- **Enforced by:**` exactly one of: one or more backticked test paths, `review only`, or `pending #<issue>` with an optional `(phase <n>)`. Required.
  - Nested bullets under it may name code on `main` that still departs, and the issue that fixes it, or "not yet owned".
- `- **Since:**` the issue that set or last changed the decision.

## Format limits

`tests/lint/test_architecture_docs.py` enforces these for every file in this folder, this one included.

- Headings use levels 1 to 3.
- A section opens with at most one paragraph of 40 words or fewer; after it come only bullets, sub-headings and code blocks.
- No code block, nested ones included, is longer than 10 lines.
- Decision entries hold only the fields above, each once and in order.
- Every backticked repo path and every relative link resolves, and a `path::Class::test` citation names an existing class and test.
- Paths carry no `:<line>` suffix, because line numbers rot.
- Each file is under 150 lines.
- Every area file is listed in the index below.

## Index

- [exceptions.md](exceptions.md): domain errors, `catch_failure`, error codes, lock timeouts and declined confirmations.
- [remediations.md](remediations.md): how a remediation's message and command are written.
- [diagnostics.md](diagnostics.md): diagnostic-log records, their levels and where they are reported.
- [results.md](results.md): entrypoint results, the result header, `ok`, `status` and helper return values.
