"""
``find_slots`` — what times are actually free.

The model is never asked for a date. It does not know what day it is, and a
date it works out itself is a time the calendar never offered. It asks in days
from now; the clock and the calendar decide what that means.

Every slot comes back with an opaque reference that ``book_slot`` takes, so a
booking can only ever name a time this ability offered.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from celeris_bridge.domain.agent.abilities.contract import (
    Ability,
    AbilityContext,
    AbilityResult,
)
from celeris_bridge.domain.agent.engine.slots import offer, window
from celeris_bridge.ports.calendar import Calendar, CalendarScope, Clock, NoCalendar

__all__ = ["SchedulingServices", "find_slots"]

NO_CALENDAR = (
    "This company has not connected a calendar, so you cannot offer times. Say you "
    "will have someone follow up to arrange it, and do not mention calendars."
)

NOTHING_FREE = (
    "There is nothing free in that range. Offer to look further ahead, or say you "
    "will have someone follow up. Do not invent a time."
)

FOUND = (
    "Offer two or three of these, in the visitor's words, and say who they would be "
    "meeting. Use the slotReference exactly as given when you book — never a time you "
    "worked out yourself."
)


class SchedulingServices(Protocol):
    """The ports the booking abilities need."""

    @property
    def calendar(self) -> Calendar: ...

    @property
    def clock(self) -> Clock: ...


async def _handler(
    arguments: Mapping[str, Any], context: AbilityContext
) -> AbilityResult:
    services: SchedulingServices = context.services
    start, end = window(
        services.clock.now(),
        start_in_days=int(arguments.get("startInDays") or 0),
        days=int(arguments.get("days") or context.settings["daysAhead"]),
    )

    try:
        availability = await services.calendar.availability(
            scope=CalendarScope(
                workspace_id=context.workspace_id,
                agent_instance_id=context.agent_instance_id,
            ),
            start=start,
            end=end,
            minutes=int(context.settings["meetingMinutes"]),
        )
    except NoCalendar:
        # Not a fault: there is nothing to retry and nothing to apologise for.
        return AbilityResult(output={"slots": [], "note": NO_CALENDAR})

    offers = offer(
        availability.slots,
        availability.hosts,
        time_zone=availability.time_zone,
        limit=int(context.settings["maxSlotsOffered"]),
    )
    if not offers:
        return AbilityResult(
            output={"slots": [], "note": NOTHING_FREE},
            meta={"timeZone": availability.time_zone},
        )

    return AbilityResult(
        output={
            "timeZone": availability.time_zone,
            "note": FOUND,
            "slots": [
                {
                    "slotReference": slot.reference,
                    "when": slot.label,
                    "with": slot.host_name,
                    **({"team": slot.host_team} if slot.host_team else {}),
                    **({"role": slot.host_role} if slot.host_role else {}),
                }
                for slot in offers
            ],
        },
        meta={"timeZone": availability.time_zone, "offered": len(offers)},
    )


find_slots = Ability(
    key="find_slots",
    version="1.0.0",
    label="Find free times",
    description="Offers visitors times that are genuinely free on your team's calendar.",
    ai_description=(
        "Find times that are free to book. Returns each time already written out for "
        "the visitor, who they would be meeting, and a slotReference to book it with."
    ),
    instructions=(
        "Call find_slots when the visitor wants to book or asks when someone is free. "
        "Offer only times it returns, say them exactly as the 'when' field is written, "
        "and never work out a date yourself — you do not know today's date. If nothing "
        "is free, offer to look further ahead rather than guessing a time."
    ),
    input={
        "startInDays": {
            "type": "number",
            "description": (
                "How many days from now to start looking. 0 means from now, 1 means "
                "tomorrow. Leave it out for as soon as possible."
            ),
            "integer": True,
            "min": 0,
            "max": 60,
        },
        "days": {
            "type": "number",
            "description": "How many days to look across. Leave it out for the default.",
            "integer": True,
            "min": 1,
            "max": 14,
        },
    },
    settings={
        "meetingMinutes": {
            "label": "Meeting length",
            "description": "How long a booked meeting runs, in minutes.",
            "type": "number",
            "integer": True,
            "min": 10,
            "max": 180,
            "default": 30,
            "editable": True,
        },
        "daysAhead": {
            "label": "How far ahead to look",
            "description": "Days searched when the agent does not ask for a range.",
            "type": "number",
            "integer": True,
            "min": 1,
            "max": 14,
            "default": 7,
            "editable": True,
        },
        "maxSlotsOffered": {
            "label": "Times to offer at most",
            "description": "More than a handful and the visitor stops reading.",
            "type": "number",
            "integer": True,
            "min": 1,
            "max": 20,
            "default": 8,
            "editable": True,
        },
    },
    effect="read",
    limits={"timeoutMs": 8_000, "maxCallsPerTurn": 3},
    handler=_handler,
)
