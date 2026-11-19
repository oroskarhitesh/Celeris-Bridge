"""
Tests for offering times and reading them back.

The property being protected: a booking can only ever name a time that was
offered. Everything else here is about a visitor reading a sensible date.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from celeris_bridge.domain.agent.engine.slots import (
    describe,
    offer,
    read_reference,
    reference_for,
    window,
)
from celeris_bridge.ports.calendar import Host, Slot

NOW = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)  # a Tuesday afternoon


def slot(hour: int = 10, *, day: int = 11, hosts: tuple[str, ...] = ("h1",)) -> Slot:
    start = datetime(2026, 11, day, hour, 0, tzinfo=timezone.utc)
    return Slot(start=start, end=start + timedelta(minutes=30), host_ids=hosts)


HOSTS = {
    "h1": Host(host_id="h1", name="Priya Raman", team="Sales", role="AE"),
    "h2": Host(host_id="h2", name="Sam Okafor", team="Sales"),
}


class TestTheSearchWindow:
    def test_today_starts_from_now_not_from_midnight(self) -> None:
        # A visitor asking at 2:30pm for something today does not want this
        # morning offered to them.
        start, end = window(NOW, start_in_days=0, days=1)
        assert start == NOW
        assert end == NOW + timedelta(days=1)

    def test_tomorrow_starts_from_midnight(self) -> None:
        start, _ = window(NOW, start_in_days=1, days=1)
        assert start == datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)

    def test_a_week_ahead_is_a_week_ahead(self) -> None:
        start, end = window(NOW, start_in_days=7, days=3)
        assert start == datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)
        assert end - start == timedelta(days=3)

    def test_a_nonsense_range_still_produces_a_window(self) -> None:
        start, end = window(NOW, start_in_days=-5, days=0)
        assert start == NOW
        assert end > start


class TestWritingOutATime:
    def test_a_time_reads_as_a_person_would_say_it(self) -> None:
        assert describe(slot(10).start) == "Wed 11 Nov, 10:00"

    def test_it_is_written_in_the_workspaces_zone(self) -> None:
        # 10:00 UTC is 15:30 in Kolkata. Offering "10:00" to an Indian
        # business would have people turning up at the wrong time.
        assert describe(slot(10).start, "Asia/Kolkata") == "Wed 11 Nov, 15:30"
        assert describe(slot(10).start, "America/New_York") == "Wed 11 Nov, 05:00"

    def test_a_mistyped_zone_falls_back_rather_than_failing(self) -> None:
        # A workspace with a bad time zone still gets offered times, in UTC,
        # which is visible rather than silently wrong.
        assert describe(slot(10).start, "Mars/Olympus") == "Wed 11 Nov, 10:00"

    def test_the_weekday_does_not_depend_on_the_servers_locale(self) -> None:
        # strftime("%a") would return a translated weekday on a machine with a
        # different locale, and a visitor must not read a day in a language
        # nobody chose.
        assert describe(slot(10).start).startswith("Wed")


class TestTheSlotReference:
    def test_it_survives_a_round_trip(self) -> None:
        one = slot(10)
        read = read_reference(reference_for("h1", one))
        assert read == ("h1", one.start, one.end)

    def test_two_different_slots_get_two_different_references(self) -> None:
        assert reference_for("h1", slot(10)) != reference_for("h1", slot(11))

    def test_two_hosts_at_one_time_get_two_references(self) -> None:
        assert reference_for("h1", slot(10)) != reference_for("h2", slot(10))

    @pytest.mark.parametrize(
        "forged",
        [
            "",
            "tomorrow at 10",
            "h1~2026-11-11T10:00:00Z~2026-11-11T10:30:00Z",
            "h1~20261111T100000Z",
            "h1~20261111T100000Z~20261111T103000Z~extra",
            "h1~20261311T100000Z~20261311T103000Z",
        ],
    )
    def test_something_the_model_made_up_is_not_read(self, forged: str) -> None:
        # The ability turns None into "that is not a time that was offered",
        # which is the difference between no appointment and an appointment the
        # visitor believes in and nobody holds.
        assert read_reference(forged) is None

    def test_a_reference_is_read_back_in_utc_whatever_zone_it_was_shown_in(self) -> None:
        one = slot(10)
        _, start, _ = read_reference(reference_for("h1", one))  # type: ignore[misc]
        assert start == one.start
        assert start.tzinfo == timezone.utc


class TestOfferingSlots:
    def test_each_slot_is_offered_with_a_host_and_a_reference(self) -> None:
        offers = offer((slot(10),), HOSTS)
        assert len(offers) == 1
        assert offers[0].host_name == "Priya Raman"
        assert offers[0].label == "Wed 11 Nov, 10:00"
        assert read_reference(offers[0].reference) is not None

    def test_one_host_per_slot_not_all_of_them(self) -> None:
        # A visitor choosing a time should not have to choose a person too.
        offers = offer((slot(10, hosts=("h1", "h2")),), HOSTS)
        assert len(offers) == 1
        assert offers[0].host_name == "Priya Raman"

    def test_a_slot_nobody_can_host_is_not_a_slot(self) -> None:
        assert offer((slot(10, hosts=("gone",)),), HOSTS) == ()

    def test_a_slot_with_no_hosts_at_all_is_dropped(self) -> None:
        assert offer((slot(10, hosts=()),), HOSTS) == ()

    def test_the_number_offered_is_capped(self) -> None:
        many = tuple(slot(hour) for hour in range(9, 18))
        assert len(offer(many, HOSTS, limit=3)) == 3

    def test_the_order_the_calendar_gave_is_kept(self) -> None:
        offers = offer((slot(9), slot(10), slot(11)), HOSTS, limit=2)
        assert [o.label for o in offers] == ["Wed 11 Nov, 09:00", "Wed 11 Nov, 10:00"]

    def test_a_hosts_team_and_role_come_through_when_they_exist(self) -> None:
        assert offer((slot(10),), HOSTS)[0].host_team == "Sales"
        assert offer((slot(10, hosts=("h2",)),), HOSTS)[0].host_role == ""
