# Planning Before Implementation

Read this **before writing any code** for a GitHub issue. The plan document (`.agentic/plan.md`) is the deliverable.
While planning, remain **read-only**: do not edit `src/` or `tests/`, do not run tests or quality gates, and do not commit or push.

A plan has two readers. The human approving it needs to see, at the top, what changes for existing users and what is still undecided. The agent implementing a phase needs the files and symbols that phase touches, the new public surface the issue does not already pin, and the tests that pin each requirement. Write for both, and repeat nothing the issue already says.

## How an issue reaches a plan

An issue is written in the short format of [templates/issue.md](../../.dovo/catalog/prompts/sdlc/v2/templates/issue.md), or the bug format of [templates/bug.md](../../.dovo/catalog/prompts/sdlc/v2/templates/bug.md) (`/dovo-issue` drafts both). It records what a person decided: numbered outcomes (`O-n`) and decisions (`D-n`), with binding values for the literals a person chose. `dovo run issue-plan-v2 -i <n>` readies the issue, labels it `dovo:ready` and slices it into a plan a person approves, kept in the issue's work plan comment, the only comment the flow posts on the issue. `dovo run issue-code-v2 -i <n>` then builds the next slice: it phases the slice by this guide, codes it, reviews it and opens its pull request. A plan of one slice is phased in the `issue-plan-v2` run itself. Each slice of a multi-slice issue gets its own sub-issue when it starts, ready from the approved plan.

`/dovo-plan` follows the same steps for work outside that flow.

## Step 1: Extract the contract from the issue

```bash
gh issue view <number> --json number,title,body
```

- Link the issue; do not copy its prose. Every outcome (`O-n`) and decision (`D-n`) appears by ID in the FRs column of the `## Phases` table instead, and is cited by at least one test line. A requirement whose parts land in different phases is listed in each of those phases' FRs cells with the part it covers in parentheses (`O-7 (reply)`, `O-7 (fixer)`), and each part is cited by at least one test line.
- Do not copy the issue's **literal** contracts (its binding values: names, flags, JSON, and exact error and output strings) into the plan: cite the issue section that holds them (`#<n>, Binding values`). The implementing agent reads the issue alongside the plan. A phase's `### Code` holds only new public surface the issue does not pin literally (Step 4).
- Copy each **Out of scope** item that names the issue owning it into `## Owned elsewhere` as a collision check (`#n owns <X>`): the plan does not do that work. It is not a ban, so a line the change would otherwise leave false is still fixed (R17). Never derive a `## Traps` bullet from the issue's scope.
- The issue's observable contracts bind the plan: CLI behaviour and output, exit codes, persisted formats, entrypoint signatures, result models and the literal values tests assert. The issue names no internal shapes; the plan chooses them in Step 2. When grounding shows that an outcome or decision cannot hold as written, plan the better design: add an Open Question whose assumed answer is that design, cite the `path:line` that shows why, and name the departure under **What you're approving**. Never write a Trap that tells the implementer to work around the issue.
- The issue's outcomes and decisions bind the plan as written. When following one literally would exclude behaviour of a foundation the change adopts (an `OSError` on Dovo's own state that `catch_failure` records, and the `diagnostics` entry that follows; see [architecture/exceptions.md](architecture/exceptions.md) and [architecture/diagnostics.md](architecture/diagnostics.md)), or would freeze wording nothing relies on, plan the better behaviour as a departure in the same way.
- Absent an issue, quote the request in the opening paragraph and number the requirements you derive from it (`FR-1`, …) in a `## Requirements` list after `## What you're approving`.
- Do not open or consult sibling or parent issues. An issue that needs more than one pull request is sliced by `issue-plan-v2`, never planned whole with `/dovo-plan`. Any other parent issue (one with sub-issues) is never planned itself; plan its sub-issues one at a time.
- Plan zero backwards-compatibility shims, aliases, or dual code paths unless the issue explicitly demands them (R10).
- If a detail is unspecified, choose the option matching the nearest existing pattern from Step 2, and add a numbered Open Question stating the assumed answer and the rejected alternative.

## Step 2: Ground yourself in the current code

1. Read [AGENTS.md](../../AGENTS.md) (rules R1–R18 and exemplars).
2. Ground against the current default branch and record its short sha and the date. Never ground against a branch whose base is not an ancestor of it.
3. Read every function the change touches **and its callers**, and the mirrored tests. Name the closest existing implementation to mirror with `file:line` citations (signature + docstring only).
4. Look for what the change alters or depends on beyond the issue's text, and record each finding in the plan:
   - existing behaviour that changes for current users, and existing bugs on the path the change builds on → `## What you're approving`
   - persisted formats (session state and manifests, `data.db`, `config.json`, catalog `index.json`, blueprint and step YAML) and whether data written before the change still loads → `## What you're approving`
   - exhaustive iterations over an enum or type list, field-by-field merge or override logic, existing lookalike symbols, stale docs → `## Traps`
   - a lookalike or bypass the issue's own shape would introduce → a departure (Step 1), not a Trap
5. When the citations behind the plan are too many to keep inline, put a ground-truth table (`Surface`, `Location`, `What exists`) in `.agentic/evidence.md` and link it from the plan's opening paragraph. Every link in the plan must resolve to a file about this issue.

## Step 3: Enumerate every artifact and split into phases

Write the file-by-file manifest into `## Artifact inventory` in the plan itself: every file and symbol each phase touches, with its phase. A reviewer reads it to see what the change touches, and the implementer reads it as the phase's work list.
Group requirements into phases of one concern each (~400 changed lines or less, tests included):
- Each phase passes `uv run dovo run quality` on its own, ships something and leaves no dead code.
- Replacing an internal is phased as add, switch callers, delete, with the deletion phase planned up front in `### Deletion ledger` (R10).
- Order phases by dependency, lower layers first (R1). A fix to existing behaviour that the change depends on is its own phase, first.
- A phase over budget states, under the Phases table, why it cannot be split.
- `/dovo-code` lands every phase in one PR, so the phases together serve the issue's one responsibility. A plan whose phases mix kinds of change (a foundation and its adoption, or a behaviour change and a refactor) says so first under **What you're approving** and recommends splitting the issue with `/dovo-issue`.
- Resolve every check now: no plan line or ledger entry may be conditional on a `grep` the implementer runs later.

## Step 4: Write `.agentic/plan.md`

- **Code:** a phase's `### Code` holds only new public surface the issue does not already pin literally: a new public signature with type hints, a result model, an enum, a command's flag set. No stubs for private helpers, and no copy of a literal the issue's Binding values already hold; cite that issue section instead. A phase with no such surface says "None: the issue pins it (`#<n>, <section>`)".
- **Tests:** one line per test, `TestClass.test_name (path) — O-n: <outcome>`, citing every requirement it pins. No bodies and no `raise NotImplementedError` stubs. Sibling cases are one parametrized test and stay one line.
- **Commands:** every command a phase adds or changes gets the R13 matrix (success, a failure path, `--format json`, each option it binds) as test lines in that phase's `### Tests`.
- **Decisions:** a phase's `### Decisions` lists only where the plan departs from the issue or from the nearest existing pattern; omit the heading when there is none.
- **Numbers:** a line estimate or total appears once, or agrees everywhere it appears.
- Keep every planned function under cognitive complexity $\le 10$.

### Plan document template (`.agentic/plan.md`)

````markdown
# Issue #<n>: <title>

Planning only. Grounded against `<default branch>` at `<short sha>` (<YYYY-MM-DD>).

The issue's contract is not repeated here; read it on #<n>. This plan covers how each requirement lands, in which phase, and how it is tested. The [Phases](#phases) table maps every requirement to its phase, and each test line cites the requirements it pins. [<Citations: `.agentic/evidence.md`>]

## What you're approving

**Open questions:**

> **OQ-1: <one-line question>**
> - **Assumed:** <answer>. Reply "<word>" for <the other answer>.
> - **Why:** <one fact per bullet, each with its `path:line`>
> - **If changed:** <what the other answer changes, and in which phases>
> - **Challenged (C<n>):** <the challenge's alternative>, because:
>   - <one bullet per piece of the challenge's evidence and failure, each with its `path:line`>
> - **Turns on:** <the one fact or preference the choice depends on>
> - **Recommended:** <assumed | challenged | none>, because <one clause>. Strongest point for the other side: <one clause>.
> - **Note:** <an issue sentence the choice reverses, a follow-up it needs, or the test that pins it>

<Or "None.">

Write one fact per bullet. **Challenged**, **Turns on** and **Recommended** appear only on a question a challenge or a conflicting review finding raised, and are added when the findings are folded in (see `fold-findings` in `.dovo/catalog/blueprints/feature-dev.yml`). **Note** appears only when there is one.

**Changes to existing behaviour:**

1. **<what changes for whom> (phase <p>).** <What happens today, with `path:line`, and what happens after.>

<Or "None: new surface only.">

---

## Traps

- **<trap>** (`path:line`). <What goes wrong, and what to do instead.>
- **<edge case>** (phase <p>, `path:line`). <A genuine implementation gotcha, and how to handle it.>

## Owned elsewhere

- **#<n> owns <X>** (from the issue). <What in this change sits next to it, or "no overlap">.

<Or "None.">

---

## Phases

| Phase | Ships | FRs | Depends on | Est. changed lines | Risk, and where to look |
|---|---|---|---|---|---|
| 1 | <what a reviewer can check after this phase> | O-1, O-2, D-1 | none | <n> | **Low/Med/High.** <what to check> |

<Why an over-budget phase cannot be split; which phases are independent.>

### Deletion ledger

<Table of `Phase | Deletes`, or "Deletes nothing: greenfield.">

---

## Artifact inventory

| Artifact | Path | New or changed | Phase |
|---|---|---|---|
| `<symbol>` | `<path:line>` | new / changed: <the change, in one clause> | <p> |

Not needed: <surfaces checked and left alone, e.g. migration, result model, config key>.

---

## Phase <p>: <name>

### Code

<new public surface the issue does not pin literally: signature, result model, enum, flag set. Or "None: the issue pins it (`#<n>, Binding values`)".>

### Decisions

- **<departure from the issue or the nearest pattern>:** <choice>, because <one clause>. Rejected: <alternative>. [See OQ-<n>.]

### Tests

- `<TestClass>.<test_name>` (`<test file path>`) — O-<n>[, D-<m>]: <exact outcome>.
- `<TestClass>.<test_name>[<case>, <case>]` (`<test file path>`) — O-<n>: <exact outcome, per case>.

<For commands, the R13 matrix as test lines: success, a failure path, `--format json`, each option it binds.>

---

## Cross-cutting

- **Docs gates that fire:** <specific docs per `docs/agents/documentation.md`, and the phase each lands in, or "none">
- **Validation:** `uv run dovo run tests --path <tests>`, then `uv run dovo run quality`
````

## Step 5: Save and hand off

1. Verify all planned paths, imports, public surface and phases against R1–R18 in [AGENTS.md](../../AGENTS.md). Then split every outcome and decision into its separate obligations, each sentence a test could observe, and check that each obligation lands in a phase's FRs cell and is cited by at least one test line.
2. Write `.agentic/plan.md` (and `.agentic/evidence.md` when used) and its open questions to `.agentic/plan.json` (schema in the `dovo-plan` skill), deleting any stale `.agentic/review*`, `.agentic/plan-review*`, `.agentic/plan-challenge*` and `.agentic/plan-walkthrough*`.
3. Report the plan path, the Phases table, and every numbered Open Question.

## In the pull request

The plan lives in uncommitted `.agentic/`, so the PR carries what a later reader needs to check it.

- **List the plan's phases in the PR description**, one line per phase with what it ships.
- **A PR that departs from its ticket updates or comments on the ticket in the same change.** Name each departure (an outcome or decision not followed as written, or work an **Out of scope** item gives another issue) in the PR description, and edit the ticket or comment on it so its text matches what ships. Never close a ticket whose text contradicts the code.
