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
