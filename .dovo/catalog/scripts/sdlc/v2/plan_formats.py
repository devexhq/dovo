"""The shapes of the records sdlc v2's planning steps write and read.

A decision is one entry of `decisions` in .agentic/plan.json: a choice the plan
is written against, and who settled it. `settled_by` is `human` for an open
question the human answered, with `answer` holding the human's words;
`assumed_accepted` for an open question the human left to its assumed answer;
and `planner` for a departure from the issue the planner made, which the human
approves with the plan. `was` is the id of the open question the decision
settles, or null for a planner's departure that was never one. `replaces`, when
present, names the decision an earlier planning session posted to the issue
that this one takes the place of: `{"id": "D-2", "session": "<session id>"}`.

A prior decision is one entry of `decisions` in .agentic/prior-decisions.json,
which `plan.py prior-decisions` writes from the issue's `dovo:decisions`
comments. Its `status` is `given` when the issue's description is unchanged
since the comment was posted, and `to_confirm` otherwise.

A requirement is one entry of `requirements` in .agentic/requirements.json,
which `plan.py requirements` writes from an issue's description: a numbered
item and its exact text. The document's `format` is `short` when the
description has an `## Outcomes` section, whose items are outcomes (`O-<n>`)
and whose `## Decisions` items are decisions (`D-<n>`), and `legacy` otherwise,
whose items are functional and non-functional requirements (`FR-<n>`,
`NFR-<n>`) and numbered error cases (`EC-<n>`).

A phase is the document .agentic/phases/phase-<n>.json, which the breakdown
step writes from the approved plan's `## Phase <n>` section: the one file the
code loop's agent for phase n implements. Its `requirements` quote entries of
requirements.json with their exact text, its `decisions` are the ids of the
plan.json decisions whose `phases` include n, and each of its `actions` is one
obligation (`code`, `test`, `delete` or `doc`) on a `path[::symbol]` target,
citing the ids of the quoted requirements it answers. Its `binding` copies,
exactly, the code blocks under `### Code` in the plan's `## Phase <n>`
section, the new surface the plan makes binding. Its `departures` and `tests`
copy the list items under that section's `### Decisions` and `### Tests`, and
its `owned_elsewhere` copies the plan's `## Owned elsewhere` list; each may be
empty. A phase has no other field. `plan.py check-breakdown` writes .agentic/phases/index.json from the
phases it passes: per phase, its file, title, dependencies, requirement and
decision ids, and action targets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

SETTLED_BY = ("human", "assumed_accepted", "planner")
FROM_QUESTION = frozenset({"human", "assumed_accepted"})
DECISION_ID = re.compile(r"D-\d+")
QUESTION_ID = re.compile(r"OQ-\d+")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
SESSION_ID = re.compile(r"[^\s()]+")
PRIOR_SCHEMA_VERSION = 1
REQUIREMENTS_SCHEMA_VERSION = 1
PHASE_SCHEMA_VERSION = 1
INDEX_SCHEMA_VERSION = 1
ACTION_KINDS = ("code", "test", "delete", "doc")
ACTION_ID = re.compile(r"(?P<phase>\d+)\.\d+")
PHASE_FIELDS = frozenset(
    {
        "schema_version",
        "slice",
        "phase",
        "title",
        "depends_on",
        "intent",
        "requirements",
        "decisions",
        "traps",
        "builds_on",
        "binding",
        "departures",
        "tests",
        "owned_elsewhere",
        "actions",
        "not_this_phase",
    }
)
ACTION_FIELDS = frozenset({"id", "kind", "target", "do", "requirements"})


class FormatError(ValueError):
    """Raised when a record does not have its shape; the message is the one-line reason."""


def _text(raw: dict[str, object], key: str, owner: str) -> str:
    """Return raw[key] when it is a non-empty string.

    Raises:
        FormatError: raw[key] is missing, not a string or empty.
    """
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FormatError(f"{owner} has no {key}.")
    return value


def _texts(raw: dict[str, object], key: str, owner: str) -> tuple[str, ...]:
    """Return raw[key] when it is a list of non-empty strings.

    Raises:
        FormatError: raw[key] is missing or holds anything but non-empty strings.
    """
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise FormatError(f"{owner} has no {key} list of strings.")
    return tuple(item for item in value if isinstance(item, str))


def _numbers(raw: dict[str, object], key: str, owner: str) -> tuple[int, ...]:
    """Return raw[key] when it is a list of phase numbers.

    Raises:
        FormatError: raw[key] is missing or holds anything but integers.
    """
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        raise FormatError(f"{owner} has no {key} list of phase numbers.")
    return tuple(item for item in value if isinstance(item, int))


def _settled_by(raw: dict[str, object], owner: str) -> str:
    """Return raw's settled_by when it is one of SETTLED_BY.

    Raises:
        FormatError: settled_by is missing or names no known way of settling.
    """
    value = raw.get("settled_by")
    if value is None:
        raise FormatError(f"{owner} has no settled_by.")
    if value not in SETTLED_BY:
        raise FormatError(f"{owner} has settled_by {value!r}; it must be one of {', '.join(SETTLED_BY)}.")
    return str(value)


def _was(raw: dict[str, object], settled_by: str, owner: str) -> str | None:
    """Return the open question id raw settles, or None for a planner's departure that was never one.

    Raises:
        FormatError: was is not an open question id, or is null on a decision settled from a question.
    """
    value = raw.get("was")
    if value is None and settled_by not in FROM_QUESTION:
        return None
    if not isinstance(value, str) or not QUESTION_ID.fullmatch(value):
        raise FormatError(f"{owner} has was {value!r}; it must be the id of the open question it settles.")
    return value


def _answer(raw: dict[str, object], settled_by: str, owner: str) -> str | None:
    """Return the human's answer, which a decision has exactly when the human settled it.

    Raises:
        FormatError: a human's decision has no answer, or another decision has one.
    """
    if settled_by == "human":
        return _text(raw, "answer", owner)
    if raw.get("answer") is not None:
        raise FormatError(f"{owner} has an answer, but only a decision settled_by human has one.")
    return None


def _date(raw: dict[str, object], owner: str) -> str:
    """Return raw's date when it is a calendar date written YYYY-MM-DD.

    Raises:
        FormatError: date is missing or not a YYYY-MM-DD date.
    """
    value = raw.get("date")
    if isinstance(value, str) and ISO_DATE.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            pass
        else:
            return value
    raise FormatError(f"{owner} has date {value!r}; it must be a YYYY-MM-DD date.")


@dataclass(frozen=True)
class Replaced:
    """A decision an earlier planning session posted, named by its id and that session."""

    id: str
    session: str


def _replaces(raw: dict[str, object], owner: str) -> Replaced | None:
    """Return the earlier decision raw replaces, or None when it replaces none.

    Raises:
        FormatError: replaces is present but has no D-<n> id or no session id.
    """
    value = raw.get("replaces")
    if value is None:
        return None
    ident = value.get("id") if isinstance(value, dict) else None
    session = value.get("session") if isinstance(value, dict) else None
    if not isinstance(ident, str) or not DECISION_ID.fullmatch(ident):
        raise FormatError(f"{owner} has replaces {value!r}; its id must be D-<n>.")
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
        raise FormatError(f"{owner} has replaces {value!r}; its session must be a session id.")
    return Replaced(id=ident, session=session)


@dataclass(frozen=True)
class Decision:
    """One settled choice in plan.json's decisions, and who settled it."""

    id: str
    was: str | None
    decision: str
    settled_by: str
    answer: str | None
    rejected: tuple[str, ...]
    phases: tuple[int, ...]
    date: str
    replaces: Replaced | None

    @classmethod
    def from_json(cls, raw: object, position: int) -> Decision:
        """Return the decision raw describes; position (from 1) names it while it has no id.

        Raises:
            FormatError: raw is not a well-formed decision; the message names the decision and the field.
        """
        if not isinstance(raw, dict):
            raise FormatError(f"Decision {position} is not a JSON object.")
        ident = raw.get("id")
        if not isinstance(ident, str) or not DECISION_ID.fullmatch(ident):
            raise FormatError(f"Decision {position} has id {ident!r}; it must be D-<n>.")
        owner = f"Decision {ident}"
        settled_by = _settled_by(raw, owner)
        return cls(
            id=ident,
            was=_was(raw, settled_by, owner),
            decision=_text(raw, "decision", owner),
            settled_by=settled_by,
            answer=_answer(raw, settled_by, owner),
            rejected=_texts(raw, "rejected", owner),
            phases=_numbers(raw, "phases", owner),
            date=_date(raw, owner),
            replaces=_replaces(raw, owner),
        )


@dataclass(frozen=True)
class PriorDecision:
    """A decision read back from one of the issue's dovo:decisions comments, with the bullet's text after its id."""

    id: str
    text: str
    slice: str
    session: str
    status: str

    def to_json(self) -> dict[str, object]:
        """Return the entry as prior-decisions.json's decisions holds it."""
        return {"id": self.id, "text": self.text, "slice": self.slice, "session": self.session, "status": self.status}


def prior_decisions_json(issue: int, decisions: list[PriorDecision]) -> dict[str, object]:
    """Return the prior-decisions.json document listing issue's prior decisions."""
    return {
        "schema_version": PRIOR_SCHEMA_VERSION,
        "issue": issue,
        "decisions": [decision.to_json() for decision in decisions],
    }


@dataclass(frozen=True)
class Requirement:
    """One numbered item of an issue's description: its id, its kind and its text after the id."""

    id: str
    kind: str
    text: str

    def to_json(self) -> dict[str, object]:
        """Return the entry as requirements.json's requirements holds it."""
        return {"id": self.id, "kind": self.kind, "text": self.text}

    @classmethod
    def from_json(cls, raw: object, position: int) -> Requirement:
        """Return the requirement raw describes; position (from 1) names it while it has no id.

        Raises:
            FormatError: raw is not a JSON object with an id, a kind and a text.
        """
        if not isinstance(raw, dict):
            raise FormatError(f"Requirement {position} is not a JSON object.")
        owner = f"Requirement {position}"
        return cls(id=_text(raw, "id", owner), kind=_text(raw, "kind", owner), text=_text(raw, "text", owner))


def requirements_json(issue: int, issue_format: str, requirements: list[Requirement]) -> dict[str, object]:
    """Return the requirements.json document listing issue's numbered items, in the description's order."""
    return {
        "schema_version": REQUIREMENTS_SCHEMA_VERSION,
        "issue": issue,
        "format": issue_format,
        "requirements": [requirement.to_json() for requirement in requirements],
    }


def _object(raw: object, owner: str, allowed: frozenset[str]) -> dict[str, object]:
    """Return raw when it is a JSON object holding only fields in allowed.

    Raises:
        FormatError: raw is not a JSON object, or holds a field outside allowed.
    """
    if not isinstance(raw, dict):
        raise FormatError(f"{owner} is not a JSON object.")
    unknown = sorted(str(key) for key in raw if key not in allowed)
    if unknown:
        raise FormatError(f"{owner} has unknown field {', '.join(unknown)}.")
    return raw


def _objects(raw: dict[str, object], key: str, owner: str) -> list[object]:
    """Return raw[key] when it is a list.

    Raises:
        FormatError: raw[key] is missing or not a list.
    """
    value = raw.get(key)
    if not isinstance(value, list):
        raise FormatError(f"{owner} has no {key} list.")
    return value


@dataclass(frozen=True)
class Quote:
    """A requirement a phase answers: its id and its text, quoted exactly from requirements.json."""

    id: str
    text: str

    @classmethod
    def from_json(cls, raw: object, owner: str) -> Quote:
        """Return the quote raw describes.

        Raises:
            FormatError: raw is not an object with exactly an id and a text.
        """
        named = f"{owner} requirement"
        quote = _object(raw, named, frozenset({"id", "text"}))
        return cls(id=_text(quote, "id", named), text=_text(quote, "text", named))


@dataclass(frozen=True)
class BuildsOn:
    """A symbol main or an earlier phase already holds that a phase uses, with the path it lives at."""

    symbol: str
    path: str

    @classmethod
    def from_json(cls, raw: object, owner: str) -> BuildsOn:
        """Return the entry raw describes.

        Raises:
            FormatError: raw is not an object with exactly a symbol and a path.
        """
        named = f"{owner} builds_on entry"
        entry = _object(raw, named, frozenset({"symbol", "path"}))
        return cls(symbol=_text(entry, "symbol", named), path=_text(entry, "path", named))


@dataclass(frozen=True)
class Action:
    """One obligation of a phase: what to do to which target, and the requirements it answers."""

    id: str
    kind: str
    target: str
    do: str
    requirements: tuple[str, ...]

    @classmethod
    def from_json(cls, raw: object, phase: int, owner: str) -> Action:
        """Return the action raw describes, whose id must be `<phase>.<k>`.

        Raises:
            FormatError: raw is not a well-formed action of phase; the message names the action and the field.
        """
        action = _object(raw, f"{owner} action", ACTION_FIELDS)
        ident = action.get("id")
        match = ACTION_ID.fullmatch(ident) if isinstance(ident, str) else None
        if match is None or int(match["phase"]) != phase:
            raise FormatError(f"{owner} has action id {ident!r}; it must be {phase}.<k>.")
        named = f"{owner} action {ident}"
        kind = action.get("kind")
        if kind not in ACTION_KINDS:
            raise FormatError(f"{named} has kind {kind!r}; it must be one of {', '.join(ACTION_KINDS)}.")
        return cls(
            id=str(ident),
            kind=str(kind),
            target=_text(action, "target", named),
            do=_text(action, "do", named),
            requirements=_texts(action, "requirements", named),
        )


def _decision_ids(raw: dict[str, object], owner: str) -> tuple[str, ...]:
    """Return raw's decisions when it is a list of D-<n> ids.

    Raises:
        FormatError: decisions is missing or holds anything but D-<n> ids.
    """
    ids = _texts(raw, "decisions", owner)
    wrong = [ident for ident in ids if not DECISION_ID.fullmatch(ident)]
    if wrong:
        raise FormatError(f"{owner} has decisions {', '.join(wrong)}; each must be a D-<n> id from plan.json.")
    return ids


def _twice(ids: list[str]) -> list[str]:
    """Return the ids that appear more than once in ids, sorted."""
    return sorted({ident for ident in ids if ids.count(ident) > 1})


@dataclass(frozen=True)
class Phase:
    """One phase of a slice, as the code loop's agent for that phase reads it."""

    slice: str
    phase: int
    title: str
    depends_on: tuple[int, ...]
    intent: str
    requirements: tuple[Quote, ...]
    decisions: tuple[str, ...]
    traps: tuple[str, ...]
    builds_on: tuple[BuildsOn, ...]
    binding: tuple[str, ...]
    departures: tuple[str, ...]
    tests: tuple[str, ...]
    owned_elsewhere: tuple[str, ...]
    actions: tuple[Action, ...]
    not_this_phase: tuple[str, ...]

    @classmethod
    def from_json(cls, raw: object, owner: str) -> Phase:
        """Return the phase raw describes; owner (its file's name) names it in a refusal.

        Raises:
            FormatError: raw is not a well-formed phase; the message names the file and the field.
        """
        doc = _object(raw, owner, PHASE_FIELDS)
        if doc.get("schema_version") != PHASE_SCHEMA_VERSION:
            raise FormatError(f"{owner} has schema_version {doc.get('schema_version')!r}; it must be 1.")
        number = doc.get("phase")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise FormatError(f"{owner} has phase {number!r}; it must be a phase number.")
        phase = cls(
            slice=_text(doc, "slice", owner),
            phase=number,
            title=_text(doc, "title", owner),
            depends_on=_numbers(doc, "depends_on", owner),
            intent=_text(doc, "intent", owner),
            requirements=tuple(Quote.from_json(item, owner) for item in _objects(doc, "requirements", owner)),
            decisions=_decision_ids(doc, owner),
            traps=_texts(doc, "traps", owner),
            builds_on=tuple(BuildsOn.from_json(item, owner) for item in _objects(doc, "builds_on", owner)),
            binding=_texts(doc, "binding", owner),
            departures=_texts(doc, "departures", owner),
            tests=_texts(doc, "tests", owner),
            owned_elsewhere=_texts(doc, "owned_elsewhere", owner),
            actions=tuple(Action.from_json(item, number, owner) for item in _objects(doc, "actions", owner)),
            not_this_phase=_texts(doc, "not_this_phase", owner),
        )
        phase.check_references(owner)
        return phase

    def check_references(self, owner: str) -> None:
        """Refuse a phase with no action, an id listed twice, or an action citing a requirement the phase does not quote.

        Raises:
            FormatError: the phase's actions and quotes do not fit together; the message names the id.
        """
        if not self.actions:
            raise FormatError(f"{owner} has no actions.")
        quoted = [quote.id for quote in self.requirements]
        for ids, what in ((quoted, "requirement"), ([action.id for action in self.actions], "action")):
            if twice := _twice(ids):
                raise FormatError(f"{owner} lists {what} {', '.join(twice)} twice.")
        for action in self.actions:
            if unquoted := [ident for ident in action.requirements if ident not in quoted]:
                raise FormatError(f"{owner} action {action.id} cites {', '.join(unquoted)}, which it does not quote.")

    def to_index(self, file: str) -> dict[str, object]:
        """Return the phase's entry in index.json, naming file as where it lives."""
        return {
            "phase": self.phase,
            "file": file,
            "title": self.title,
            "depends_on": list(self.depends_on),
            "requirements": [quote.id for quote in self.requirements],
            "decisions": list(self.decisions),
            "targets": list(dict.fromkeys(action.target for action in self.actions)),
        }


def phase_index_json(slice_id: str, entries: list[dict[str, object]]) -> dict[str, object]:
    """Return the index.json document listing a slice's phase entries, in the slice's phase order."""
    return {"schema_version": INDEX_SCHEMA_VERSION, "slice": slice_id, "phases": entries}
