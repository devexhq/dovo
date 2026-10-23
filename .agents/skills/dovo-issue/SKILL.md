---
name: dovo-issue
description: >-
  Create or update an implementation-ready, self-contained GitHub issue for Dovo
  CLI with authoritative scope, numbered FR/NFR contracts, pre-determined data,
  error cases, and a non-redundant Definition of Done. Grounds the issue in the
  code first and splits work larger than one user story or one PR into
  a parent issue with native sub-issues. Invoked as /dovo-issue; invoked as
  /dovo-issue reground <number> to re-check a drafted issue against current main
  and label its size and readiness before work starts. Use when asked to create,
  draft, revise, or re-ground a GitHub issue.
---

# dovo-issue

Draft, create, or revise an authoritative, self-contained GitHub issue for Dovo CLI.
Canonical example of structure, depth, and tone: [examples/github-issue.md](examples/github-issue.md).

## Core Principles

1. **Authoritative (no soft decisions):**
   - Everything in the issue body is **required** unless under **Out of scope**.
   - Never mark work as optional, stretch, nice-to-have, "if implemented," "consider," or "v1 maybe."
   - Never present unresolved product forks ("either X or Y"). Decide before writing; put the decision in Scope, FR/NFR, or Pre-determined data.
   - If scope is too large, split it per **Size and split** rather than parking optional work inside one body.

2. **Greenfield cutover (no shims):**
   - Do not require backward compatibility with internal APIs, error strings, internal models, or the public surface. Dovo is greenfield until its first public release (`AGENTS.md` R9): when a CLI flag, `config.json` or blueprint key, or the JSON event envelope is wrong, the issue changes it outright.
   - When touching an existing shared surface, list it under **Shared surfaces changed** and require callers, tests, and docs to be updated in the same change (R10).
   - Never freeze existing internals with phrases like "X stays unchanged", "untouched", "alongside", or "keep old callers working."

3. **Self-contained (zero issue-tracker tours):**
   - Prefer **in-repo paths** (`AGENTS.md`, `src/dovo/...`, `schemas/v1/...`) over GitHub issue numbers.
   - Default to **zero** issue links in the body. Only link another issue when it supersedes or duplicates a ticket. Parent and blocker relationships are set with `gh` flags (see **Size and split**), never written into the body.
   - Write **Out of scope** as self-contained capability bullets (e.g., `- CLI subcommands (dovo config show|set)`), never `- Everything in #9–#20`. When a sibling issue covers the item, say so: `- Referencing prompts from agent steps (next issue in this split)`.

4. **Observable contracts only:**
   - FRs, error cases and **Pre-determined data** state what a test can observe without reading the implementation. For user-facing work, that means CLI commands, flags, output, exit codes, YAML and `config.json` fields, and files on disk. For an internal surface, it means the entrypoint's signature, result model and statuses.
   - Never prescribe how: helper functions to call or reuse, which loops or call sites to update, module layout. The planner finds those in the code, and an issue that names them goes stale and doubles the plan.

## Workflow

### 1. Ground

Before drafting, read the entry points of every surface the issue touches, and their callers. Record:
- **Existing bugs on the path** the work builds on. Each becomes its own sub-issue, which blocks the work that depends on it.
- **Persisted formats** the work changes (session state and manifests, `data.db`, `config.json`, catalog `index.json`, blueprint and step YAML). List each under **Shared surfaces changed**, with what happens to data written before the change.
- **Call sites** when the work applies one pattern across many places (exception handlers, commands, callers of a replaced function): count them per package, for example with `git grep -c`, and read the matches to drop comments, docs and tests from the count. Size the issue from that count and the tests each site needs.

Only read; never edit code or run tooling that mutates or validates. Read-only `git`, read-only `gh`, `rg` and file reads are enough.

### 2. Size and split

An issue is one user story that ships value on its own and lands as one PR of about 200 to 400 changed lines, tests included. `/dovo-code` implements every phase of a plan in one PR, so the issue is what bounds a PR's size. Split it when any of these hold:
- the **I want** line, or the **As a** line, joins independently valuable outcomes or different users (for example someone running a blueprint and someone running `dovo doctor`)
- more than about 6 FRs, counting an FR that lists separate cases as one FR per case
- more than one new public surface (for example a command group *and* a schema field)
- it changes a persisted format *and* adds CLI surface
- the same change applies across more than two packages or domain areas
- you estimate more than about 400 changed lines, tests included

Split by value delivered, never by layer: each child has its own user story and is useful once merged. Size each child at 200 to 400 changed lines rather than splitting into many smaller ones; an issue under 200 lines that is the whole story stays whole. When the same change spans several domain areas, the first child ships whatever they share together with one area that proves it end to end, and each later child covers one area. Every split has a parent issue and native GitHub sub-issues:

- **Parent:** the Goal / User Story, one short paragraph on why the children are ordered as they are, and an **Out of scope** list for the whole feature. No FRs, data, error cases or Definition of Done: GitHub tracks progress through the sub-issues. When splitting an existing issue, edit it into the parent (`gh issue edit <n> --body-file …`) so its number, links and discussion survive.
- **Sub-issues:** full issues in the structure below, each self-contained. The planner reads only the sub-issue, never the parent or its siblings, so every requirement and literal contract it needs is in its own body. Create each with `gh issue create --parent <parent>`, adding `--blocked-by <n>` when it cannot ship before another sub-issue.

For example, "prompts as a catalog type that agent steps can reference" becomes a parent with three sub-issues: a bug fix on the shared catalog delete path; prompts as a catalog type with `dovo prompt` commands, blocked by the fix; and `prompt_file` on agent steps, blocked by the catalog type.

### 3. Draft

Write each issue in the structure below.

## Issue Structure

Use the sections below in order. Omit a section only when it genuinely does not apply (e.g., no CLI copy for an internal refactor). Never add separate **Acceptance criteria**, **Dependencies**, or issue-list **References** sections.

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

<Fenced code blocks or tables locking DTO/result shapes, status enums, error codes, config keys, paths, or function signatures the implementer must not invent.>

## CLI output expectations

### <scenario>
```text
<exact or near-exact console copy, or omit for non-CLI changes>
```

## Error cases to handle

1. <failure condition → expected status, error code, and remediation behavior>

## Definition of done

- <ONLY checks not already obvious from FR/NFR: cross-FR integration bars, grep bars confirming deleted legacy symbols have zero references in src/ and tests/, or doc gates>
```

## Pre-Publish Checklist

Before running `gh issue create` or `gh issue edit`:
1. Scan the draft for shim-inducing words (`unchanged`, `untouched`, `as before`, `alongside`, `keep`, `compatib`, `shim`, `legacy`). Delete them or convert them into a **Shared surfaces changed** bullet and a grep removal bar in **Definition of done**.
2. Confirm every new model or code path answers "what does this replace?" and mandates deleting the superseded path.
3. Confirm **Definition of done** does not repeat individual FRs line-by-line.
4. Re-run the **Size and split** checks on the finished draft. If one now holds, split before publishing.
5. Scan FRs and **Pre-determined data** for private helper names, loop or call-site lists, and module paths that are not public surface. Delete them (principle 4).
6. Confirm every bug and persisted-format change found in **Ground** appears as a blocker issue or under **Shared surfaces changed**.
7. When publishing a split, create or edit the parent first, then the sub-issues in dependency order so each `--blocked-by` names an issue that exists. Confirm with `gh api repos/{owner}/{repo}/issues/<parent>/sub_issues --jq '.[].number'`.

Drafting applies no `dovo-size-*` or status label; re-grounding does.

## Re-ground mode

`/dovo-issue reground <number>` runs right before work starts on an issue. It checks the issue against what has merged since it was drafted, corrects the body, and leaves the issue with exactly one size label and one status label. Only an issue labelled `dovo-ready` is picked up for implementation. A parent issue gets no labels; re-ground each sub-issue.

### 1. Find the base

The base is the commit the issue was last checked against: the SHA in its latest comment starting with `<!-- dovo-reground -->`, or, when there is none, the last `origin/main` commit before the issue was created:

```bash
git fetch origin main
gh issue view <n> --json body,createdAt,labels,comments
git rev-list -1 --before=<createdAt> origin/main
```

### 2. Re-ground

Read what merged since the base (`git log --oneline <base>..origin/main`, then the diffs that touch the issue's surfaces), and re-run **Ground** against `origin/main`. Check every path, type, error code, config key, CLI copy and other literal in the body against current code, and whether merged work already delivered part of the scope. The code stays read-only as in **Ground**; the only writes are to the issue: its body, its labels and the comment in step 5.

Edit the body with `gh issue edit <n> --body-file …` when anything is stale: fix stale literals, drop scope that has shipped, and add any new bug or persisted-format change. The edited body follows **Core Principles** and passes the **Pre-Publish Checklist**, including the split checks. When it must now be split, split it per **Size and split** and re-ground each sub-issue instead.

### 3. Label size

Remove any other `dovo-size-*` label and apply one:

| Label | When |
|---|---|
| `dovo-size-s` | A few files on one surface, up to about 200 changed lines |
| `dovo-size-m` | Several files or two surfaces, about 200 to 400 changed lines |
| `dovo-size-l` | Several packages, schemas or surfaces, over about 400 changed lines |

`dovo-size-l` fails **Size and split**, so an issue sized `l` is split before it can be `dovo-ready`; label it `dovo-blocked` when the split needs a human decision.

### 4. Label status

Remove any other status label and apply the first that holds:

| Label | When |
|---|---|
| `dovo-blocked` | An issue it is blocked by is open, or the body needs a decision only a human can make. No agent can move it forward. |
| `dovo-investigate` | An agent can make progress alone but not implement yet: a submitted bug whose cause is not confirmed in the code, or an open technical question (reproduce, root-cause, size). The investigation ends by rewriting the issue in the structure above and re-grounding it again. |
| `dovo-ready` | The body matches `origin/main`, has no open question, and passes **Size and split**. |

List the open issues it is blocked by with:

```bash
gh api repos/{owner}/{repo}/issues/<n>/dependencies/blocked_by --jq '.[] | select(.state == "open") | .number'
```

A submitted bug report that is not in the structure above gets `dovo-investigate` and is not rewritten until the investigation confirms its cause.

Set both labels in one call, removing only the labels the issue has: `gh issue edit <n> --add-label dovo-size-m,dovo-ready --remove-label dovo-investigate`.

### 5. Comment

Post one comment recording the check, so the next re-ground and the implementing agent know where it stands:

```markdown
<!-- dovo-reground -->
Re-grounded against `<full origin/main SHA>`.

- **Body changes:** <each correction, or "none">
- **Size:** `dovo-size-<s|m|l>`, <one-line reason>
- **Status:** `dovo-<ready|investigate|blocked>`, <the blocking issue, the decision needed, or the question to investigate; omit the reason for ready>
```
