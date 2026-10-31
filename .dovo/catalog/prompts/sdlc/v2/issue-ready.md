# Issue ready check

Decide whether an existing issue is ready to plan, against `origin/main` as it is now. An issue records what a person decided, in the short format of [templates/issue.md](templates/issue.md), and planning grounds it in the code just in time. So the check is light. It asks whether the person's intent still holds on `main`, never whether the description matches the code.

The check never edits the issue, its labels or its comments. It writes its verdict to `.agentic/`, and the blueprint applies it after a person approves; run by hand, report the verdict and ask before applying anything. The description is the person's, so no finding proposes a change to the title or the body. The blueprints rewrite a legacy issue, one written before the short format, with the rewrite mode of [issue-write.md](issue-write.md#rewrite-a-legacy-issue) and a person's approval before they run the check, so the check reads only short issues.

## 1. Read

- Delete any earlier `.agentic/issue-*` files and `.agentic/requirements.json`, so a stale verdict is never applied.
- `git fetch origin main` and record `git rev-parse --short origin/main`. Under the blueprint the `ready-base` step has already fetched and checked out `origin/main`, so a fetch the agent sandbox refuses changes nothing; do not report it.
- Read the issue with `gh issue view <n> --json number,title,body,labels,state`, its blockers with `gh issue view <n> --json blockedBy --jq '.blockedBy.nodes[] | "#\(.number) \(.state)"'` and its sub-issues with `gh issue view <n> --json subIssues --jq '.subIssues.nodes[] | "#\(.number) \(.state) \(.title)"'`.
- Record the `body_sha` of the description as you read it: `uv run --quiet python .dovo/catalog/scripts/sdlc/v2/plan.py body-sha --issue <n>`.
- List its numbered items: `uv run python .dovo/catalog/scripts/sdlc/v2/plan.py requirements --issue <n> --out .agentic/requirements.json`. Its `format` is `short` when the description has an `## Outcomes` section and `legacy` otherwise; its `requirements` are the outcomes (`O-n`) and decisions (`D-n`) of a short issue, each with its exact text. Exit 1 names an id the description numbers twice: a `structure` finding (check 4).

## 2. Gates

Each failure is a `gate` finding that only a person can settle, so the verdict is `not_ready`:

- The issue is open.
- It has no open sub-issues, unless it is the issue `issue-plan-v2` is about to slice, which maps its sub-issues onto slices, or the issue `issue-code-v2` works one of its slices on, whose work plan already maps them. Any other issue with sub-issues is planned as an epic, not readied as one slice; name the first open sub-issue whose blockers are all closed.
- It has exactly one `dovo-size-*` label.

## 3. Checks

Run the four checks against `origin/main`. Every finding quotes the issue text it is about and cites its evidence.

1. **Dependencies.** Every issue under **Depends on**, and every blocker, is closed, or its stated assumption still holds on `main`. An open dependency whose assumption no longer holds, or that states none, is a `gate` finding: a person merges it, restates the assumption or drops the dependency.
2. **Nothing already delivered.** No outcome (`O-n`) is already delivered on `main`. Check what the item says a user or caller observes: the CLI's help (`uv run dovo <command> --help`), merged pull requests (`gh pr list --state merged --search "<terms>"`) and the code (`rg`). A delivered item, or the delivered part of one, is a `stale` finding naming the commit or pull request that delivered it: a person drops the item or closes the issue. When `issue-code-v2` checks the issue for one slice of a plan with two or more slices, check only the outcomes that slice's `outcomes` lists: the plan's other slices deliver the rest, and the merged ones have already delivered theirs.
3. **No decision contradicted.** No decision (`D-n`) is contradicted by work merged since the issue was written: a merged change that settled the same question another way. Each is a `contradiction` finding citing the decision and the commit or pull request: a person keeps the decision, which then reverses the merged work, or drops it.
4. **The short template.** The description follows [templates/issue.md](templates/issue.md):
   - A short issue has Why, Outcomes and Done when; no section outside the template; its sections in the template's order; at least one numbered outcome in `requirements.json`; and no id numbered twice. Each miss is a `structure` finding, and the verdict is `not_ready`: a person fixes the description on GitHub, since this check never rewrites the description. Text the template's checklist would leave out, such as a path, a symbol, a count, a list of the sites a rule applies to, an implementation choice recorded as a decision or a `#<n>` reference, is not a finding, and neither is an issue the template would split. The description is the person's, and planning grounds every such claim. List it under Notes in the report, one line each.
   - A legacy issue, one without an `## Outcomes` section, is one `structure` finding: "Legacy long-format issue; rewrite it to the short format with issue-write.md's rewrite mode first." The verdict is `not_ready`.

A finding needs evidence: a `path:line`, a sha, a pull request or an issue number. A shape you would prefer, but the issue's own decisions do not require, is not a finding.

## 4. Report

Write `.agentic/issue-ready.md`: the verdict, the base sha, one line per finding with its evidence and fix, and a Notes heading with one line per note from check 4, left out when there is none. Write `.agentic/issue-ready-comment.md`, the comment posted with the label: `Ready to plan at <sha> (<YYYY-MM-DD>).`, followed by `No changes needed.` The blueprint posts it with `plan.py ready-comment`, which heads it with a `<!-- dovo:ready ... -->` marker naming the description the check read, so `issue-code-v2` re-checks the issue only once that description changes. Write `.agentic/issue-ready.json` to agree with the report (the issue numbers and shas below are illustrative placeholders; do not look them up):

```json
{
  "issue": 9000,
  "verdict": "not_ready",
  "grounded_at": "630086c0",
  "body_sha": "584163b1c188761f1b9f70578ae35323dfbbd478c65c7cd6fa30bd28c427eb24",
  "findings": [
    {
      "id": "F1",
      "kind": "stale",
      "summary": "O-2 says `dovo session list --all` lists discarded sessions, which shipped in fa99a3eb.",
      "evidence": "fa99a3eb",
      "fix": "Drop O-2, or close the issue if nothing else is left."
    }
  ]
}
```

- `verdict` is `ready` (no findings) or `not_ready` (a gate failed or a finding needs a person).
- `body_sha` names the description the check certifies: the sha step 1 recorded. `plan.py ready-comment` refuses to post the comment, and the issue stays unlabelled, when the description is no longer that one.
- `kind` is `gate`, `stale`, `contradiction` or `structure`. `evidence` is a `path:line`, a sha, a pull request or an issue number; for a `contradiction`, the decision id and the commit or pull request (`D-2, #1290`).

Remove the `dovo:ready` label whenever the body is edited by hand: it certifies the body as of its comment's sha.
