"""
Tests for qualification.

The decision these functions make is who the sales team calls. It must not vary
with how a sentence was phrased, and it must not be reachable by a visitor who
insists — so none of it is left to the model.
"""

from __future__ import annotations

from typing import Any

import pytest

from celeris_bridge.domain.agent.qualification import (
    MAX_DETAIL_CHARACTERS,
    Accepted,
    QualificationField,
    Rejected,
    VisitorStatus,
    assess,
    check_detail,
    fields_from,
    parse_quantity,
)


def field(**changed: Any) -> QualificationField:
    base: dict[str, Any] = {
        "key": "full_name",
        "question": "Who am I speaking with?",
        "required_to_book": True,
        "format": "text",
    }
    return QualificationField(**{**base, **changed})


NAME = field()
EMAIL = field(key="email", question="Best email?", format="email")
SIZE = field(key="company_size", question="How many people?", required_to_book=False, format="number")


class TestReadingTheFieldsOutOfSettings:
    def test_declared_rows_become_fields(self) -> None:
        fields = fields_from(
            {
                "qualificationFields": [
                    {
                        "key": "email",
                        "question": "Best email?",
                        "requiredToBook": True,
                        "format": "email",
                    }
                ]
            }
        )
        assert fields == (
            QualificationField(
                key="email", question="Best email?", required_to_book=True, format="email"
            ),
        )

    def test_the_declared_order_is_kept(self) -> None:
        # It is the order the admin arranged, and the order the agent is told
        # to ask in.
        fields = fields_from(
            {
                "qualificationFields": [
                    {"key": "b", "question": "B?", "requiredToBook": False},
                    {"key": "a", "question": "A?", "requiredToBook": False},
                ]
            }
        )
        assert [f.key for f in fields] == ["b", "a"]

    def test_an_agent_with_nothing_declared_has_no_fields(self) -> None:
        assert fields_from({}) == ()

    def test_a_row_without_a_key_is_skipped_rather_than_raising(self) -> None:
        # Reachable from a conversation that started before the definition
        # changed. A half-written row must not take the turn down.
        fields = fields_from(
            {"qualificationFields": [{"question": "?"}, {"key": "ok", "question": "Ok?"}]}
        )
        assert [f.key for f in fields] == ["ok"]

    def test_a_field_with_no_format_is_plain_text(self) -> None:
        assert fields_from({"qualificationFields": [{"key": "a", "question": "A?"}]})[0].format == "text"

    def test_a_field_with_no_question_falls_back_to_its_key(self) -> None:
        assert fields_from({"qualificationFields": [{"key": "role"}]})[0].question == "role"

    def test_settings_that_are_not_a_list_are_ignored(self) -> None:
        assert fields_from({"qualificationFields": "email"}) == ()


class TestReadingAQuantity:
    @pytest.mark.parametrize(
        "answer,expected",
        [
            ("40", 40),
            ("about 40", 40),
            ("40-50 people", 40),
            ("~12", 12),
            ("1.5k", 1500),
            ("1,500", 1500),
            ("we are 8 right now", 8),
            ("2k employees", 2000),
        ],
    )
    def test_prose_answers_are_understood(self, answer: str, expected: int) -> None:
        # Visitors answer "how many people?" in prose. Insisting on a bare
        # integer throws away most real answers.
        assert parse_quantity(answer) == expected

    @pytest.mark.parametrize("answer", ["lots", "", "a handful", "0", "zero"])
    def test_answers_with_no_number_in_them_are_not(self, answer: str) -> None:
        assert parse_quantity(answer) is None


class TestCheckingOneDetail:
    def test_text_is_tidied(self) -> None:
        assert check_detail(NAME, "  Priya   Raman \n") == Accepted("Priya Raman")

    def test_text_is_capped(self) -> None:
        outcome = check_detail(NAME, "x" * 1_000)
        assert isinstance(outcome, Accepted)
        assert len(outcome.value) == MAX_DETAIL_CHARACTERS

    def test_an_empty_answer_is_refused(self) -> None:
        assert isinstance(check_detail(NAME, "   "), Rejected)

    def test_an_email_is_stored_lower_case(self) -> None:
        assert check_detail(EMAIL, " Priya@Example.COM ") == Accepted("priya@example.com")

    @pytest.mark.parametrize("value", ["priya", "priya@", "@example.com", "priya@example", "a b@c.com"])
    def test_something_that_is_not_an_address_is_refused_with_a_next_step(
        self, value: str
    ) -> None:
        outcome = check_detail(EMAIL, value)
        assert isinstance(outcome, Rejected)
        # Phrased as an instruction: "invalid email" gets the same bad value
        # sent back again.
        assert "ask the visitor to check it" in outcome.reason

    def test_a_personal_address_is_refused_when_the_workspace_wants_work_ones(self) -> None:
        outcome = check_detail(EMAIL, "priya@gmail.com", work_email_only=True)
        assert isinstance(outcome, Rejected)
        assert "work email" in outcome.reason

    def test_a_work_address_is_accepted_either_way(self) -> None:
        assert check_detail(EMAIL, "priya@acme.io", work_email_only=True) == Accepted(
            "priya@acme.io"
        )

    def test_a_personal_address_is_fine_by_default(self) -> None:
        assert check_detail(EMAIL, "priya@gmail.com") == Accepted("priya@gmail.com")

    def test_a_number_field_keeps_what_the_visitor_said(self) -> None:
        # "about 40" is stored as written: it is what they told us. The parse
        # is only there to know it answers the question.
        assert check_detail(SIZE, "about 40") == Accepted("about 40")

    def test_a_number_field_refuses_an_answer_with_no_number(self) -> None:
        outcome = check_detail(SIZE, "quite a few")
        assert isinstance(outcome, Rejected)
        assert "needs a number" in outcome.reason

    def test_the_format_decides_the_check_and_not_the_field_name(self) -> None:
        # The reference checks `if (key === "email")`. Rename the field to
        # work_email and it silently starts accepting "yes please".
        renamed = field(key="work_email", question="Work email?", format="email")
        assert isinstance(check_detail(renamed, "not-an-address"), Rejected)

        # And the reverse: a field happening to be called email is not treated
        # as one unless it says so.
        misnamed = field(key="email", question="Email?", format="text")
        assert check_detail(misnamed, "not-an-address") == Accepted("not-an-address")


class TestWhereTheVisitorStands:
    def test_a_visitor_who_has_said_nothing_is_exploring(self) -> None:
        assert assess(fields=[NAME, EMAIL], details={}).status is VisitorStatus.EXPLORING

    def test_a_visitor_part_way_through_is_qualifying(self) -> None:
        assessment = assess(fields=[NAME, EMAIL], details={"full_name": "Priya"})
        assert assessment.status is VisitorStatus.QUALIFYING
        assert [f.key for f in assessment.missing] == ["email"]

    def test_a_visitor_who_has_given_everything_required_is_qualified(self) -> None:
        assessment = assess(
            fields=[NAME, EMAIL, SIZE],
            details={"full_name": "Priya", "email": "priya@acme.io"},
        )
        assert assessment.status is VisitorStatus.QUALIFIED
        assert assessment.missing == ()

    def test_an_optional_field_never_holds_anything_up(self) -> None:
        assessment = assess(fields=[SIZE], details={})
        assert assessment.status is VisitorStatus.QUALIFIED

    def test_a_workspace_that_asks_for_nothing_has_nothing_to_wait_for(self) -> None:
        assert assess(fields=[], details={}).status is VisitorStatus.QUALIFIED

    def test_the_missing_fields_come_back_in_declared_order(self) -> None:
        assessment = assess(fields=[NAME, EMAIL], details={})
        assert [f.key for f in assessment.missing] == ["full_name", "email"]

    def test_a_visitor_the_workspace_does_not_sell_to_is_not_a_fit(self) -> None:
        assessment = assess(
            fields=[NAME],
            details={"full_name": "Priya"},
            visitor_type="job_seeker",
            not_a_fit_types=["job_seeker", "competitor"],
        )
        assert assessment.status is VisitorStatus.NOT_A_FIT
        assert assessment.reasons == ("The visitor is a job seeker",)

    def test_not_a_fit_outranks_having_everything(self) -> None:
        # Otherwise a competitor who fills in every field is handed to sales.
        assessment = assess(
            fields=[NAME],
            details={"full_name": "A Rival"},
            visitor_type="competitor",
            not_a_fit_types=["competitor"],
        )
        assert assessment.status is VisitorStatus.NOT_A_FIT

    def test_not_knowing_who_someone_is_is_not_grounds_for_turning_them_away(self) -> None:
        # "unknown" is the default, so excluding it would reject every visitor
        # before they say anything.
        assessment = assess(
            fields=[NAME],
            details={},
            visitor_type="unknown",
            not_a_fit_types=["unknown", "competitor"],
        )
        assert assessment.status is not VisitorStatus.NOT_A_FIT

    def test_a_workspace_that_excludes_nobody_excludes_nobody(self) -> None:
        assessment = assess(
            fields=[NAME], details={"full_name": "A Rival"}, visitor_type="competitor"
        )
        assert assessment.status is VisitorStatus.QUALIFIED
