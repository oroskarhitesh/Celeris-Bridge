"""
Ports for time: what the clock says, and what the calendar has free.

``Clock`` is a port rather than a call to ``datetime.now`` because "now" is an
input to every slot search, and a test that cannot choose it is a test that
behaves differently in the afternoon.

``SlotTaken`` is declared here, with the port, because a slot going stale
between being offered and being booked is **normal**. Two visitors can be
looking at the same Tuesday. An ability has to tell the model something it can
act on — offer another time — rather than "unavailable", which gets the visitor
an apology and no appointment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Protocol, runtime_checkable

__all__ = [
    "Availability",
    "Booking",
    "BookingRequest",
    "Calendar",
    "CalendarScope",
    "Clock",
    "Host",
    "NoCalendar",
    "Slot",
    "SlotTaken",
]


@runtime_checkable
class Clock(Protocol):
    """What time it is, in UTC."""

    def now(self) -> datetime: ...


@dataclass(frozen=True)
class CalendarScope:
    """Whose calendar. From the authenticated principal, never from the model."""

    workspace_id: str
    agent_instance_id: str | None = None


@dataclass(frozen=True)
class Host:
    """Someone a visitor can meet.

    Only what a visitor may be told: a name, a team, a role. No email, no
    calendar id, nothing that identifies an employee outside the booking.
    """

    host_id: str
    name: str
    team: str = ""
    role: str = ""


@dataclass(frozen=True)
class Slot:
    """A time at least one host is free."""

    start: datetime
    end: datetime
    host_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Availability:
    """What is free, and how to say it in the workspace's own time zone."""

    time_zone: str = "UTC"
    slots: tuple[Slot, ...] = ()
    hosts: Mapping[str, Host] = field(default_factory=dict)


@dataclass(frozen=True)
class BookingRequest:
    """One booking, as the agent asks for it."""

    host_id: str
    start: datetime
    end: datetime
    visitor_name: str
    visitor_email: str = ""
    visitor_phone: str = ""
    note: str = ""


@dataclass(frozen=True)
class Booking:
    """A booking that exists."""

    booking_id: str
    host_id: str
    start: datetime
    end: datetime
    #: The workspace's zone, so the confirmation reads in the same one the
    #: offer did. Carried on the booking rather than looked up again: a second
    #: call to learn how to phrase the time we already have is a round trip
    #: that can fail after the appointment is already made.
    time_zone: str = "UTC"


class SlotTaken(Exception):
    """That time is no longer free. Expected, not exceptional."""


class NoCalendar(Exception):
    """This workspace has not connected a calendar.

    Separate from an outage: nothing is broken and retrying will not help. The
    agent should stop offering times rather than apologise for a fault.
    """


@runtime_checkable
class Calendar(Protocol):
    """A workspace's bookable time."""

    async def availability(
        self,
        *,
        scope: CalendarScope,
        start: datetime,
        end: datetime,
        minutes: int,
    ) -> Availability:
        """Free slots of ``minutes`` between ``start`` and ``end``, soonest first.

        Raises :class:`NoCalendar` when the workspace has not connected one.
        """
        ...

    async def book(
        self, *, scope: CalendarScope, request: BookingRequest
    ) -> Booking:
        """
        Take the slot.

        Raises :class:`SlotTaken` if it has gone, :class:`NoCalendar` if there
        is nothing to book against. The calendar is the authority on whether a
        slot is free — an agent must not decide that from what it offered a
        moment ago.
        """
        ...
