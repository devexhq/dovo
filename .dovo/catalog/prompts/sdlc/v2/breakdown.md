# breakdown

Break the approved slice plan into one phase file per phase. The code loop gives each phase's agent only its own file, so a phase file holds everything that agent needs and nothing another phase needs.

Inputs: `.agentic/plan.md`, `.agentic/plan.json` and `.agentic/requirements.json`, the slice's numbered items of the issue it is worked on, with their exact text. Output: `.agentic/phases/phase-<n>.json`, one per phase number in `plan.json`'s `slices[].phases` for the slice. The next step, `plan.py check-breakdown`, refuses the files when they drop or misquote anything and then writes `.agentic/phases/index.json` itself.

## Hard boundaries

- **Never edit `.agentic/plan.md`, `.agentic/plan.json` or `.agentic/requirements.json`**, the issue, `src/`, `tests/` or `docs/`, and never commit or push. Write only `.agentic/phases/phase-<n>.json`.
- **Add nothing the plan does not say.** Every action, trap and decision comes from the plan as approved. When the plan is silent on something a phase needs, leave it out and say so in your report; never fill the gap with a design of your own.
- **No sketches.** A phase file has no `sketch` or `data` field. It says how to do the work in prose, in `intent` and each action's `do`. The only code it holds is `binding`, an exact copy of the code blocks under the phase's `### Code` in `plan.md`, which the plan makes binding.
- **Copied fields are copies.** `binding`, `departures`, `tests` and `owned_elsewhere` copy the plan's text; the check refuses any edit, cut or addition and shows the text it expected.

## 1. Start clean

If `.agentic/check-breakdown.log` exists, an earlier breakdown failed the check: read it, then fix what it names in the files you write below.

```bash
mkdir -p .agentic/phases && find .agentic/phases -name 'phase-*.json' -delete && rm -f .agentic/phases/index.json
```

## 2. Read the plan

Read `.agentic/plan.md` in full, `plan.json` (its `slices` entry for the slice and its `decisions`) and `requirements.json`.

## 3. Write one file per phase

For each phase number `n` of the slice, write `.agentic/phases/phase-<n>.json` from the plan's `## Phase <n>` section and the plan-wide sections that touch it (Traps, the Phases table and its deletion ledger, the Artifact inventory, cross-cutting doc updates):

```json
{
  "schema_version": 1,
  "slice": "S3",
  "phase": 1,
  "title": "Hide discarded sessions",
  "depends_on": [],
  "intent": "Why this phase exists and how its parts fit together, in markdown.",
  "requirements": [{ "id": "O-1", "text": "exact text from requirements.json" }],
  "decisions": ["D-4"],
  "traps": ["The JSON output must filter too, since both read the same result."],
  "builds_on": [{ "symbol": "SessionStatus", "path": "src/dovo/core/sessions/models.py" }],
  "binding": ["class SessionListResult(BaseResult):\n    sessions: list[SessionView]\n    hidden: int"],
  "departures": ["**D-4** Filter in `Sessions.list`, not in the formatter, so JSON output filters too."],
  "tests": ["`tests/cli/session/test_list.py`: a discarded session is not listed, in terminal and JSON output (O-1)."],
  "owned_elsewhere": ["#1197 owns deleting `SessionStatus`."],
  "actions": [
    { "id": "1.1", "kind": "code", "target": "src/dovo/core/sessions/sessions.py::Sessions.list",
      "do": "Leave out discarded sessions unless asked for them.", "requirements": ["O-1"] },
    { "id": "1.2", "kind": "test", "target": "tests/cli/session/test_list.py",
      "do": "A discarded session is not listed, in terminal and JSON output.", "requirements": ["O-1"] }
  ],
  "not_this_phase": ["Phase 2: the --all flag."]
}
```

- `slice` is the slice id, `phase` is `n`, `title` the phase's heading without `Phase <n>:`, and `depends_on` the earlier phases of the slice whose work this one uses.
- `intent` explains why the phase exists and how its parts fit together, from the phase section and the plan's approach, so the agent understands the actions rather than only following them.
- `requirements` quotes every item of `requirements.json` this phase answers, `id` and `text` copied exactly, never paraphrased or cut. An item that several phases answer in part is quoted whole in each of them.
- `decisions` lists the id of every `plan.json` decision whose `phases` include `n`, and of every decision whose `phases` is `[]` (it applies to every phase), and no other. The agent reads their text from `plan.json`.
- `traps` holds the plan's traps and edge cases that touch this phase's files, one sentence each.
- `builds_on` names each symbol from main or an earlier phase that this phase uses, with the path where it lives (for an earlier phase's symbol, the path the plan's Artifact inventory gives it).
- `binding` holds each fenced code block under `### Code` in the plan's `## Phase <n>` section, in order: the text between the fences, copied exactly and without the fence lines. It is `[]` when that section has no `### Code` block. Never edit, shorten or add to a block; a block the plan gets wrong is the plan's to fix, so report it.
- `departures` holds each item of the list under `### Decisions` in the plan's `## Phase <n>` section, and `tests` each item of the list under its `### Tests`. Each is `[]` when the section has no such subsection.
- `owned_elsewhere` holds each item of the plan's `## Owned elsewhere` list; every phase file carries the whole list. It is `[]` when the plan has no such section.
- For `departures`, `tests` and `owned_elsewhere`, an item is a top-level list item, one starting at the line's first column with `- `, `* `, `+ ` or `<n>. `. Copy its text after that marker exactly, with its continuation lines and nested items, up to the next top-level item, a blank line or a heading. The check compares items with each run of whitespace as one space, so joining the item's lines with spaces is a copy.
- `actions` lists the phase's obligations in the order to do them, each one requirement part, one test, one deletion or one doc change, never a single edit. `id` is `<n>.<k>`, numbered from 1. `kind` is `code`, `test`, `delete` (a deletion-ledger entry) or `doc`. `target` is a path, with `::symbol` when the obligation is one symbol. `do` says what must hold once it is done, and, for a test, what it asserts. `requirements` cites the ids it answers, each one the phase quotes; a doc or deletion action may cite none.
- `not_this_phase` is one line per later phase naming work this phase must leave to it.

Every item of `requirements.json` must be cited by at least one action of some phase. When the plan maps an item to no phase, cite it from the action the plan's test lines tie it to, or report that the plan leaves it unmapped.

## 4. Hand off

Run the check yourself before you stop, and fix every problem it names:

```bash
uv run --quiet python .dovo/catalog/scripts/sdlc/v2/plan.py check-breakdown --slice <id>
```

Report the files written, the number of actions per phase, and anything in the plan you could not place in a phase file.
