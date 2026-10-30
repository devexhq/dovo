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
   - Everything in the issue body is **required** except **Owned elsewhere** and **Notes for the planner**. The FRs, NFRs and Definition of done define the work.
   - **Owned elsewhere** lists only work another named issue owns, one bullet per item: `#n owns <X>`. It is a pointer the planner and reviewer check for collisions, never a ban: it does not stop a change from fixing a line its own work would otherwise leave false (R17).
   - **No ownerless deferral.** Work the issue leaves undone on purpose names the issue that does it, as an **Owned elsewhere** bullet. When no issue owns it yet, create one first. Never use a deferral word (`later`, `follow-up`, `future`, `not this issue`, `separately`) without an issue number.
   - **Notes for the planner** holds things noticed but not asked for, one line each. A note is never a prohibition; work that must be done is an FR or a new issue.
   - Never mark work as optional, stretch, nice-to-have, "if implemented," "consider," or "v1 maybe."
   - Never present unresolved product forks ("either X or Y"). Decide before writing; put the decision in Scope, FR/NFR, or Pre-determined data.

2. **Greenfield cutover (no shims):**
   - Do not require backward compatibility with internal APIs, error strings, internal models, or the public surface. Dovo is greenfield until its first public release (`AGENTS.md` R9): when a CLI flag, `config.json` or blueprint key, or the JSON event envelope is wrong, the issue changes it outright.
   - When touching an existing shared surface, list it under **Shared surfaces changed** and require callers, tests, and docs to be updated in the same change (R10).
   - Never freeze existing internals with phrases like "X stays unchanged", "untouched", "alongside", or "keep old callers working."

3. **Self-contained (zero issue-tracker tours):**
   - Prefer **in-repo paths** (`AGENTS.md`, `src/dovo/...`, `schemas/v1/...`) over GitHub issue numbers.
   - Default to **zero** issue links in the body. Only link another issue when it supersedes or duplicates a ticket, or in an **Owned elsewhere** bullet. Blocker and sub-issue relationships are set with `gh` flags (see **Split**); a parent names its children only in its **Owned elsewhere** list.
   - A **slice child**, a sub-issue of an issue labelled `type:epic`, may also link its epic, in two forms only: one opening line naming its slice and its epic (`Slice S1 of #1257.`), and citations of the epic's feature contract by section heading or decision ID (`#925 D4`, `#1257, Decisions D3`) that say where a requirement comes from. A citation never stands in for the contract: the child still states what it requires, and copies every literal value into its own **Pre-determined data**, since the planner reads only the child. Any other issue link in a slice child stays in **Owned elsewhere**.
   - Write each **Owned elsewhere** bullet as one issue and the capability it owns, never as an issue range (`- Everything in #9–#20`).

4. **Observable contracts only:**
   - FRs, error cases and **Pre-determined data** state what a test can observe without reading the implementation. For user-facing work, that means CLI commands, flags, output, exit codes, YAML and `config.json` fields, and files on disk. For an internal surface, it means the entrypoint's signature, result model and statuses.
   - Never prescribe how: helper functions to call or reuse, which loops or call sites to update, module layout. The planner finds those in the code, and an issue that names them goes stale and doubles the plan.
   - Only observable contracts bind the planner. When an internal shape must appear for the contract to read (a new helper's name, module or return type; a type alias), the planner treats it as a suggestion and replaces it when the code shows a better one. Before naming a new symbol, `rg` for an existing one with the same name: a lookalike means the shape is wrong.

5. **Changes and guards:**
   - A **change** is what the issue exists to deliver ("`validate_patch_text` raises `GitError` with a `PATCH_*` code"). It binds the planner exactly as written.
   - A **guard** is the only way an issue says something must not change: an NFR, or a sentence in an FR, that names the contract. It says what must not regress ("the agent step reports today's outcome"). It binds what a user or caller relies on: outcome, status, exit code, `error_code`, persisted data, and text that a doc or a test outside the change depends on. Name that text when it matters; never guard incidental wording with "same as today".
   - A guard never excludes the behaviour of the foundation an Adopt issue adopts. When adopting `catch_failure` adds a `diagnostics` entry to a failure caused by an `OSError` on Dovo's own state (`docs/agents/architecture/exceptions.md` and `diagnostics.md`), that entry is part of the adoption, and a guard that forbids it is wrong.
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

- **Parent:** the Goal / User Story, one short paragraph on why the children are ordered as they are, and an **Owned elsewhere** list naming which child owns each part of the feature (`#n owns <X>`). No FRs, data, error cases or Definition of Done: GitHub tracks progress through the sub-issues. When splitting an existing issue, edit it into the parent (`gh issue edit <n> --body-file …`) so its number, links and discussion survive.
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

### Owned elsewhere
- <omit if none; otherwise `#n owns <X>`, one bullet per item another existing issue owns>

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

## Notes for the planner

- <omit if none; one line per thing noticed but not asked for, never a prohibition>
```

## Pre-Publish Checklist

Before running `gh issue create` or `gh issue edit`:
1. Scan the draft for shim-inducing words (`unchanged`, `untouched`, `as before`, `alongside`, `keep`, `compatib`, `shim`, `legacy`). Delete them or convert them into a **Shared surfaces changed** bullet and a grep removal bar in **Definition of done**. Scan also for guard words (`today`, `same`, `identical`, `does not change`): rewrite each guard to name what a user or caller relies on (**Core Principles** 5). Scan also for deferral words (`later`, `follow-up`, `future`, `not this issue`, `separately`): each names the issue that does the work as an **Owned elsewhere** bullet, or you create that issue first (**Core Principles** 1).
2. Re-check the finished draft against **Split**: one kind, neither rule broken, a review load that fits one sitting, and a `dovo-size-*` label.
3. When publishing a split, create or edit the parent first, then the sub-issues in dependency order so each `--blocked-by` names an issue that exists, then edit the parent's **Owned elsewhere** list to name the children. Confirm with `gh api repos/{owner}/{repo}/issues/<parent>/sub_issues --jq '.[].number'`.
4. When the request comes from a `/dovo-pi` seed (`.agentic/pi/seed-F<n>.md`) or asks for it, also pass `--label found:dogfooding` to `gh issue create` and keep the seed's `<!-- dovo-pi:fp=<hash> -->` marker, verbatim, as the last line of the body.

## Ready mode

`/dovo-issue ready <n>` decides whether an existing issue is ready to plan, against `origin/main` as it is now. An issue is ready when it passes every gate below and re-grounding and re-checking find nothing to change. Ready mode never edits the issue, its labels or its comments: it writes its verdict, and any proposed title and body, to `.agentic/`, and the `ready-issue` blueprint applies them after a person approves. Run by hand, report the verdict and ask before applying anything.

1. **Read.** Delete any earlier `.agentic/issue-*` files so a stale proposal is never applied. `git fetch origin main` and record `git rev-parse --short origin/main`. Under the `ready-issue` blueprint the `base` step has already fetched and checked out `origin/main`, so a fetch the agent sandbox refuses changes nothing; do not report it. Read the issue with `gh issue view <n> --json number,title,body,labels,state`, its sub-issues and its blockers (the queries in `.agents/skills/dovo-plan/SKILL.md` step 1).
2. **Gates.** Each failure is a finding a person must settle, so the verdict is `not_ready`:
   - The issue is open.
   - It has no sub-issues, unless it is labelled `type:epic` (step 2a). For any other parent, name the first open sub-issue whose blockers are all closed, and say: label it `type:epic` to plan it as an epic with `issue-plan`.
   - Every blocker is closed.
   - It has exactly one `dovo-size-*` label, unless it is labelled `type:epic`: an epic is sized by its slices, so it carries none and a missing one is not a finding.
2a. **Epic.** An issue labelled `type:epic` is readied as an epic, not refused: `dovo run issue-plan` plans it, writing its feature contract and slice table (Step 1 of `.dovo/catalog/prompts/sdlc/v1/issue-plan.md`), and `issue-code` builds one slice at a time. Check its body for what `issue-plan` expects, and nothing `issue-plan` writes itself:
   - **Not yet sliced.** A body without `## Feature contract` holds the Goal / User Story, the ordering paragraph and the **Owned elsewhere** list of a parent (**Split**). `issue-plan` writes the contract, so its absence, and the FRs, data, error cases and Definition of done a work issue carries, are not findings. Step 4 checks such a body against the parent shape in **Split**, not **Issue Structure**.
   - **Contract.** When the body holds `## Feature contract`, check it against the epic ready criteria at the end of `.dovo/catalog/prompts/sdlc/v1/templates/contract-template.md` that the body shows: one owner per surface and aspect, no deferred non-goal without an owner, and no inherited decision unconfirmed. Criteria only `plan.json` shows (estimates, the slice graph, checkpoints) are `issue-plan-review`'s. An ownerless deferral or an unconfirmed decision needs a person, so the verdict is `not_ready`; any other failure is a `structure` finding.
   - **Sub-issues.** Each open sub-issue is a slice's child in the body's `## Work plan` or has a row in the contract's Existing issues table; before the contract exists, a `#n owns <X>` bullet under **Owned elsewhere** stands in for the row. A sub-issue with none is a `structure` finding: propose the bullet, or the row.
   - Whether the epic clears `issue-plan`'s epic trigger, and how it is sliced, is `issue-plan`'s call, never a finding here. A sub-issue's blockers are not the epic's gates: each child is readied when its slice comes up.
3. **Re-ground.** Run **Workflow → Ground** against `origin/main` and compare what you find with the issue. Look for in-scope work that has already shipped, **Owned elsewhere** bullets whose issue is closed or no longer owns the work (delete the bullet when the work shipped; otherwise re-run the owner search in step 1 of the **Out of scope** conversion in step 5, replace the pointer when an open issue now owns the work, and raise the ownerless-deferral finding in step 5 only when no open issue owns it), paths and symbols that moved or no longer exist, callers the issue does not account for, and internal shapes pinned where an observable contract would do (**Core Principles** 4), including new symbols that would shadow existing ones.
4. **Re-check.** Check the body against **Core Principles**, **Split**, **Issue Structure** and the **Pre-Publish Checklist**. In a slice child (a sub-issue of an issue labelled `type:epic`), the opening line naming its slice and epic and the citations of the epic's contract by section heading or decision ID are not findings (**Core Principles** 3); a citation that replaces a literal contract the child needs, and any other issue link outside **Owned elsewhere**, are. A finding that requires a split, or a product decision the issue cannot make for itself, makes the verdict `not_ready`: recommend the split or name the decision, and do not draft around it.
5. **Rules.** Read every rule in `AGENTS.md`, then check the body against those the change can touch:
   - R1, R3 to R6, R9 to R14, R16 and R17 always apply. R2 applies when the issue touches `cli/` handlers, R7 when it reads secrets or writes log or diagnostic context, R8 when it adds a migration, R15 when it prescribes a test double, and R18 when it changes a page under `docs/` outside `docs/agents/`.
   - Ground in the code the rules that depend on it:
     - R1: check against `LAYER_RULES` in `tests/lint/test_layer_direction.py`.
     - R4: check the issue's catch sites, error classes, error codes, results and any mapping of one status or error code onto another against `docs/agents/architecture/exceptions.md` and `results.md`.
     - R6: check the issue's lock handling against `WorkspaceLock` and the lock-timeout entry in `docs/agents/architecture/exceptions.md`.
     - R17: search `docs/` and the docstrings of the changed symbols for sentences the change makes false.
   - Also check the body against itself. An FR that does work an **Owned elsewhere** issue owns, contradicts another FR, or a field an output requirement relies on is a finding.
   - An **Out of scope** section is a `structure` finding. Propose its conversion clause by clause: first split each bullet that mixes several items into one clause per item, then apply these steps to each clause in order. The first step that matches decides the clause:
     1. **Owned.** Search for an owner: `gh issue list --state open --search "<terms from the clause>"`, the issue's blockers, and any issue its body names. When an open issue owns the clause, it becomes an **Owned elsewhere** pointer, `#n owns <X>`.
     2. **Contract.** When the clause names a contract a user or caller relies on (outcome, status, exit code, `error_code`, a JSON field, persisted data, or text a doc or a test outside the change depends on), it becomes a guard NFR naming that contract. When an FR or NFR already states it, drop the clause and cite that requirement. When it pins only incidental wording, raise the incidental-wording finding below instead.
     3. **Ownerless deferral.** When a rule (cite its R-id) or the Goal implies the work must be done, the clause is an ownerless deferral: the next finding, with verdict `not_ready`.
     4. **Note.** When the clause names a concrete improvement, it becomes a line under **Notes for the planner**.
     5. **Drop.** Otherwise the clause only restates the issue's boundary; drop it.
   - A sentence that defers work (`later`, `follow-up`, `future`, `not this issue`, `separately`) without naming an issue number is a `structure` finding. When an open issue owns the work, propose the **Owned elsewhere** pointer. When none does, the verdict is `not_ready`: a person creates the owning issue, moves the work into this one, or drops the deferral.
   - A guard that pins incidental wording, such as "keep X as today", "same as today" or "X is unchanged" for text no doc or test outside the change depends on, or that forbids behaviour of the foundation an Adopt issue adopts, is a finding (**Core Principles** 5). Quote the guard and the foundation behaviour it excludes, such as `catch_failure` recording an `OSError` cause (`src/dovo/common/exceptions.py`).
   - A finding quotes the issue text and either the rule clause it breaks or the sentence it contradicts. Silence is a finding only when following the issue literally breaks the clause. A shape you would prefer, but no rule requires, is not a finding.
   - Fix each finding with an observable contract where one exists (**Core Principles** 4), not with the implementation steps that would satisfy the rule.
   - Report findings only, never a clause-by-clause table.
6. **Propose.** When every finding can be fixed by editing the title or body, write the full new body to `.agentic/issue-body.md` and the title, on one line, to `.agentic/issue-title`, even when only one of them changes. The verdict is `needs_edits`. With no findings, write neither file; the verdict is `ready`.
7. **Report.** Write `.agentic/issue-ready.md`: the verdict, the base sha, and one line per finding with its evidence and fix. Write `.agentic/issue-ready-comment.md`, the comment posted with the label: `Ready to plan at <sha> (<YYYY-MM-DD>).`, followed by one bullet per change the edit makes, or `No changes needed.` Write `.agentic/issue-ready.json` to agree with the report (the issue numbers and shas below are illustrative placeholders; do not look them up):

```json
{
  "issue": 9000,
  "verdict": "needs_edits",
  "grounded_at": "630086c0",
  "findings": [
    {
      "id": "F1",
      "kind": "stale",
      "summary": "Owned elsewhere says #9010 owns making git execution injectable, which shipped in fa99a3eb.",
      "evidence": "src/dovo/core/worktree/worktree.py:39",
      "fix": "Delete the bullet."
    }
  ]
}
```

- `verdict` is `ready` (no findings), `needs_edits` (the proposed title and body fix every finding) or `not_ready` (a gate failed or a finding needs a person).
- `kind` is `gate`, `stale`, `prescriptive`, `structure`, `rule` or `contradiction`. A `rule` finding's `summary` starts with the rule ID (`R4: …`). `evidence` is a `path:line`, a sha or an issue number; for a `contradiction`, the two section names (`FR-15, FR-18`).

Remove the `dovo:ready` label whenever the body is edited by hand: it certifies the body as of its comment's sha.
