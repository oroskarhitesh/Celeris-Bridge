"""
``save_visitor_details`` — write down what the visitor told us.

The first ability that changes anything, which brings two firsts with it: an
``idempotency_key``, because a retried tool call must not count as a second
answer; and ``input_for``, because the arguments are the workspace's own
qualification fields rather than a shape fixed in this file.

The model reports. It does not judge: whether the visitor is a fit is decided in
:mod:`celeris_bridge.domain.agent.qualification`, from the workspace's settings.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Protocol

from celeris_bridge.domain.agent.abilities.contract import (
    Ability,
    AbilityContext,
    AbilityResult,
)
from celeris_bridge.domain.agent.qualification import (
    MAX_DETAIL_CHARACTERS,
    VISITOR_TYPES,
    Assessment,
    Rejected,
    VisitorStatus,
    assess,
    check_detail,
    fields_from,
)
from celeris_bridge.ports.visitor import ConversationRef, VisitorRecord, VisitorRecords

__all__ = ["VisitorServices", "save_visitor_details"]

#: The argument that is not a qualification field.
VISITOR_TYPE = "visitorType"

NO_CONVERSATION = (
    "There is no conversation to attach these details to, so nothing was saved. "
    "Carry on helping the visitor and do not ask for their details again."
)

NOTE_QUALIFIED = (
    "The team now has what it needs and can follow up by email. Do not promise "
    "when, and do not say that anything is booked."
)

NOTE_NOT_A_FIT = (
    "This is not someone to sell to. Keep helping with their questions and point "
    "them somewhere useful. Do not push for a demo, and do not tell them there is "
    "a rule or policy about it — there is not one."
)


class VisitorServices(Protocol):
    """The one port this ability needs."""

    @property
    def visitors(self) -> VisitorRecords: ...


def _inputs_for(agent_settings: Mapping[str, Any]) -> dict[str, Any]:
    """One argument per detail this workspace collects, plus who the visitor is.

    Every one is optional. The model is told to save what the visitor actually
    said, and a required argument would push it into inventing the rest.

    All of them are text, even a field whose format is a number: visitors answer
    "how many people work there?" with "about 40", and a number-typed argument
    leaves the model either guessing a figure or dropping the answer.
    :func:`~celeris_bridge.domain.agent.qualification.check_detail` does the
    parsing, where a bad answer can come back as a sentence the model can act on.
    """
    arguments: dict[str, Any] = {
        field.key: {
            "type": "string",
            "description": f"What the visitor said when asked: {field.question}",
            "maxLength": MAX_DETAIL_CHARACTERS,
        }
        for field in fields_from(agent_settings)
    }
    arguments[VISITOR_TYPE] = {
        "type": "enum",
        "description": (
            "Who the visitor is, if they have made it clear. Leave it out if you "
            "are guessing."
        ),
        "options": list(VISITOR_TYPES),
    }
    return arguments


def _note(assessment: Assessment) -> str | None:
    if assessment.status is VisitorStatus.QUALIFIED:
        return NOTE_QUALIFIED
    if assessment.status is VisitorStatus.NOT_A_FIT:
        return f"{NOTE_NOT_A_FIT} The reason: {'; '.join(assessment.reasons)}."
    if assessment.missing:
        return (
            "Still needed before you can offer a time: "
            + "; ".join(field.question for field in assessment.missing)
            + ". Ask for at most one of these in your next reply."
        )
    return None


def _fingerprint(arguments: Mapping[str, Any], context: AbilityContext) -> str:
    """One conversation saying the same thing twice is one answer, not two."""
    canonical = json.dumps(dict(sorted(arguments.items())), sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{context.conversation_id}:{digest}"


async def _handler(
    arguments: Mapping[str, Any], context: AbilityContext
) -> AbilityResult:
    if not context.conversation_id:
        return AbilityResult(output={"saved": [], "note": NO_CONVERSATION})

    services: VisitorServices = context.services
    reference = ConversationRef(
        workspace_id=context.workspace_id, conversation_id=context.conversation_id
    )
    stored = await services.visitors.load(reference)
    known = stored if stored is not None else VisitorRecord()

    # Read from the agent's settings, not from this ability's: the fields are
    # the agent's, and they may have changed since the schema was built.
    fields = fields_from(context.agent_settings)
    by_key = {field.key: field for field in fields}

    details = dict(known.details)
    saved: list[str] = []
    rejected: list[dict[str, str]] = []

    for key, value in arguments.items():
        if key == VISITOR_TYPE:
            continue
        field = by_key.get(key)
        if field is None:
            # Only reachable when an admin edited the definition mid-conversation.
            rejected.append(
                {"field": key, "reason": f'"{key}" is not something this company collects'}
            )
            continue
        outcome = check_detail(
            field, value, work_email_only=bool(context.settings["workEmailOnly"])
        )
        if isinstance(outcome, Rejected):
            rejected.append({"field": key, "reason": outcome.reason})
            continue
        details[key] = outcome.value
        saved.append(key)

    visitor_type = arguments.get(VISITOR_TYPE) or known.visitor_type or "unknown"
    assessment = assess(
        fields=fields,
        details=details,
        visitor_type=visitor_type,
        not_a_fit_types=context.settings["notAFitVisitors"],
    )
    record = VisitorRecord(
        details=details,
        visitor_type=visitor_type,
        status=assessment.status.value,
        missing=tuple(field.key for field in assessment.missing),
        reasons=assessment.reasons,
    )

    # Two halves to this. A detail or a visitor type is the only thing the
    # model can actually contribute, so with neither there is nothing to create
    # a record from — a call where every answer was rejected must not leave an
    # empty one behind. But a record that already exists is refreshed whenever
    # anything derived from it has moved, which happens on its own when the
    # workspace changes which details it requires.
    learned = bool(saved) or visitor_type != known.visitor_type
    written = learned or (stored is not None and record != known)
    if written:
        await services.visitors.save(reference, record)

    output: dict[str, Any] = {
        "saved": saved,
        "status": assessment.status.value,
        "stillNeeded": [field.question for field in assessment.missing],
    }
    if rejected:
        output["rejected"] = rejected
    note = _note(assessment)
    if note:
        output["note"] = note

    return AbilityResult(
        output=output,
        meta={
            "status": assessment.status.value,
            "detailsKnown": len(details),
            "written": written,
        },
    )


save_visitor_details = Ability(
    key="save_visitor_details",
    version="1.0.0",
    label="Save visitor details",
    description=(
        "Records what visitors share — name, email, company size — and works out "
        "whether they are someone your team should follow up with."
    ),
    ai_description=(
        "Save details the visitor has shared about themselves. Fill only the fields "
        "they actually gave you. Returns where they stand and what is still needed."
    ),
    instructions=(
        "As soon as the visitor shares anything about themselves — a name, an email, "
        "where they work, how big their team is, when they need something — call "
        "save_visitor_details in that same reply, before you answer. Pass only what "
        "they actually said; never fill a field from a guess. Set visitorType when "
        "they have made it clear who they are. Do not decide yourself whether they "
        "are worth following up: this tool tells you."
    ),
    input_for=_inputs_for,
    settings={
        "workEmailOnly": {
            "label": "Only accept work email addresses",
            "description": (
                "On, a personal address such as gmail.com is refused and the agent "
                "asks for a work one."
            ),
            "type": "boolean",
            "default": False,
            "editable": True,
        },
        "notAFitVisitors": {
            "label": "Never treat these as leads",
            "description": (
                "The agent keeps helping them — it just stops qualifying them and "
                "never offers a time."
            ),
            "type": "multiselect",
            "options": [
                {"value": "student", "label": "Students"},
                {"value": "job_seeker", "label": "Job seekers"},
                {"value": "vendor", "label": "Vendors selling to us"},
                {"value": "competitor", "label": "Competitors"},
                {"value": "existing_customer", "label": "Existing customers"},
            ],
            "default": ["student", "job_seeker", "competitor"],
            "editable": True,
        },
    },
    effect="write",
    limits={"timeoutMs": 8_000, "maxCallsPerTurn": 2},
    idempotency_key=_fingerprint,
    handler=_handler,
)
