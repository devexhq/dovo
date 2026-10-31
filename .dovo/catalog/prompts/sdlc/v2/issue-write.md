# Write an issue

Turn a person's description of a change into a GitHub issue in the short format of [templates/issue.md](templates/issue.md). The issue records what the person decided; planning grounds it in the code later, so ground only enough to name dependencies and to catch work that has already shipped.

In rewrite mode, the input is an existing issue written before the short format, one with no `## Outcomes` section, and the output is a draft of its description in that format for a person to approve: follow [Rewrite a legacy issue](#rewrite-a-legacy-issue) below rather than steps 1 to 4.

## 1. Read the request

Read the person's description and anything they point to. Separate what they decided (the problem, the behaviour they want, trade-offs they settled, values they chose) from what they guessed about the code. Only the first goes into the issue.

Then size it against the template's rule that an issue is one independently shippable set of outcomes. When the description covers parts that would each ship on their own, such as the same rule applied to several packages, propose one issue per part to the person before drafting. When the parts share a rule or shared code, the first issue is the foundation: the rule in the architecture docs, the shared code and checks, and one part converted as the pilot. Write the issues in that order, each later one depending on the foundation.

## 2. Ground lightly

Ground against `origin/main` (`git fetch origin main`), reading only; never edit code or run tooling that mutates or validates.

- **Dependencies:** search open issues for work this one needs first (`gh issue list --state open --search "<terms>"`). Each becomes a **Depends on** item stating what this issue assumes it delivers.
- **Already shipped:** check that no outcome is already delivered: the CLI's help (`uv run dovo <command> --help`), merged pull requests (`gh pr list --state merged --search "<terms>"`) and the code (`rg`). Drop an outcome that has shipped, and tell the person.
- **Owners:** for what the issue leaves out, find the open issue that owns it, so **Out of scope** can name it.
- **A bug still happens:** for a bug, follow the steps to reproduce on `main` when they only read state or run in a scratch workspace, and say whether it reproduced. A bug that no longer reproduces is not filed; tell the person which merged pull request fixed it when you can find it.

Record none of what the grounding finds in the code: paths, symbols, revision numbers, column lists, test names and counts stay out of the issue.

## 3. Draft

Write the title and body to the template, its sections in order. A bug, behaviour that is wrong today, uses [templates/bug.md](templates/bug.md): **What happens** and **Steps to reproduce** take the place of **Why**, and **Done when** names the regression test; everything else below holds.

- **Why** in the person's terms.
- One **Outcome** per observable behaviour, numbered from `O-1`, stated as a rule that holds wherever it applies. Where the grounding found many places it applies today, state what they share, with one or two as examples, and leave finding the rest to planning.
- One **Decision** per trade-off the person settled, numbered from `D-1`, with its cost. Never invent one, and leave how the work is done (which field, a check's scope, a doc's wording) to planning. When the description leaves a trade-off open, ask the person, and when they leave it open, leave it out for planning to raise as an open question.
- **Binding values** only for literals the person chose.
- **Done when** naming the outcomes by id, plus any check beyond them.

No hard line wraps: each paragraph and list item is one line, and every issue reference is an explicit `[GH-<n>](https://github.com/devexhq/dovo/issues/<n>)` link. The same holds for every comment and pull request text that sdlc v2 posts, except a `Closes #<n>` line, which GitHub needs as written.

## 4. Check

Run the template's checklist over every sentence and over the whole issue. Then show the person the title and body, and publish only when they ask: `gh issue create --title "<title>" --body-file <file>`. A bug adds `--label type:bug --label priority:<p0-critical|p1-high|p2-medium|p3-low>`, choosing the one priority label by the descriptions of the repository's `priority:*` labels (`gh label list`); any other issue gets no priority label. Add `--blocked-by <n>` for each **Depends on** issue that must merge first.

## Rewrite a legacy issue

Given an existing issue's number, and any overlapping issues you were asked to check, draft its description in the short format and map every item of the old text onto the draft. `issue-plan-v2` and `issue-code-v2` run this mode the first time they plan a legacy issue, show both files to a person and replace the description only once that person approves; never rewrite issues in bulk. Write only `.agentic/issue-rewrite.md` and `.agentic/issue-rewrite-notes.md`, and never edit the issue, its labels or its comments.

1. **Read.** `gh issue view <n> --json number,title,body,comments`, its blockers with `gh issue view <n> --json blockedBy --jq '.blockedBy.nodes[] | "#\(.number) \(.state)"'`, and every overlapping issue you were given.
2. **Ground lightly,** as in step 2, only enough to drop what already shipped and to name overlaps: check each functional requirement and error case against `main`, and search open issues for the same work (`gh issue list --state open --search "<terms>"`).
3. **Draft** `.agentic/issue-rewrite.md`, the whole description, to the template as in step 3:
   - **Keep every decision a person made.** A choice the old text settles (in its scope, a requirement, a guard, its Pre-determined data or a note) becomes a decision with its cost, or, for a literal a person chose (a name, a message, an exit code, a key), a Binding values line with the exact value. When unsure whether a person decided something, keep it.
   - **A bug** (labelled `type:bug`, or describing behaviour that is wrong today) is drafted to [templates/bug.md](templates/bug.md): **What happens** and **Steps to reproduce** from the old report's observed behaviour and repro, keeping the user-facing steps and dropping any guessed cause or source location.
   - **Outcomes** from the functional requirements and error cases: the behaviour a user or caller observes, one item each, numbered from `O-1`. Leave out what has already shipped. Requirements that repeat one rule across a list of sites become that one rule.
   - **Why** from the goal or user story; **Depends on** from the blockers and the issues the old text says must land first, each with its assumption; **Out of scope** from the items another issue owns, naming that issue; **Done when** naming the outcomes by id, plus any doc check the old definition of done holds.
   - **Drop what the code can tell:** paths, symbols, revision numbers, column lists, test names, counts, inventories, implementation steps, restated `AGENTS.md` rules and grep bars.
   - Keep an opening `Slice <id> of #<issue> ...` line and any marker comment verbatim. The title stays as it is: the rewrite replaces only the description.
4. **Map** in `.agentic/issue-rewrite-notes.md`, one line per item:
   - `## Mapping`: each `FR-n`, `NFR-n` and numbered error case (`EC-n`, by its position in the list), and each Pre-determined data value or other decision the old text states, quoted briefly, then `→` and the `O-n`, `D-n` or Binding value it became, or `dropped: <why>` (shipped in a named commit or pull request, a detail the code can tell, a restated rule). A decision is never dropped.
   - `## Shipped or stale`: what the old text asks for that `main` already delivers or that no longer holds, with the commit or pull request; left out when there is none.
   - `## Overlaps`: each open issue that covers some of the same work, what overlaps, and the ownership question the person settles (`#1255 also closes interrupted steps: does this issue or #1255 own that?`), saying which answer the draft assumes; left out when there is none.
5. **Check.** Run the template's checklist over the draft, and check that every decision in the old text appears in the mapping as a `D-n` or a Binding value. No hard line wraps in either file. Under a blueprint, stop here: it shows both files and applies the rewrite. Run by hand, show the person both files, and replace the description only when they approve: `uv run --quiet python .dovo/catalog/scripts/sdlc/v2/plan.py rewrite-apply --issue <n> --body-file .agentic/issue-rewrite.md --body-sha <sha>`, with the sha `uv run --quiet python .dovo/catalog/scripts/sdlc/v2/plan.py body-sha --issue <n>` printed when you read the issue. It refuses when someone has edited the description since, and otherwise replaces it without posting a comment; GitHub keeps the old description in the issue's edit history.
