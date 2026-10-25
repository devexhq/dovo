---
name: dovo-issue
description: >-
  Create or update an implementation-ready, self-contained GitHub issue for Dovo
  CLI with authoritative scope, numbered FR/NFR contracts, pre-determined data,
  error cases, and a non-redundant Definition of Done. Grounds the issue in the
  code first and splits work that mixes more than one responsibility into
  a parent issue with native sub-issues. Invoked as /dovo-issue; /dovo-issue
  ready <n> re-grounds an existing issue against main and reports whether it is
  ready to plan. Use when asked to create, draft, revise, or ready a GitHub issue.
---

# dovo-issue

Draft, create, or revise an authoritative, self-contained GitHub issue for Dovo CLI.
Canonical example of structure, depth, and tone: [examples/github-issue.md](examples/github-issue.md).

An issue is a working document. Aim for a sensible issue, not a perfect one: when planning or review shows it is mis-sized or mis-scoped, split or edit it then.

## Core Principles

1. **Authoritative (no soft decisions):**
   - Everything in the issue body is **required** unless under **Out of scope**.
   - **Out of scope** means not required, never forbidden. It bounds the work; it does not stop the planner from changing an out-of-scope line that the in-scope change would otherwise leave inconsistent.
   - Never mark work as optional, stretch, nice-to-have, "if implemented," "consider," or "v1 maybe."
   - Never present unresolved product forks ("either X or Y"). Decide before writing; put the decision in Scope, FR/NFR, or Pre-determined data.

2. **Greenfield cutover (no shims):**
   - Do not require backward compatibility with internal APIs, error strings, internal models, or the public surface. Dovo is greenfield until its first public release (`AGENTS.md` R9): when a CLI flag, `config.json` or blueprint key, or the JSON event envelope is wrong, the issue changes it outright.
   - When touching an existing shared surface, list it under **Shared surfaces changed** and require callers, tests, and docs to be updated in the same change (R10).
   - Never freeze existing internals with phrases like "X stays unchanged", "untouched", "alongside", or "keep old callers working."

3. **Self-contained (zero issue-tracker tours):**
   - Prefer **in-repo paths** (`AGENTS.md`, `src/dovo/...`, `schemas/v1/...`) over GitHub issue numbers.
   - Default to **zero** issue links in the body. Only link another issue when it supersedes or duplicates a ticket. Parent and blocker relationships are set with `gh` flags (see **Split**), never written into the body.
   - Write **Out of scope** as capability bullets, never as issue ranges (`- Everything in #9–#20`).

4. **Observable contracts only:**
   - FRs, error cases and **Pre-determined data** state what a test can observe without reading the implementation. For user-facing work, that means CLI commands, flags, output, exit codes, YAML and `config.json` fields, and files on disk. For an internal surface, it means the entrypoint's signature, result model and statuses.
   - Never prescribe how: helper functions to call or reuse, which loops or call sites to update, module layout. The planner finds those in the code, and an issue that names them goes stale and doubles the plan.
   - Only observable contracts bind the planner. When an internal shape must appear for the contract to read (a new helper's name, module or return type; a type alias), the planner treats it as a suggestion and replaces it when the code shows a better one. Before naming a new symbol, `rg` for an existing one with the same name: a lookalike means the shape is wrong.

5. **Changes and guards:**
   - A **change** is what the issue exists to deliver ("`validate_patch_text` raises `GitError` with a `PATCH_*` code"). It binds the planner exactly as written.
   - A **guard** says what must not regress ("the agent step reports today's outcome"). It binds what a user or caller relies on: outcome, status, exit code, `error_code`, persisted data, and text that a doc or a test outside the change depends on. Name that text when it matters; never guard incidental wording with "same as today".
   - A guard never excludes the behaviour of the foundation an Adopt issue adopts. When adopting `catch_failure` adds a `Details logged:` line to a failure caused by an `OSError`, that line is part of the adoption, and a guard that forbids it is wrong.
   - The planner may depart from a guard, never from a change, with an Open Question that names the departure ([planning.md](../../../docs/agents/planning.md) Step 1).

## Workflow

### 1. Ground

Before drafting, read the entry points of every surface the issue touches, and their callers. Record:
- **Existing bugs on the path** the work builds on. Each becomes its own sub-issue, which blocks the work that depends on it.
- **Persisted formats** the work changes (session state and manifests, `data.db`, `config.json`, catalog `index.json`, blueprint and step YAML). List each under **Shared surfaces changed**, with what happens to data written before the change.

Only read; never edit code or run tooling that mutates or validates. Read-only `git`, read-only `gh`, `rg` and file reads are enough.

### 2. Split

An issue is one kind of change that a reviewer can describe in one sentence without "and". It lands as one PR.

Split issues by kind and by review load. Review load is the number of distinct decisions a reviewer must check, not the number of lines, files or packages. A package that needs its own codes, mappings or deletions is a decision of its own. Keep an issue to one kind and a load a reviewer can hold in one sitting; split where a new decision would start a second review.

| Kind | Changes |
|---|---|
| Fix | One wrong behaviour, or a few small ones in one area, each with its own regression test |
| Foundation | A new shared type, tool or rule that two or more issues adopt |
| Adopt | One or more packages move onto a foundation and delete what it replaces; they take on the foundation's behaviour with it |
| Refactor | Structure only, worth merging on its own; behaviour is identical |
| Remove | Deletes what nothing uses any more |
| Feature | New user-facing surface |

Two rules hold for every split:
- **Don't mix kinds.** A foundation and its adoption, or a behaviour change and a refactor, are separate issues.
- **Don't split if the halves need a bridge.** When one half would have to map the new shape back onto the old one so the other half still works, keep them as one issue.

Two kinds fold into another issue instead of standing alone. A refactor that only prepares one issue is that issue's first plan phase. A foundation with one adopter goes into the adopter's issue.

Label every issue except a split's parent `dovo-size-s`, `dovo-size-m` or `dovo-size-l` for its rough review time.

Every split has a parent issue and native GitHub sub-issues:

- **Parent:** the Goal / User Story, one short paragraph on why the children are ordered as they are, and an **Out of scope** list for the whole feature. No FRs, data, error cases or Definition of Done: GitHub tracks progress through the sub-issues. When splitting an existing issue, edit it into the parent (`gh issue edit <n> --body-file …`) so its number, links and discussion survive.
- **Sub-issues:** full issues in the structure below, each self-contained. The planner reads only the sub-issue, never the parent or its siblings, so every requirement and literal contract it needs is in its own body. Create each with `gh issue create --parent <parent> --label dovo-size-<s|m|l>`, adding `--blocked-by <n>` when it cannot ship before another sub-issue.

For example, "prompts as a catalog type that agent steps can reference" becomes a parent with three sub-issues: a bug fix on the shared catalog delete path; prompts as a catalog type with `dovo prompt` commands, blocked by the fix; and `prompt_file` on agent steps, blocked by the catalog type.

### 3. Draft

Write each issue in the structure below.

## Issue Structure

Use the sections below in order, and include a section only when it applies (e.g., no CLI copy for an internal refactor). Never add separate **Acceptance criteria**, **Dependencies**, or issue-list **References** sections.

```markdown
## Goal / User Story

**As a** <user>,
**I want** <capability>,
**so that** <benefit>.

<1–3 sentences on the outcome unlocked and key constraints.>

## Scope

### In scope
- <mandatory deliverable>

### Out of scope
- <capabilities not built in this issue; self-contained stop list>

### Shared surfaces changed
- <omit if none; otherwise name each renamed/modified type, field, or file and its new shape; for a persisted format, what happens to data written before the change>

## Functional requirements

### FR-1: <name>
<condition → action → result, written so it is directly testable>

## Non-functional requirements

### NFR-1: <name>
<idempotency, atomic writes, determinism, or greenfield cutover requirements>

## Pre-determined data

<Only the contracts the implementer would otherwise have to invent: DTO/result shapes, status enums, error codes, config keys, paths, entrypoint signatures, or the literal values tests assert, in fenced code blocks or tables.>

## CLI output expectations

### <scenario>
```text
<exact or near-exact console copy>
```

## Error cases to handle

1. <failure condition → expected status, error code, and remediation behavior>

## Definition of done

- <ONLY checks not already obvious from FR/NFR: cross-FR integration bars, grep bars confirming deleted legacy symbols have zero references in src/ and tests/, or doc gates>
```

## Pre-Publish Checklist

Before running `gh issue create` or `gh issue edit`:
1. Scan the draft for shim-inducing words (`unchanged`, `untouched`, `as before`, `alongside`, `keep`, `compatib`, `shim`, `legacy`). Delete them or convert them into a **Shared surfaces changed** bullet and a grep removal bar in **Definition of done**. Scan also for guard words (`today`, `same`, `identical`, `does not change`): rewrite each guard to name what a user or caller relies on (**Core Principles** 5).
2. Re-check the finished draft against **Split**: one kind, neither rule broken, a review load that fits one sitting, and a `dovo-size-*` label.
3. When publishing a split, create or edit the parent first, then the sub-issues in dependency order so each `--blocked-by` names an issue that exists. Confirm with `gh api repos/{owner}/{repo}/issues/<parent>/sub_issues --jq '.[].number'`.

## Ready mode

`/dovo-issue ready <n>` decides whether an existing issue is ready to plan, against `origin/main` as it is now. An issue is ready when it passes every gate below and re-grounding and re-checking find nothing to change. Ready mode never edits the issue, its labels or its comments: it writes its verdict, and any proposed title and body, to `.agentic/`, and the `ready-issue` blueprint applies them after a person approves. Run by hand, report the verdict and ask before applying anything.

1. **Read.** Delete any earlier `.agentic/issue-*` files so a stale proposal is never applied. `git fetch origin main` and record `git rev-parse --short origin/main`. Under the `ready-issue` blueprint the `base` step has already fetched and checked out `origin/main`, so a fetch the agent sandbox refuses changes nothing; do not report it. Read the issue with `gh issue view <n> --json number,title,body,labels,state`, its sub-issues and its blockers (the queries in `.agents/skills/dovo-plan/SKILL.md` step 1).
2. **Gates.** Each failure is a finding a person must settle, so the verdict is `not_ready`:
   - The issue is open.
   - It has no sub-issues. For a parent, name the first open sub-issue whose blockers are all closed.
   - Every blocker is closed.
   - It has exactly one `dovo-size-*` label.
3. **Re-ground.** Run **Workflow → Ground** against `origin/main` and compare what you find with the issue. Look for in-scope work that has already shipped, out-of-scope items that are now done, paths and symbols that moved or no longer exist, callers the issue does not account for, and internal shapes pinned where an observable contract would do (**Core Principles** 4), including new symbols that would shadow existing ones.
4. **Re-check.** Check the body against **Core Principles**, **Split**, **Issue Structure** and the **Pre-Publish Checklist**. A finding that requires a split, or a product decision the issue cannot make for itself, makes the verdict `not_ready`: recommend the split or name the decision, and do not draft around it.
5. **Rules.** Read every rule in `AGENTS.md`, then check the body against those the change can touch:
   - R1, R3 to R6, R9 to R14, R16 and R17 always apply. R2 applies when the issue touches `cli/` handlers, R7 when it reads secrets or writes log or diagnostic context, R8 when it adds a migration, R15 when it prescribes a test double, and R18 when it changes a page under `docs/` outside `docs/agents/`.
   - Ground in the code the rules that depend on it:
     - R1: check against `LAYER_RULES` in `tests/lint/test_layer_direction.py`.
     - R4: check the issue's catch sites, results and any mapping of one status or error code onto another against `docs/agents/code-conventions.md`.
     - R6: check the issue's lock handling against `WorkspaceLock`, `LockTimeoutError` and `catch_failure`.
     - R17: search `docs/` and the docstrings of the changed symbols for sentences the change makes false.
   - Also check the body against itself. An FR that contradicts **Out of scope**, another FR, or a field an output requirement relies on is a finding.
   - A guard that pins incidental wording, or that forbids behaviour of the foundation an Adopt issue adopts, is a finding (**Core Principles** 5). Quote the guard and the foundation behaviour it excludes, such as `catch_failure` recording an `OSError` cause (`src/dovo/common/exceptions.py`).
   - A finding quotes the issue text and either the rule clause it breaks or the sentence it contradicts. Silence is a finding only when following the issue literally breaks the clause. A shape you would prefer, but no rule requires, is not a finding.
   - Fix each finding with an observable contract where one exists (**Core Principles** 4), not with the implementation steps that would satisfy the rule.
   - Report findings only, never a clause-by-clause table.
6. **Propose.** When every finding can be fixed by editing the title or body, write the full new body to `.agentic/issue-body.md` and the title, on one line, to `.agentic/issue-title`, even when only one of them changes. The verdict is `needs_edits`. With no findings, write neither file; the verdict is `ready`.
7. **Report.** Write `.agentic/issue-ready.md`: the verdict, the base sha, and one line per finding with its evidence and fix. Write `.agentic/issue-ready-comment.md`, the comment posted with the label: `Ready to plan at <sha> (<YYYY-MM-DD>).`, followed by one bullet per change the edit makes, or `No changes needed.` Write `.agentic/issue-ready.json` to agree with the report:

```json
{
  "issue": 923,
  "verdict": "needs_edits",
  "grounded_at": "630086c0",
  "findings": [
    {
      "id": "F1",
      "kind": "stale",
      "summary": "Out of scope lists making git execution injectable, which shipped in fa99a3eb.",
      "evidence": "src/dovo/core/worktree/worktree.py:39",
      "fix": "Delete the bullet."
    }
  ]
}
```

- `verdict` is `ready` (no findings), `needs_edits` (the proposed title and body fix every finding) or `not_ready` (a gate failed or a finding needs a person).
- `kind` is `gate`, `stale`, `prescriptive`, `structure`, `rule` or `contradiction`. A `rule` finding's `summary` starts with the rule ID (`R4: …`). `evidence` is a `path:line`, a sha or an issue number; for a `contradiction`, the two section names (`FR-15, FR-18`).

Remove the `dovo:ready` label whenever the body is edited by hand: it certifies the body as of its comment's sha.
