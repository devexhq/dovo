# GitHub Issue Creation and Updates

Use this guide when opening or revising GitHub issues for Dovo CLI.
Canonical example of structure, depth, and tone:
[docs/agents/examples/github-issue.md](examples/github-issue.md).

Issues should be **implementation-ready** and **authoritative**: an agent or
human can implement from the issue alone without guessing product intent,
schemas, CLI wording, or which bullets are "nice to have."

## Authoritative issues (no soft decisions)

- Everything in the issue body is **required** for that issue unless the section
  is **Out of scope** (explicit non-goals for *this* issue only).
- Do **not** mark work as optional, stretch, nice-to-have, "if implemented,"
  "recommended," "consider," or "v1 maybe."
- Do **not** present unresolved product forks ("A or B", "either X or Y").
  Decide before writing; put the decision in Scope, FR/NFR, or Pre-determined
  data.
- If scope is too large, **split into another issue** rather than parking half
  the work as optional inside one body.
- Prefer decisive verbs: *must*, *shall*, *always*, *never*. Avoid hedging that
  leaves the implementer to choose.

## Greenfield until specified otherwise

This project is **greenfield** by default:

- Do **not** require backward compatibility with prior APIs, CLI flags, file
  layouts, error strings, or on-disk formats unless the issue **explicitly**
  states a compatibility constraint.
- Prefer the cleanest correct design for the stated FR/NFR. It is acceptable
  (and expected) to replace or delete superseded code paths when the issue's
  behavior is the new source of truth.
- Do not add shims, dual code paths, deprecation windows, or "keep old callers
  working" NFRs unless compatibility is an explicit requirement in that issue.
- When an issue changes a shared surface, update callers and tests in the same
  change set so the tree matches the issue—not a transitional hybrid.

## Self-contained issues (limit cross-issue links)

Issues are implemented by agents that pay per token. **Do not** turn the body
into a reading list of other GitHub issues.

### When writing or editing issues

- Prefer **in-repo paths** (`docs/cli-plan.md`, `docs/agents/*.md`, schema files)
  over GitHub issue numbers when pointing at shared contracts.
- **Do not** list sibling issues as prerequisites, "related reading," or
  out-of-scope definitions ("see #9–#20").
- Link another GitHub issue **only when strictly necessary and high value**, for
  example:
  - this issue **supersedes** or **duplicates** a specific issue and the number
    is required for triage
  - a hard blocker that cannot be stated as a capability without the number
  - a human-only tracking note that does not affect implementation
- Default is **zero** issue links in the body. One is rare; a range or laundry
  list is almost always wrong.
- Never use issue links as a substitute for stating requirements or non-goals
  in full in *this* body.

### Out of scope must be self-contained

**Out of scope** is a stop sign for implementers, not a pointer to other
tickets. Write **capability bullets** a reader understands without opening
anything else.

Good:

```markdown
### Out of scope
- CLI subcommands (`dovo config show|set|unset|validate`)
- Terminal rendering of effective config or source metadata
- Mutating config on disk (set/unset/atomic write)
- Exit-code policy for `dovo config validate`
- Config generation / repair (`dovo init`)
```

Bad:

```markdown
### Out of scope
- Everything in #9–#20 (read those issues first)
- Downstream consumers — see #5, #6, and the status epic
```

### When implementing from an issue

- Treat the opened issue body (plus in-repo docs it cites) as sufficient.
- **Do not** fetch, browse, or bulk-load other GitHub issues unless the user
  explicitly asks or a single cited issue is strictly required to complete the
  task and its contract is not already stated in the current issue.
- Do not expand scope by reading sibling tickets "for context."

Cross-issue coordination belongs in project boards, milestones, or human
process—not in the implementer's required reading list.

## When to create vs update

- **Create** a new issue for a distinct feature, behavior change, or bug with
  its own acceptance boundary.
- **Update** an existing issue when scope, requirements, predetermined data,
  CLI copy, or Definition of Done change—edit the body in place rather than
  scattering decisions across comments only.
- Prefer one focused issue over a grab-bag. Split large work into linked issues
  when FR/NFR would otherwise mix unrelated outcomes. Splitting for humans does
  **not** mean each issue body must link the whole set.

## Title

- Imperative or outcome-focused, concise (e.g. `Seed starter workflow definitions`).
- No ticket-ID prefixes or noisy tags in the title.

## Body structure

Use the sections below in order. Omit a section only when it truly does not
apply (e.g. no CLI for a pure library change); do not omit Scope or Definition
of Done for feature work.

Do **not** add a separate **Acceptance criteria** section. Fold close-out checks
into **Definition of Done** (see §7).

Do **not** add a Dependencies section (upstream/downstream lists).

Do **not** add a References section that is mostly other GitHub issues. If you
need a pointer, prefer one in-repo doc path. Omit References entirely when the
issue is already self-contained.

### 1. Goal / User Story

Short narrative of **why** and **who benefits**. Prefer 2–4 sentences:

- User-facing outcome when the work ships
- Friction removed or capability unlocked
- Constraints that must hold (e.g. idempotent, non-destructive)

One-liner form when useful:

```markdown
**As a** <user>,
**I want** <capability>,
**so that** <benefit>.
```

### 2. Scope

Explicit boundaries. **In scope** is the full contract for this issue.
**Out of scope** lists work that belongs elsewhere or is intentionally not part
of this change—not "later if we have time."

```markdown
### In scope
- ...

### Out of scope
- ...
```

Every in-scope bullet is mandatory. If something is not ready to mandate, leave
it out of the issue (or open a separate issue when the decision is made).

**Out of scope** rules:

- Name **capabilities and surfaces**, not issue numbers.
- Must be understandable **without** opening other GitHub issues.
- Do not define non-goals as "see #N" or "covered by the config epic."
- Keep it short; it prevents over-building, not multi-issue research.

### 3. Functional / Non-Functional Requirements

Number requirements so discussion and PRs can reference them (`FR-1`, `NFR-2`).

FR/NFR are the **behavior contract**. Implement from these (plus Pre-determined
data). Do not restate each FR as a checklist item later.

**Functional (FR-*)** — observable behaviors, branching, and outcomes:

- Happy path
- Idempotent / rerun behavior
- Partial success
- Validation gates
- Flag-controlled behavior

Each FR should state **condition → action → result** (and structured errors
where relevant). Write FRs so they are already testable; that removes the need
for a parallel acceptance-criteria list.

**Non-functional (NFR-*)** — quality attributes, for example:

- Idempotency and non-destructive defaults
- Deterministic file names and content
- Atomic writes / durability
- Performance or size limits (when they matter)
- Security or trust boundaries (when they matter)

Do **not** add compatibility/stability NFRs by default. Only include them when
the issue explicitly owns a frozen contract (e.g. a shipped schema version that
must keep reading existing user files—and say so plainly).

### 4. Pre-determined data

Lock values the implementer must not invent. Include only what this issue
owns; link to schemas/docs for shared contracts **in-repo** when needed.

Typical contents:

| Kind | Examples |
|------|----------|
| DTO / result shapes | field names, types, lists vs scalars |
| Config keys / defaults | JSON paths, default values |
| Templates | full YAML/JSON bodies or paths to package templates |
| Constants | filenames, workflow `name` values, error codes |
| Paths | `.dovo/workflows/fix-tests.yml`, etc. |
| APIs | function signatures, helper responsibilities |

Use fenced code blocks for schemas, templates, and APIs. Keep template content
valid against the relevant schema version when the issue claims validity.
API shapes in the issue are normative for the implementation unless the issue
is updated.

### 5. CLI Output Expectations

Show **exact or near-exact** user-visible copy for important outcomes:

- Fresh / success
- Already present / no-op
- Partial success
- Validation or hard failure (include a short "Fix:" hint when useful)

Prefer bullet trees that match existing `dovo` console style. If output is
structured data only (no new copy), say so and describe the fields rendered.

### 6. Error Cases to Handle

Numbered list of failure modes, not only the happy path. Include:

- Missing or unwritable paths
- Type/path collisions (file vs directory)
- Schema or validation failures
- Permission / atomic-write failures
- Concurrency or reruns when relevant
- Invalid user input or conflicting flags

For each case, the issue should make the expected product behavior obvious
(abort, skip, partial continue, error code/message).

If every failure mode is already fully specified in FR-*, this section may be
omitted rather than duplicated.

### 7. Definition of Done

Single close-out gate for the issue. **Do not** add a separate Acceptance
criteria section.

**FR/NFR are assumed.** Definition of Done must **not** restate requirements
that a competent implementer can already verify from FR/NFR, Pre-determined
data, CLI copy, or Error cases (e.g. do not list "missing file returns
`not_found`" if FR-3 already says that).

Include **only** checks that are **not clearly inferrable** from those
sections, such as:

- End-to-end or integration outcomes that span multiple FRs
- Coverage completeness bars ("every `ConfigLoadStatus` has a test")
- Greenfield cutover ("callers use the new API; superseded paths removed")
- Docs or command wiring the issue owns
- Explicit non-ships that are easy to miss ("this change does not add `dovo config
  show`")
- Cross-cutting ship bars (templates validate, tests in the suite pass)

Keep the list short (aim for a handful of bullets). Prefer verifiable
statements over process fluff ("LGTM", "code reviewed").

Omit vague bars ("works well", "good UX") unless paired with a concrete check.

Do not require backward compatibility unless that constraint is an explicit
in-scope product requirement.

Do **not** use Definition of Done to dump related issue numbers. State
non-ships as capabilities ("no CLI subcommand") instead of "leave #9–#20 to
others."

Example shape:

```markdown
## Definition of done

- After `dovo init`, `load_config` succeeds on the generated file
- Tests cover every `ConfigLoadStatus` value
- In-tree callers use `load_config`; old dual load paths are removed
- `docs/agents/schemas.md` documents the load API and error codes
- No `dovo config` CLI subcommand is added in this change
```

## Extra sections (when they add clarity)

Add after the core sections only if useful—do not pad:

| Section | Use when |
|---------|----------|
| **Test plan** | Non-obvious unit/integration cases worth listing (how to test, not a second done list) |
| **Write / rollout strategy** | Atomic writes, cutover steps the implementer must follow |
| **API** | Multi-module design needs an agreed surface (normative) |
| **References** | Rare. Prefer a single in-repo doc/schema path. Omit by default. |

Do **not** add **Dependencies** (upstream/downstream prerequisite lists).
Do **not** add **Acceptance criteria** (merged into Definition of Done).
Do **not** add **References** that are mostly other GitHub issues.

## Tone and style

Match [docs/agents/examples/github-issue.md](examples/github-issue.md) for
depth and directness (structure may differ where this guide says so):

- Direct, specification-like; minimal marketing language
- Short bullets over long prose
- Concrete paths, command names (`dovo init`), and schema versions
- Requirements as behaviors, not implementation micromanagement—unless
  predetermined data or safety (atomic write, non-destructive) requires it
- Authoritative voice: the issue is the contract, not a menu of choices
- Self-contained: implementable without a tour of the issue tracker

## Updates and PR linkage

- When implementation discovers a decision change, **update the issue body**
  (scope, FR/NFR, templates, Definition of Done) so the issue stays the
  source of truth.
- Reference requirement IDs in PR descriptions when helpful (`Implements FR-2`).
- PR text may cite `Fixes #N` / `Closes #N` for the issue being completed; that
  is not a license to load unrelated issues into the agent context.
- Close the issue only when Definition of Done is met (or explicitly waived in
  the issue with rationale). Meeting every FR/NFR is necessary; Definition of
  Done is the explicit close-out checklist on top of that contract.

## Minimal skeleton

```markdown
## Goal / User Story

<why, who, outcome, key constraints>

## Scope

### In scope
- ...

### Out of scope
- <capabilities not issue numbers; self-contained stop list>

## Functional requirements

### FR-1: <name>
...

## Non-functional requirements

### NFR-1: <name>
...

## Pre-determined data

<DTOs, config, templates, constants, paths, APIs>

## CLI output expectations

### <scenario>
```
...
```

## Error cases to handle

1. ...

## Definition of done

- <only checks not clearly inferrable from FR/NFR and sections above>
```
