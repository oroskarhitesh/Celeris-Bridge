"""
``book_slot`` — take one of the times ``find_slots`` offered.

The sharpest ability in the system: it is the one that creates something in the
real world, in someone's actual calendar, off the back of a model's judgement
about what a visitor said.

Three things keep that safe. The slot is named by the reference ``find_slots``
gave out, so a time nobody offered cannot be booked. The calendar, not the
agent, decides whether the slot is still free. And the idempotency key means a
retried call — by the runner, or by a model that did not see the first result —
is the same booking rather than a second one.
"""

from __future__ import annotations

from typing import Any, Mapping

from celeris_bridge.domain.agent.abilities.contract import (
    Ability,
    AbilityContext,
    AbilityResult,
)
from celeris_bridge.domain.agent.engine.slots import (
    SLOT_REFERENCE_PATTERN,
    describe,
    read_reference,
)
from celeris_bridge.domain.agent.qualification import (
    QualificationField,
    Rejected,
    check_detail,
)
from celeris_bridge.ports.calendar import (
    BookingRequest,
    CalendarScope,
    NoCalendar,
    SlotTaken,
)

__all__ = ["book_slot"]

NOT_A_SLOT = (
    "That is not a time that was offered. Call find_slots and offer one of the times "
    "it returns. Do not tell the visitor anything is booked."
)

TAKEN = (
    "That time has just been taken. Apologise briefly, call find_slots again and "
    "offer another. Do not tell the visitor the first one was booked."
)

NO_CALENDAR = (
    "There is no calendar connected, so nothing can be booked. Say you will have "
    "someone follow up to arrange a time."
)

BAD_EMAIL = (
    "That email address will not reach them, so the invitation would go nowhere. Ask "
    "the visitor to check it, and do not say anything is booked."
)

BOOKED = (
    "It is booked and the invitation is on its way to that address. Tell the visitor "
    "the time and who they are meeting. Do not offer another time."
)

_EMAIL_FIELD = QualificationField(
    key="visitorEmail", question="The email for the invitation", required_to_book=True, format="email"
)


def _fingerprint(arguments: Mapping[str, Any], context: AbilityContext) -> str:
    """One conversation, one slot, one booking — however many times it is asked for."""
    return f"{context.conversation_id}:{arguments['slotReference']}"


async def _handler(
    arguments: Mapping[str, Any], context: AbilityContext
) -> AbilityResult:
    services = context.services
    slot = read_reference(str(arguments["slotReference"]))
    if slot is None:
        # The pattern in the schema makes this unlikely, and it is still the
        # difference between "no appointment" and "an appointment the visitor
        # believes in and nobody holds".
        return AbilityResult(output={"booked": False, "note": NOT_A_SLOT})

    # The invitation goes to this address, so it is checked here rather than
    # trusted — through the same function the qualification fields use.
    outcome = check_detail(_EMAIL_FIELD, arguments["visitorEmail"])
    if isinstance(outcome, Rejected):
        return AbilityResult(output={"booked": False, "note": BAD_EMAIL})

    host_id, start, end = slot
    try:
        booking = await services.calendar.book(
            scope=CalendarScope(
                workspace_id=context.workspace_id,
                agent_instance_id=context.agent_instance_id,
            ),
            request=BookingRequest(
                host_id=host_id,
                start=start,
                end=end,
                visitor_name=str(arguments["visitorName"]),
                visitor_email=outcome.value,
                visitor_phone=str(arguments.get("visitorPhone") or ""),
                note=str(arguments.get("note") or ""),
            ),
        )
    except SlotTaken:
        return AbilityResult(output={"booked": False, "note": TAKEN})
    except NoCalendar:
        return AbilityResult(output={"booked": False, "note": NO_CALENDAR})

    return AbilityResult(
        output={
            "booked": True,
            "when": describe(booking.start, booking.time_zone),
            "note": BOOKED,
        },
        meta={"bookingId": booking.booking_id, "hostId": booking.host_id},
    )


book_slot = Ability(
    key="book_slot",
    version="1.0.0",
    label="Book a time",
    description="Books one of the times find_slots offered, on that person's calendar.",
    ai_description=(
        "Book a time the visitor chose. Needs the slotReference from find_slots, their "
        "name and the email the invitation should go to."
    ),
    instructions=(
        "Only call book_slot once the visitor has confirmed a specific time out of the "
        "ones you offered and has given their name and email. Pass the slotReference "
        "exactly as find_slots returned it. Never say anything is booked until this "
        "tool says it is — if it comes back with a note, follow the note."
    ),
    input={
        "slotReference": {
            "type": "string",
            "description": (
                "The slotReference of the chosen time, copied exactly from find_slots."
            ),
            "required": True,
            "pattern": SLOT_REFERENCE_PATTERN,
            "maxLength": 200,
        },
        "visitorName": {
            "type": "string",
            "description": "The visitor's name, as they gave it.",
            "required": True,
            "minLength": 2,
            "maxLength": 120,
        },
        "visitorEmail": {
            "type": "string",
            "description": "The email address the invitation should go to.",
            "required": True,
            "maxLength": 254,
        },
        "visitorPhone": {
            "type": "string",
            "description": "A phone number, only if the visitor offered one.",
            "maxLength": 40,
        },
        "note": {
            "type": "string",
            "description": "One line on what they want to talk about, in their words.",
            "maxLength": 300,
        },
    },
    effect="write",
    # One booking per message. A model that wants two has misread something.
    limits={"timeoutMs": 15_000, "maxCallsPerTurn": 1},
    idempotency_key=_fingerprint,
    handler=_handler,
)
