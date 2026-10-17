---
name: dovo-issue
description: >-
  Create or update an implementation-ready, self-contained GitHub issue for Dovo
  CLI with authoritative scope, numbered FR/NFR contracts, pre-determined data,
  error cases, and a non-redundant Definition of Done. Invoked as /dovo-issue.
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
   - If scope is too large, split into another issue rather than parking optional work inside one body.

2. **Greenfield cutover (no shims):**
   - Do not require backward compatibility with internal APIs, error strings, or internal models unless explicitly mandated for a public contract (CLI flags, `config.json`, blueprint YAML, JSON wire envelope).
   - When touching an existing shared surface, list it under **Shared surfaces changed** and require callers, tests, and docs to be updated in the same change (R10).
   - Never freeze existing internals with phrases like "X stays unchanged", "untouched", "alongside", or "keep old callers working."

3. **Self-contained (zero issue-tracker tours):**
   - Prefer **in-repo paths** (`AGENTS.md`, `src/dovo/...`, `schemas/v1/...`) over GitHub issue numbers.
   - Default to **zero** issue links in the body. Only link another issue when it supersedes/duplicates a ticket or names a hard blocker.
   - Write **Out of scope** as self-contained capability bullets (e.g., `- CLI subcommands (dovo config show|set)`), never `- Everything in #9–#20`.

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
- <omit if none; otherwise name each renamed/modified type, field, or file and its new shape>

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
