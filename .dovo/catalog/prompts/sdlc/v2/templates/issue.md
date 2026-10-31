# Issue template

An issue records what a person decided; the plan records what the code says. Planning grounds the issue in the code just in time, and anything the code can tell would go stale in the issue, so leave it out: no paths, symbols, revision numbers, column lists, test names or counts. Name only public surface (commands, flags, JSON fields, and literal texts a person chose), and state each outcome as a rule rather than a list of the places it applies today, so the issue still holds after the code changes.

An issue is one independently shippable set of outcomes, usually one package or one user-facing surface. When the work spans parts that would each ship on their own, write one issue per part. When those parts share a rule or shared code, the first issue is the foundation: it lands the rule in the architecture docs, the shared code and checks, and the rule applied to one part as the pilot, and every other part's issue depends on it and cites the rule instead of restating it.

A bug, behaviour that is wrong today, uses [bug.md](bug.md) instead: it opens with what happens and the steps to reproduce it in place of **Why**, and follows this template in everything else.

Use the sections below in this order. Why, Outcomes and Done when are required; write every other section only when it has something to say, and leave it out otherwise, never filled with "none". Add no other section. An issue split out for one slice keeps its opening line, `Slice <id> of #<issue> ...`, before Why, and a seeded issue keeps its marker comment as its last line; nothing else goes outside the sections.

No hard line wraps: each paragraph and each list item is one line, so the text uses GitHub's full width. Write every issue reference as an explicit link, `[GH-<n>](https://github.com/devexhq/dovo/issues/<n>)`, where it fits in the prose: GitHub expands `#<n>` into the issue's full title everywhere, and a bare `GH-<n>` inside a list item, but never an explicit link.

```markdown
## Why
<The problem a person has today and why it is worth solving now. One short paragraph.>

## Depends on
- [GH-<n>](https://github.com/devexhq/dovo/issues/<n>): <what this issue assumes it delivers, which planning checks; and what to do if it does not hold>

## Outcomes
- **O-1** <A rule a user or caller can observe once the issue is done: what they run, see or get, wherever it applies.>

## Decisions
- **D-1** <A trade-off a person settled, and what it costs. Accepted.>

## Binding values
- <A literal a person chose, such as a name, a message or a limit>: `<the exact value>`

## Out of scope
- <What a reader might expect this issue to do and it does not; name the issue that does it, when one does.>

## Done when
<The outcomes that must hold, and any check beyond them, such as a doc that must stop saying something.>
```

## What each section holds

- **Why:** the reason for the work in a person's terms, never the fix.
- **Depends on:** each issue this one needs first, with the assumption about it that planning checks against `main`.
- **Outcomes:** observable behaviour, numbered `O-1`, `O-2` and on, one outcome per item, each one line. Each is a rule that holds wherever it applies, ideally one a check (a lint or a test) keeps true, never a list of today's call sites, messages or commands; "for example" may name one or two.
- **Decisions:** trade-offs a person settled and would defend, numbered `D-1`, `D-2` and on, each with its cost and the alternative it rules out when there is one. How the work is done (which field carries something, a check's exact scope, a doc's wording) is planning's, not a decision.
- **Binding values:** only literals a person chose, which planning copies exactly; a value the code already fixes is left out.
- **Out of scope:** the boundary a reader could get wrong, never work the outcomes need.
- **Done when:** the outcomes by id, plus checks beyond them.

## Checklist

Test each sentence before keeping it:

1. Could the planner work this out by reading the code? Then leave it out.
2. Would it still be true after a behaviour-preserving refactor? If not, it's planning: leave it out.
3. Does it list the places a rule applies today? State the rule they share instead, with one or two examples at most.
4. Is it a choice of how to do the work rather than a trade-off a person would defend? Leave it to planning.

Then check the whole issue:

- It is one independently shippable set of outcomes; parts that would ship on their own are separate issues, after a foundation issue when they share a rule or code.
- Every issue reference is an explicit `[GH-<n>](…)` link, never `#<n>` or a bare `GH-<n>`.
- Every outcome and decision has its own id, and no id appears twice.
- No open question: a trade-off a person has not settled is asked before writing, or left out for planning to raise.
- Every paragraph and list item is one line.
