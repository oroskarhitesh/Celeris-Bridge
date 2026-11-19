"""
Tests for ``find_slots``, ``book_slot`` and ``suggest_replies``.

The booking pair is where a model's judgement turns into something in a real
person's calendar, so most of this is about what cannot happen: a time nobody
offered, a second booking from one intention, a confirmation for a booking that
did not happen.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import pytest

from celeris_bridge.domain.agent.abilities import (
    AbilityContext,
    AbilityEffect,
    AbilityInputInvalid,
    input_json_schema,
    validate_input,
)
from celeris_bridge.domain.agent.abilities.builtins import (
    book_slot,
    builtin_abilities,
    find_slots,
    suggest_replies,
)
from celeris_bridge.domain.agent.engine.slots import reference_for
from celeris_bridge.ports.calendar import (
    Availability,
    Booking,
    BookingRequest,
    Calendar,
    CalendarScope,
    Clock,
    Host,
    NoCalendar,
    Slot,
    SlotTaken,
)

NOW = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
HOSTS = {"h1": Host(host_id="h1", name="Priya Raman", team="Sales", role="AE")}


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def slot(hour: int = 10, day: int = 11, hosts: tuple[str, ...] = ("h1",)) -> Slot:
    start = datetime(2026, 11, day, hour, 0, tzinfo=timezone.utc)
    return Slot(start=start, end=start + timedelta(minutes=30), host_ids=hosts)


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class FakeCalendar:
    def __init__(
        self,
        slots: Sequence[Slot] = (),
        *,
        time_zone: str = "UTC",
        raises: Exception | None = None,
        book_raises: Exception | None = None,
    ) -> None:
        self.slots = tuple(slots)
        self.time_zone = time_zone
        self.raises = raises
        self.book_raises = book_raises
        self.asked: list[dict[str, Any]] = []
        self.booked: list[BookingRequest] = []

    async def availability(
        self, *, scope: CalendarScope, start: datetime, end: datetime, minutes: int
    ) -> Availability:
        self.asked.append(
            {"scope": scope, "start": start, "end": end, "minutes": minutes}
        )
        if self.raises is not None:
            raise self.raises
        return Availability(time_zone=self.time_zone, slots=self.slots, hosts=HOSTS)

    async def book(self, *, scope: CalendarScope, request: BookingRequest) -> Booking:
        if self.book_raises is not None:
            raise self.book_raises
        self.booked.append(request)
        return Booking(
            booking_id=f"b{len(self.booked)}",
            host_id=request.host_id,
            start=request.start,
            end=request.end,
            time_zone=self.time_zone,
        )


@dataclass
class FakeServices:
    calendar: Any = None
    clock: Any = None


def ask_for_slots(
    arguments: Mapping[str, Any] | None = None,
    *,
    calendar: FakeCalendar | None = None,
    **settings: Any,
):
    calendar = calendar if calendar is not None else FakeCalendar((slot(10), slot(11)))
    context = AbilityContext(
        workspace_id="w1",
        agent_instance_id="a1",
        conversation_id="c1",
        settings={
            "meetingMinutes": 30,
            "daysAhead": 7,
            "maxSlotsOffered": 8,
            **settings,
        },
        services=FakeServices(calendar=calendar, clock=FixedClock()),
    )
    clean = validate_input(find_slots, arguments or {})
    return run(find_slots.handler(clean, context)), calendar


def ask_to_book(arguments: Mapping[str, Any], *, calendar: FakeCalendar | None = None):
    calendar = calendar if calendar is not None else FakeCalendar()
    context = AbilityContext(
        workspace_id="w1",
        agent_instance_id="a1",
        conversation_id="c1",
        settings={},
        services=FakeServices(calendar=calendar, clock=FixedClock()),
    )
    clean = validate_input(book_slot, arguments)
    return run(book_slot.handler(clean, context)), calendar


class TestFindingTimes:
    def test_the_build_offers_both_booking_abilities(self) -> None:
        registry = builtin_abilities()
        assert registry["find_slots"] is find_slots
        assert registry["book_slot"] is book_slot

    def test_the_model_is_never_asked_for_a_date(self) -> None:
        # It does not know today's date, and a date it works out itself is a
        # time the calendar never offered.
        schema = input_json_schema(find_slots)
        assert set(schema["properties"]) == {"startInDays", "days"}
        assert schema["required"] == []

    def test_free_times_come_back_written_out_for_the_visitor(self) -> None:
        result, _ = ask_for_slots()
        slots = result.output["slots"]
        assert slots[0]["when"] == "Wed 11 Nov, 10:00"
        assert slots[0]["with"] == "Priya Raman"
        assert slots[0]["team"] == "Sales"

    def test_the_window_comes_from_the_clock_not_the_model(self) -> None:
        _, calendar = ask_for_slots({"startInDays": 1, "days": 2})
        asked = calendar.asked[0]
        assert asked["start"] == datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)
        assert asked["end"] == datetime(2026, 11, 13, 0, 0, tzinfo=timezone.utc)

    def test_asking_for_nothing_in_particular_uses_the_workspaces_default(self) -> None:
        _, calendar = ask_for_slots({}, daysAhead=3)
        asked = calendar.asked[0]
        assert asked["start"] == NOW
        assert asked["end"] == NOW + timedelta(days=3)

    def test_the_meeting_length_is_the_workspaces(self) -> None:
        _, calendar = ask_for_slots({}, meetingMinutes=45)
        assert calendar.asked[0]["minutes"] == 45

    def test_the_tenant_comes_from_the_context(self) -> None:
        _, calendar = ask_for_slots()
        assert calendar.asked[0]["scope"] == CalendarScope(
            workspace_id="w1", agent_instance_id="a1"
        )

    def test_times_are_written_in_the_workspaces_zone(self) -> None:
        result, _ = ask_for_slots(
            calendar=FakeCalendar((slot(10),), time_zone="Asia/Kolkata")
        )
        assert result.output["slots"][0]["when"] == "Wed 11 Nov, 15:30"
        assert result.output["timeZone"] == "Asia/Kolkata"

    def test_how_many_are_offered_is_capped_by_the_setting(self) -> None:
        many = tuple(slot(hour) for hour in range(9, 18))
        result, _ = ask_for_slots(calendar=FakeCalendar(many), maxSlotsOffered=3)
        assert len(result.output["slots"]) == 3

    def test_each_time_carries_a_reference_the_model_must_copy(self) -> None:
        result, _ = ask_for_slots()
        assert result.output["slots"][0]["slotReference"] == reference_for("h1", slot(10))
        assert "never a time you worked out yourself" in result.output["note"]

    def test_nothing_free_is_said_plainly_with_what_to_do_about_it(self) -> None:
        result, _ = ask_for_slots(calendar=FakeCalendar(()))
        assert result.output["slots"] == []
        assert "Do not invent a time" in result.output["note"]

    def test_no_calendar_connected_is_not_treated_as_a_fault(self) -> None:
        # Nothing is broken and retrying will not help, so the agent should
        # stop offering times rather than apologise for an outage.
        result, _ = ask_for_slots(calendar=FakeCalendar(raises=NoCalendar()))
        assert result.output["slots"] == []
        assert "have someone follow up" in result.output["note"]
        assert "do not mention calendars" in result.output["note"].lower()


class TestBooking:
    def test_it_changes_something_so_it_carries_an_idempotency_key(self) -> None:
        assert book_slot.effect is AbilityEffect.WRITE
        assert book_slot.idempotency_key is not None

    def test_one_booking_a_message(self) -> None:
        # A model that wants two has misread something.
        assert book_slot.limits.max_calls_per_turn == 1

    def test_the_same_slot_asked_for_twice_is_one_booking(self) -> None:
        context = AbilityContext(workspace_id="w1", conversation_id="c1")
        key = book_slot.idempotency_key
        assert key is not None
        reference = reference_for("h1", slot(10))
        assert key({"slotReference": reference}, context) == key(
            {"slotReference": reference}, context
        )

    def test_a_different_slot_is_a_different_booking(self) -> None:
        context = AbilityContext(workspace_id="w1", conversation_id="c1")
        key = book_slot.idempotency_key
        assert key is not None
        assert key({"slotReference": reference_for("h1", slot(10))}, context) != key(
            {"slotReference": reference_for("h1", slot(11))}, context
        )

    def test_a_confirmed_time_is_booked_on_that_persons_calendar(self) -> None:
        result, calendar = ask_to_book(
            {
                "slotReference": reference_for("h1", slot(10)),
                "visitorName": "Priya Raman",
                "visitorEmail": "Priya@Acme.IO",
                "note": "wants to see the voice add-on",
            }
        )
        assert result.output["booked"] is True
        assert result.output["when"] == "Wed 11 Nov, 10:00"
        request = calendar.booked[0]
        assert request.host_id == "h1"
        assert request.start == slot(10).start
        assert request.visitor_name == "Priya Raman"
        # Normalised through the same check the qualification fields use.
        assert request.visitor_email == "priya@acme.io"
        assert request.note == "wants to see the voice add-on"

    def test_the_confirmation_is_in_the_workspaces_zone(self) -> None:
        result, _ = ask_to_book(
            {
                "slotReference": reference_for("h1", slot(10)),
                "visitorName": "Priya",
                "visitorEmail": "priya@acme.io",
            },
            calendar=FakeCalendar(time_zone="Asia/Kolkata"),
        )
        assert result.output["when"] == "Wed 11 Nov, 15:30"

    def test_a_time_that_was_never_offered_cannot_be_booked(self) -> None:
        # The pattern in the schema makes this unlikely; it is still the
        # difference between no appointment and an appointment the visitor
        # believes in and nobody holds.
        with pytest.raises(AbilityInputInvalid):
            validate_input(
                book_slot,
                {
                    "slotReference": "tomorrow at 10",
                    "visitorName": "Priya",
                    "visitorEmail": "priya@acme.io",
                },
            )

    def test_a_forged_reference_that_gets_past_the_pattern_is_still_refused(self) -> None:
        result, calendar = ask_to_book(
            {
                "slotReference": "h1~20261311T100000Z~20261311T103000Z",
                "visitorName": "Priya",
                "visitorEmail": "priya@acme.io",
            }
        )
        assert result.output["booked"] is False
        assert "not a time that was offered" in result.output["note"]
        assert calendar.booked == []

    def test_a_slot_taken_in_the_meantime_is_normal_not_an_error(self) -> None:
        # Two visitors can be looking at the same Tuesday.
        result, _ = ask_to_book(
            {
                "slotReference": reference_for("h1", slot(10)),
                "visitorName": "Priya",
                "visitorEmail": "priya@acme.io",
            },
            calendar=FakeCalendar(book_raises=SlotTaken()),
        )
        assert result.output["booked"] is False
        assert "call find_slots again" in result.output["note"]
        # And the agent must not claim the first one worked.
        assert "Do not tell the visitor the first one was booked" in result.output["note"]

    def test_an_unreachable_email_stops_the_booking(self) -> None:
        # The invitation would go nowhere, and the visitor would be told a
        # meeting exists that they have no way to join.
        result, calendar = ask_to_book(
            {
                "slotReference": reference_for("h1", slot(10)),
                "visitorName": "Priya",
                "visitorEmail": "priya at acme",
            }
        )
        assert result.output["booked"] is False
        assert "will not reach them" in result.output["note"]
        assert calendar.booked == []

    def test_with_no_calendar_nothing_is_booked_and_nothing_is_promised(self) -> None:
        result, _ = ask_to_book(
            {
                "slotReference": reference_for("h1", slot(10)),
                "visitorName": "Priya",
                "visitorEmail": "priya@acme.io",
            },
            calendar=FakeCalendar(book_raises=NoCalendar()),
        )
        assert result.output["booked"] is False
        assert "have someone follow up" in result.output["note"]

    def test_a_booking_that_worked_tells_the_agent_not_to_offer_another(self) -> None:
        result, _ = ask_to_book(
            {
                "slotReference": reference_for("h1", slot(10)),
                "visitorName": "Priya",
                "visitorEmail": "priya@acme.io",
            }
        )
        assert "Do not offer another time" in result.output["note"]

    def test_the_booking_id_is_kept_out_of_what_the_model_sees(self) -> None:
        result, _ = ask_to_book(
            {
                "slotReference": reference_for("h1", slot(10)),
                "visitorName": "Priya",
                "visitorEmail": "priya@acme.io",
            }
        )
        assert "bookingId" not in result.output
        assert result.meta["bookingId"] == "b1"


class TestSuggestingFollowUps:
    def ask(self, replies: Sequence[str], **settings: Any):
        context = AbilityContext(
            workspace_id="w1",
            conversation_id="c1",
            settings={"maxSuggestions": 3, "maxLength": 40, **settings},
        )
        clean = validate_input(suggest_replies, {"replies": list(replies)})
        return run(suggest_replies.handler(clean, context))

    def test_it_is_the_one_ability_that_ends_the_turn(self) -> None:
        assert suggest_replies.ends_turn is True
        assert all(
            not ability.ends_turn
            for key, ability in builtin_abilities().items()
            if key != "suggest_replies"
        )

    def test_short_distinct_suggestions_come_through(self) -> None:
        result = self.ask(["What does it cost?", "Book a demo"])
        assert result.display["quickReplies"] == ["What does it cost?", "Book a demo"]

    def test_too_many_are_trimmed(self) -> None:
        result = self.ask(["One", "Two", "Three", "Four"], maxSuggestions=2)
        assert result.display["quickReplies"] == ["One", "Two"]

    def test_one_that_is_too_long_to_be_a_button_is_dropped(self) -> None:
        result = self.ask(["Short", "x" * 60])
        assert result.display["quickReplies"] == ["Short"]

    def test_duplicates_are_dropped_however_they_are_cased(self) -> None:
        result = self.ask(["Book a demo", "book a demo", "Pricing"])
        assert result.display["quickReplies"] == ["Book a demo", "Pricing"]

    def test_a_lenient_input_still_produces_a_strict_output(self) -> None:
        # Refusing the model's list outright costs a round trip and gets a
        # similar list back. Ten are accepted, three are shown.
        result = self.ask([f"Suggestion number {n}" for n in range(10)])
        assert len(result.display["quickReplies"]) == 3

    def test_the_model_is_told_nothing_it_could_apologise_for(self) -> None:
        result = self.ask(["Short", "x" * 60])
        assert result.output == {"shown": 1}
