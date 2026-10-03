<!-- AUTO-GENERATED FROM rules_spec.yaml. DO NOT EDIT DIRECTLY. -->
# Planning Invariants & Specification Rules

> **Notice for Agents:** Plans failing `deliverable_contract` or `validation_check` will be rejected.

## [PLAN-001] Read-Only Planning Discipline
- **Phase:** `Reset & Prereqs`
- **Scope:** `Agent session & filesystem`
- **Requirement:** While planning, the agent is strictly read-only. Never edit src/ or tests/, never run tests or tooling (inv test, pytest, ruff, basedpyright, inv complexity), and never commit, push, or touch PR state. Read-only git and gh commands are the only commands permitted.
- **Deliverable Contract:** No source files edited. The plan document in .agentic/plan.md is the entire deliverable.
- **Validation Check:** Verify git status after planning shows only .agentic/plan.md created.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
# Agent reads files and gh issues, then writes only to .agentic/plan.md
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
# Agent runs `inv test` or edits `src/dovo/cli/app.py` while planning
```

## [PLAN-002] Workspace Reset and State Isolation
- **Phase:** `Reset & Prereqs`
- **Scope:** `.agentic/ directory`
- **Requirement:** Before anything else, clear the previous cycle's artifacts so a stale review, plan, or master plan can never be read as current: `rm -rf .agentic && mkdir -p .agentic`. Use -rf and remove the directory itself; `rm -f` on a directory exits non-zero and short-circuits the chained mkdir, leaving every stale artifact in place while appearing to have reset. Per-file removal is insufficient because it leaves master-plan.md behind.
- **Deliverable Contract:** An empty .agentic/ directory prior to writing the new plan.
- **Validation Check:** Check that .agentic/ contains no plan.md, master-plan.md, review.md, or review.json when a new plan is generated.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
rm -rf .agentic && mkdir -p .agentic
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
rm -f .agentic && mkdir -p .agentic  # fails on a directory, mkdir never runs, nothing is cleared
```

## [PLAN-003] Verbatim Contract Extraction
- **Phase:** `Contract Extraction`
- **Scope:** `## Contract section in .agentic/plan.md`
- **Requirement:** Extract the contract from the issue body without summarizing. Copy every FR-* and NFR-* verbatim with its ID. Paraphrasing requirements is strictly prohibited.
- **Deliverable Contract:** Section '## Contract' containing exact FR/NFR IDs and verbatim requirement text.
- **Validation Check:** Diff issue text against plan contract section. Flag any dropped clause or paraphrase.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
## Contract
- **FR-1**: Add `dovo sandbox prune` command to remove unreferenced sandboxes.
- **FR-2**: Prune operation must delete worktree branches matching `worktree/sandbox-*`.
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
## Contract
We will implement sandbox pruning for stale directories.
```

## [PLAN-004] Normative Pre-Determined Data Preservation
- **Phase:** `Contract Extraction`
- **Scope:** `## Contract section in .agentic/plan.md`
- **Requirement:** Copy Pre-determined data exactly from the issue. Field names, types, defaults, file paths, constants, error codes, and template bodies stated there are normative. You may not invent, rename, or 'improve' them.
- **Deliverable Contract:** Pre-determined data reproduced identically under '## Contract'.
- **Validation Check:** Verify all field names, types, and defaults in code samples match the issue's Pre-determined data.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
### Pre-determined data
- Model: `SandboxPruneResult`
- Field: `pruned_items: list[str] = []`
- Exit code on error: `1`
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
# Plan renames `pruned_items` to `deleted_sandboxes` because it sounds "better"
```

## [PLAN-005] Out-of-Scope Guardrails
- **Phase:** `Contract Extraction`
- **Scope:** `### Out of scope section in .agentic/plan.md`
- **Requirement:** Copy Out of scope verbatim from the issue into the plan's guardrail section. Treat it as a strict stop sign, not a hint.
- **Deliverable Contract:** Subsection '### Out of scope (verbatim from the issue)' containing exact bullet points.
- **Validation Check:** Verify that all items marked out of scope are explicitly listed and omitted from artifact inventory.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
### Out of scope (verbatim from the issue)
- Interactive confirmation prompt before pruning
- Pruning sandboxes on remote git repositories
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
# Omitting Out of scope section or planning an interactive confirmation prompt
```

## [PLAN-006] Greenfield Architecture Default
- **Phase:** `Contract Extraction`
- **Scope:** `Entire plan`
- **Requirement:** Plan zero compatibility shims, aliases, dual code paths, or deprecation windows unless the issue explicitly states a compatibility constraint. Plan to replace superseded paths and update callers in the same change set.
- **Deliverable Contract:** Direct replacements planned; zero shims or legacy fallback adapters in artifact inventory.
- **Validation Check:** Check artifact inventory for compatibility wrappers or fallback flags.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
# Plan directly replaces SandboxService.delete() with unified lifecycle pruner
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
# Plan introduces `LegacySandboxService` and adds deprecation warning shims
```

## [PLAN-007] Tree Grounding and Neighbor Mirroring
- **Phase:** `Grounding & Traps`
- **Scope:** `## Ground truth section in .agentic/plan.md`
- **Requirement:** Never plan from memory: read live domain source and name the closest existing implementation to mirror with exact file:line citations. In the ground-truth table, write at most one clause per cell. Reproduce a mirrored symbol's shape as signature + docstring only, never its full body.
- **Deliverable Contract:** Ground-truth table with columns | Surface | Location | What exists | (one clause per cell), plus explicit 'Pattern to mirror: <path:line>', with any reproduced symbol shown as signature + docstring only.
- **Validation Check:** Verify cited file:line citations exist and match cited functionality; flag any quoted body, helper internals, or file dump beyond a signature and docstring.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
## Ground truth
| Surface | Location | What exists |
| CLI app | `src/dovo/cli/sandbox/app.py:24` | Typer app registering sandbox subcommands |
| Lifecycle | `src/dovo/core/sandbox/services/lifecycle.py:80` | `delete_sandbox` method |

**Pattern to mirror:** `src/dovo/cli/config/commands/config_set.py:30`
```python
def config_set_command(context: CliContext, key: str, value: str) -> ConfigSetResult: """Set a single config key and persist it to the active scope."""
```
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
# Plan pastes the full 40-line body of config_set_command instead of its signature + docstring
```

## [PLAN-008] Trap Identification and Isolation
- **Phase:** `Grounding & Traps`
- **Scope:** `## Current state section in .agentic/plan.md`
- **Requirement:** Explicitly name every trap a lower-context implementer could fall into: dead code, similarly-named-but-unrelated symbols, duplicate implementations, or stale docs. Mark each explicitly out of scope.
- **Deliverable Contract:** Subsection '**Traps (explicitly not touched):**' naming specific symbols and paths.
- **Validation Check:** Verify known hazards (e.g. similarly-named-but-unrelated modules across sibling packages) are trapped when touched.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
**Traps (explicitly not touched):**
- Stale doc claim in `schemas.md` §4 mentioning `renderers.py`: Do not create `renderers.py`.
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
# Plan fails to warn implementer about known naming collision or dead code
```

## [PLAN-009] Complete Artifact Inventory and Deletion Ledger
- **Phase:** `Artifact Inventory`
- **Scope:** `## Artifact inventory section in .agentic/plan.md`
- **Requirement:** Produce an inventory (Artifact | Kind | Path | New or changed | Requirement), one row per touched file, writing 'none' explicitly for every untouched kind. Follow it with a deletion ledger (Path or symbol | Why it goes | Replaced by) covering superseded paths (COMPAT-002), duplicate tests (TEST-004), and dead harness symbols (TEST-013), executed in the same change set — or state 'Deletes nothing: greenfield'.
- **Deliverable Contract:** Markdown inventory table followed by 'Not needed for this issue: <kinds>', then a deletion ledger table or the explicit 'Deletes nothing: greenfield' line.
- **Validation Check:** Check every touched file maps to a requirement, every unused artifact kind is listed under 'Not needed', and the deletion ledger has rows or the explicit greenfield line.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
## Deletion ledger
| Path or symbol | Why it goes | Replaced by |
| `tests/cli/config/test_config_show.py::ConfigShowRootTests` | pass-through handler restating a domain contract | `tests/core/config/test_loader.py` |
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
# Inventory lists 9 new files and says nothing about what they supersede
```

## [PLAN-010] Literal Final Code for Contracts
- **Phase:** `Specification & Samples`
- **Scope:** `### Code section in .agentic/plan.md`
- **Requirement:** Write literal, production-ready code for anything that is a contract: models with model_config, enum definitions, function/method signatures with full type hints and docstrings, Typer argument/option declarations, exact JSON dicts, and error/warning strings.
- **Deliverable Contract:** Fully typed models, signatures, and literal dictionaries in code blocks.
- **Validation Check:** Verify contract code blocks leave no ambiguous types, missing defaults, or unspecified flags.

<!-- ✅ POSITIVE EXAMPLE -->
```python
class SandboxPruneStatus(StrEnum):
    OK = "ok"
    NOTHING_TO_PRUNE = "nothing_to_prune"
    FAILED = "failed"

class SandboxPruneResult(BaseResult):
    model_config = {"extra": "forbid", "strict": True}
    status: SandboxPruneStatus
    pruned_items: list[str] = []
```

<!-- ❌ NEGATIVE EXAMPLE -->
```python
# Plan writes vague sketch:
class SandboxPruneResult:
    # fields for status and pruned items
    pass
```

## [PLAN-011] Signature Plus Intent Docstring for Production Stubs
- **Phase:** `Specification & Samples`
- **Scope:** `### Code section in .agentic/plan.md`
- **Requirement:** Write only a real signature and a one-line docstring stating intent for every planned production function, ending with `raise NotImplementedError`. No numbered steps, no body code — the docstring says what the function does, not how, and adds nothing beyond what the signature and name don't already say.
- **Deliverable Contract:** Signature + one-line intent docstring + `raise NotImplementedError` — no steps, no body.
- **Validation Check:** Flag any production stub with numbered steps, setup comments, or body code beyond the docstring, and any docstring that narrates implementation steps instead of stating intent.

<!-- ✅ POSITIVE EXAMPLE -->
```python
def prune_sandboxes(db: SandboxesRepository, cwd: Path) -> SandboxPruneResult:
    """Delete unreferenced sandbox worktrees and branches under cwd."""
    raise NotImplementedError
```

<!-- ❌ NEGATIVE EXAMPLE -->
```python
def prune_sandboxes(db: SandboxesRepository, cwd: Path) -> SandboxPruneResult:
    """Prune sandboxes.

    1. Query active sandboxes from db
    2. Diff against on-disk worktrees
    3. Delete unreferenced ones
    """
    raise NotImplementedError
```

## [PLAN-018] Test Stubs with Mandatory Tier, Type, and Contract Docstrings
- **Phase:** `Specification & Samples`
- **Scope:** `### Tests section in .agentic/plan.md`
- **Requirement:** Write a signature and a single-line contract docstring for every planned test stub, ending with `raise NotImplementedError`. The docstring must begin with `[<tier>/<type>]` (where tier is tier-1, tier-2, tier-3, or tier-4 per docs/agents/testing.md#the-four-execution-tiers and type is unit or integration), state the public symbol exercised, and state the exact outcome contract asserted. The `### Tests` table acts as a concise summary index.
- **Deliverable Contract:** Signature + single-line docstring with `[<tier>/<type>]` tag and outcome contract + `raise NotImplementedError`.
- **Validation Check:** Flag any test stub missing the tier/type tag (e.g. `[tier-1/unit]`, `[tier-3/integration]`), missing the outcome contract, or containing numbered steps/body code.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
### Tests

| Test | Tier | Outcome |
|---|---|---|
| `SandboxCreateCliIntegrationTests::test_create_cli_capacity_exceeded_exits_one` | Tier 3 (integration) | exit 1; capacity exceeded error |

```python
class SandboxCreateCliIntegrationTests:
    def test_create_cli_capacity_exceeded_exits_one(
        self, cli_runner: CliRunner, sandbox_workspace: Path
    ) -> None:
        """[tier-3/integration] dovo sandbox create: 3 prior sandboxes exist → exit 1, 'Maximum active sandboxes reached' in stdout."""
        raise NotImplementedError
```
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
# Test stub missing tier/type tag and specific contract
def test_create_cli_capacity_exceeded_exits_one(self, cli_runner, sandbox_workspace):
    """Verifies create fails when capacity exceeded."""
    raise NotImplementedError
```

## [PLAN-012] Pre-Decomposed Complexity
- **Phase:** `Specification & Samples`
- **Scope:** `### Code section in .agentic/plan.md`
- **Requirement:** Decompose complex workflows into named helper functions in the plan itself so that every planned function holds cognitive complexity <= 10.
- **Deliverable Contract:** Multiple focused function signatures with single responsibilities rather than one large function.
- **Validation Check:** Verify no single planned function contains more than 3 branching levels or complex nested loops.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
def prune_stale_records(db: SandboxesRepository) -> list[str]: ...
def prune_orphaned_directories(sandboxes_dir: Path) -> list[str]: ...
def prune_sandboxes(...) -> SandboxPruneResult: ...
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
# Single 80-line function planned to do querying, directory diffing, git branch deleting, and error handling
```

## [PLAN-014] Flagging Unspecified Decisions with 🚨
- **Phase:** `Cross-Cutting & Validation`
- **Scope:** `### Decisions & ## Cross-cutting in .agentic/plan.md`
- **Requirement:** When an issue leaves a detail genuinely unspecified, do not stall or invent product behavior: choose the option consistent with nearest neighbor patterns, record the choice and rejected alternative, and append 🚨 so human reviewers catch it.
- **Deliverable Contract:** Decision bullet: - **<point>:** <choice>, because <reason>. Rejected: <alt>. 🚨.
- **Validation Check:** Verify all unspecified assumptions are flagged with 🚨 in the plan and restated in the agent handoff message.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
- **Prune branch deletion policy:** Delete local branches matching `worktree/sandbox-*` automatically, because active sandboxes track branches 1:1. Rejected: prompt user for each branch. 🚨
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
# Silent design choice made without recording alternative or flagging with 🚨
```

## [PLAN-015] Plan Target Path and Human Handoff Protocol
- **Phase:** `Self-Check & Handoff`
- **Scope:** `Deliverable file & Chat response`
- **Requirement:** Write the plan to .agentic/plan.md. Run the 9-item self-check before handing off. In chat, report the path, a one-paragraph summary, all 🚨 decisions and open questions, and state plainly that this was planning only: nothing was implemented, tested, committed, or pushed. Stop and wait for human review.
- **Deliverable Contract:** File written to .agentic/plan.md. Agent turn terminates without executing implementation commands.
- **Validation Check:** Ensure the agent does not immediately proceed to invoke /dovo-code or edit production files.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
"Plan saved to .agentic/plan.md. Planning only; nothing was implemented or committed.
Open decisions requiring confirmation:
- 🚨 Prune branch deletion policy: auto-delete worktree/sandbox-* branches."
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
"Plan written. Now I will start writing the code..."
```

## [PLAN-016] Pre-Handoff Checklist Compliance Sweep
- **Phase:** `Pre-Handoff Self-Audit`
- **Scope:** `Entire .agentic/plan.md, checked before saving`
- **Requirement:** Before finalizing .agentic/plan.md, sweep docs/agents/REVIEW_CHECKLIST.json for every rule matching the touched paths or domains and verify the plan itself satisfies every applicable BLOCKER clause — reread the actual planned imports, paths, and stubs against each clause, not a restatement of the rule. Fix any violation in the plan before saving. This is a save gate, not a written report: no compliance table belongs in .agentic/plan.md.
- **Deliverable Contract:** Zero BLOCKER-severity violations in the saved plan. No Rule Evaluation Matrix or other compliance table in .agentic/plan.md — the plan's own content is the evidence.
- **Validation Check:** Re-derive which REVIEW_CHECKLIST.json rules match the touched paths and spot-check the saved plan against each BLOCKER clause directly (e.g. reread Phase 2 imports for ARCH-001, reread ledger paths for TEST-002). Flag anything the agent could only justify by restating the rule rather than pointing at the plan's own text.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
Before saving, reread the Phase 2 service stub and confirmed it imports only from common/ and core/sandbox/ (ARCH-001), and reread the ledger path against the TEST-002 mapping — no BLOCKER violated, plan saved with no compliance table.
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
# Plan asserts "Complies with ARCH-001, TEST-002" without rereading the actual planned imports or paths
```

## [PLAN-017] Test Ticket Scope Contract
- **Phase:** `Test Strategy`
- **Scope:** `Any issue whose deliverable is test files`
- **Requirement:** A ticket delivering tests must declare, before implementation, the exact test file paths per the TEST-002 mapping, a line budget of 100–250 with a split plan if exceeded, and the rule IDs it's graded against. A list of test method names without paths is not a specification.
- **Deliverable Contract:** In scope lists one bullet per test file (path + estimated lines + contracts asserted); Definition of Done cites the graded rule IDs and names what's deliberately not covered here.
- **Validation Check:** Every declared path resolves under the TEST-002 mapping for its source module, and the summed line estimate stays within budget or carries a split plan.

<!-- ✅ POSITIVE EXAMPLE -->
```markdown
### In scope
- `tests/core/bootstrap/test_initialize.py` (~120 lines): preflight failure modes,
  zero-side-effect abort, idempotent rerun
- `tests/cli/init/test_init.py` (~90 lines): runner exit codes, `--format json` payload, `--force`

### Rules
TEST-002, TEST-004, TEST-017
```

<!-- ❌ NEGATIVE EXAMPLE -->
```markdown
### In scope
- Create tests for init covering the happy path and some failure cases
- Add CLI tests for every action with RootTests and CliIntegrationTests
```
