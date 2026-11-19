"""
Describing a time to a visitor, and getting the same time back again.

Two problems, one answer.

A model does not know what day it is and is poor at arithmetic on dates, so it
is never asked for one. ``find_slots`` takes "how many days from now", and this
module turns that into a window using the clock.

Coming back, a model must not be able to invent a time it was never offered —
it would read as a confirmed appointment that nobody holds. So every slot is
offered with an opaque reference that ``book_slot`` takes instead of a date.
Forging one is harmless: it decodes to a time the calendar then refuses, which
is the same path as a slot that was genuinely taken in the meantime.

This module is pure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from celeris_bridge.ports.calendar import Host, Slot

__all__ = [
    "SLOT_REFERENCE_PATTERN",
    "OfferedSlot",
    "describe",
    "offer",
    "read_reference",
    "reference_for",
    "window",
]

#: ``host~start~end``, each time a basic ISO instant. Given to the model as a
#: pattern so it copies one back rather than composing one.
SLOT_REFERENCE_PATTERN = r"^[A-Za-z0-9_-]{1,64}~\d{8}T\d{6}Z~\d{8}T\d{6}Z$"
_REFERENCE_RE = re.compile(SLOT_REFERENCE_PATTERN)
_COMPACT = "%Y%m%dT%H%M%SZ"
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


@dataclass(frozen=True)
class OfferedSlot:
    """One slot as the model and the visitor see it."""

    reference: str
    label: str
    host_name: str
    host_team: str = ""
    host_role: str = ""


def window(now: datetime, *, start_in_days: int, days: int) -> tuple[datetime, datetime]:
    """The range to search, from "in N days, for M days".

    Day 0 means from this moment, not from midnight — a visitor asking at 2pm
    for something today does not want this morning offered to them.
    """
    start = now if start_in_days <= 0 else _midnight_after(now, start_in_days)
    return start, start + timedelta(days=max(days, 1))


def _midnight_after(now: datetime, days: int) -> datetime:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(days=days)


def _zone(name: str) -> ZoneInfo | timezone:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # A workspace with a mistyped time zone still gets offered times; they
        # are simply in UTC, which is visible rather than silently wrong.
        return timezone.utc


def describe(moment: datetime, time_zone: str = "UTC") -> str:
    """``"Tue 12 Nov, 10:00"`` — in the workspace's zone, not the server's.

    Written out rather than taken from ``strftime("%a %d %b")``, whose output
    depends on the process locale. A visitor must not be shown a weekday in a
    language nobody chose.
    """
    local = moment.astimezone(_zone(time_zone))
    return (
        f"{_DAY_NAMES[local.weekday()]} {local.day} {_MONTH_NAMES[local.month - 1]}, "
        f"{local.hour:02d}:{local.minute:02d}"
    )


def reference_for(host_id: str, slot: Slot) -> str:
    """The opaque handle for one host at one slot."""
    return (
        f"{host_id}~{slot.start.astimezone(timezone.utc).strftime(_COMPACT)}"
        f"~{slot.end.astimezone(timezone.utc).strftime(_COMPACT)}"
    )


def read_reference(reference: str) -> tuple[str, datetime, datetime] | None:
    """``host_id, start, end``, or ``None`` if it is not one of ours."""
    if not _REFERENCE_RE.match(reference or ""):
        return None
    host_id, start, end = reference.split("~")
    try:
        return (
            host_id,
            datetime.strptime(start, _COMPACT).replace(tzinfo=timezone.utc),
            datetime.strptime(end, _COMPACT).replace(tzinfo=timezone.utc),
        )
    except ValueError:
        return None


def offer(
    slots: tuple[Slot, ...],
    hosts: Mapping[str, Host],
    *,
    time_zone: str = "UTC",
    limit: int = 12,
) -> tuple[OfferedSlot, ...]:
    """
    The slots as offers, one per slot.

    One host per slot, not all of them: a visitor choosing a time does not want
    to choose a person as well, and listing four names for each of twelve slots
    is a wall of text that ends with the model picking for them anyway.
    """
    offered: list[OfferedSlot] = []
    for slot in slots:
        host = next(
            (hosts[host_id] for host_id in slot.host_ids if host_id in hosts), None
        )
        if host is None:
            # A slot nobody can host is not a slot.
            continue
        offered.append(
            OfferedSlot(
                reference=reference_for(host.host_id, slot),
                label=describe(slot.start, time_zone),
                host_name=host.name,
                host_team=host.team,
                host_role=host.role,
            )
        )
        if len(offered) >= limit:
            break
    return tuple(offered)
