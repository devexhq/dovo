# Issue template

An issue records what a person decided; the plan records what the code says. Planning grounds the issue in the code just in time, and anything the code can tell would go stale in the issue, so leave it out: no paths, symbols, revision numbers, column lists, test names or counts.

Use the sections below in this order. Why, Outcomes and Done when are required; write every other section only when it has something to say, and leave it out otherwise, never filled with "none". Add no other section. An issue split out to give one slice its own issue keeps its opening line, `Slice <id> of #<issue> ...`, before Why, and a seeded issue keeps its marker comment as its last line; nothing else goes outside the sections.

No hard line wraps: each paragraph and each list item is one line, so the text uses GitHub's full width.

```markdown
## Why
<The problem a person has today and why it is worth solving now. One short paragraph.>

## Depends on
- #<n>: <what this issue assumes #<n> delivers, which planning checks; and what to do if it does not hold>

## Outcomes
- **O-1** <Behaviour a user or caller can observe once the issue is done: what they run, see or get.>

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
- **Outcomes:** observable behaviour, numbered `O-1`, `O-2` and on, one outcome per item, each one line.
- **Decisions:** trade-offs a person settled, numbered `D-1`, `D-2` and on, each with its cost and the alternative it rules out when there is one.
- **Binding values:** only literals a person chose, which planning copies exactly; a value the code already fixes is left out.
- **Out of scope:** the boundary a reader could get wrong, never work the outcomes need.
- **Done when:** the outcomes by id, plus checks beyond them.

## Checklist

Test each sentence before keeping it:

1. Could the planner work this out by reading the code? Then leave it out.
2. Would it still be true after a behaviour-preserving refactor? If not, it's planning: leave it out.

Then check the whole issue:

- Every outcome and decision has its own id, and no id appears twice.
- No open question: a trade-off a person has not settled is asked before writing, or left out for planning to raise.
- Every paragraph and list item is one line.
