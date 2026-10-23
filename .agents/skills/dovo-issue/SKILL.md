---
name: dovo-issue
description: >-
  Create or update an implementation-ready, self-contained GitHub issue for Dovo
  CLI with authoritative scope, numbered FR/NFR contracts, pre-determined data,
  error cases, and a non-redundant Definition of Done. Grounds the issue in the
  code first and splits work that mixes more than one responsibility into
  a parent issue with native sub-issues. Invoked as /dovo-issue.
  Use when asked to create, draft, or revise a GitHub issue.
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
- **Call sites** when the work applies one pattern across many places (exception handlers, commands, callers of a replaced function): count them per package, for example with `git grep -c`, and read the matches to drop comments, docs and tests from the count. The count shows which packages the work reaches.

Only read; never edit code or run tooling that mutates or validates. Read-only `git`, read-only `gh`, `rg` and file reads are enough.

### 2. Size and split

An issue is one kind of change that a reviewer can describe in one sentence without "and". It lands as one PR. Review effort comes from how many kinds of change a PR mixes, not from its length: a focused 1,000-line conversion takes longer to read than a 400-line one, but it is not harder.

| Kind | Changes | Never also |
|---|---|---|
| Fix | One wrong behaviour, or a few small ones in one area, each with its own regression test | Refactors around it |
| Foundation | A new shared type, tool or rule that two or more issues adopt | Converts callers |
| Adopt | One package, or one command's chain of steps, moves onto a foundation and deletes what it replaces | Changes what users see beyond what the move requires |
| Refactor | Structure only, worth merging on its own; behaviour is identical | Adds surface |
| Remove | Deletes what nothing uses any more | Anything else |
| Feature | New user-facing surface | Restructures what it builds on |

Three rules decide a split:
- **Don't mix kinds.** A foundation and its adoption, or a behaviour change and a refactor, are separate issues. So are outcomes for different users (someone running a blueprint and someone running `dovo doctor`), two new public surfaces (a command group and a schema field), and a persisted-format change with new CLI surface.
- **Don't split by size.** Line count, file count and layer are never reasons to split.
- **Don't split if the halves need a bridge.** When one half would have to map the new shape back onto the old one so the other half still works, keep them as one issue.

Two kinds fold into another issue instead of standing alone. A refactor that only prepares one issue is that issue's first plan phase. A foundation with one adopter goes into the adopter's issue.

When one foundation is adopted across several packages, the split is the foundation, then one Adopt issue per package or per command's chain of steps, then any Remove issue. A shared function that other commands also call is converted with its own package, not inside one command's chain.

Label every issue except a split's parent `dovo-size-s`, `dovo-size-m` or `dovo-size-l` for how long it takes to review: one area and a few files, a couple of areas, or many. An area is a `src/dovo/` package, a persisted format, a command group with its docs page, a skill, the catalog blueprints and steps, or `scripts/`. When in doubt, pick the larger label. An `l` issue names its one responsibility in its opening paragraph.

Every split has a parent issue and native GitHub sub-issues:

- **Parent:** the Goal / User Story, one short paragraph on why the children are ordered as they are, and an **Out of scope** list for the whole feature. No FRs, data, error cases or Definition of Done: GitHub tracks progress through the sub-issues. When splitting an existing issue, edit it into the parent (`gh issue edit <n> --body-file …`) so its number, links and discussion survive.
- **Sub-issues:** full issues in the structure below, each self-contained. The planner reads only the sub-issue, never the parent or its siblings, so every requirement and literal contract it needs is in its own body. Create each with `gh issue create --parent <parent> --label dovo-size-<s|m|l>`, adding `--blocked-by <n>` when it cannot ship before another sub-issue. A parent gets no size label.

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
4. Re-check the finished draft against **Size and split**: it is one kind from the table, it breaks none of the three rules, it carries a `dovo-size-*` label, and, if it is `l`, its opening paragraph names its one responsibility.
5. Scan FRs and **Pre-determined data** for private helper names, loop or call-site lists, and module paths that are not public surface. Delete them (principle 4).
6. Confirm every bug and persisted-format change found in **Ground** appears as a blocker issue or under **Shared surfaces changed**.
7. When publishing a split, create or edit the parent first, then the sub-issues in dependency order so each `--blocked-by` names an issue that exists. Confirm with `gh api repos/{owner}/{repo}/issues/<parent>/sub_issues --jq '.[].number'`.
