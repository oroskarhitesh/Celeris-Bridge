"""
Tests for ``save_visitor_details`` — the first ability that writes.

Three things matter here: the arguments the model is offered follow the
workspace's own fields; the model's report is checked before it is stored; and
the verdict on the visitor is never the model's to give.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pytest

from celeris_bridge.definitions import load_definition
from celeris_bridge.domain.agent.abilities import (
    AbilityContext,
    AbilityEffect,
    AbilityInputInvalid,
    input_json_schema,
    validate_input,
)
from celeris_bridge.domain.agent.abilities.builtins import (
    builtin_abilities,
    save_visitor_details,
)
from celeris_bridge.domain.agent.definition_schema import validate_definition
from celeris_bridge.domain.agent.overrides import resolve_config
from celeris_bridge.domain.agent.qualification import VisitorStatus
from celeris_bridge.ports.visitor import ConversationRef, VisitorRecord, VisitorRecords

FIELDS = [
    {
        "key": "full_name",
        "question": "Who am I speaking with?",
        "requiredToBook": True,
        "format": "text",
    },
    {
        "key": "email",
        "question": "What's the best email for the invite?",
        "requiredToBook": True,
        "format": "email",
    },
    {
        "key": "company_size",
        "question": "Roughly how many people are at your company?",
        "requiredToBook": False,
        "format": "number",
    },
]

AGENT_SETTINGS = {"qualificationFields": FIELDS}


class FakeVisitors:
    def __init__(self, record: VisitorRecord | None = None) -> None:
        self.record = record
        self.loaded: list[ConversationRef] = []
        self.written: list[tuple[ConversationRef, VisitorRecord]] = []

    async def load(self, ref: ConversationRef) -> VisitorRecord | None:
        self.loaded.append(ref)
        return self.record

    async def save(self, ref: ConversationRef, record: VisitorRecord) -> None:
        self.written.append((ref, record))
        self.record = record


@dataclass
class FakeServices:
    visitors: Any


def tell(
    reported: Mapping[str, Any],
    *,
    known: VisitorRecord | None = None,
    conversation_id: str | None = "c1",
    fields: Sequence[Mapping[str, Any]] = tuple(FIELDS),
    **settings: Any,
) -> tuple[Any, FakeVisitors]:
    """The model reports what the visitor said, and the ability deals with it."""
    store = FakeVisitors(known)
    context = AbilityContext(
        workspace_id="w1",
        agent_instance_id="a1",
        conversation_id=conversation_id,
        settings={
            "workEmailOnly": False,
            "notAFitVisitors": ["student", "job_seeker", "competitor"],
            **settings,
        },
        agent_settings={"qualificationFields": list(fields)},
        services=FakeServices(visitors=store),
    )
    arguments = validate_input(save_visitor_details, reported, context.agent_settings)
    result = asyncio.run(save_visitor_details.handler(arguments, context))
    return result, store


class TestTheDeclaration:
    def test_the_build_offers_it(self) -> None:
        assert builtin_abilities()["save_visitor_details"] is save_visitor_details

    def test_it_changes_something_so_a_retry_must_be_safe(self) -> None:
        assert save_visitor_details.effect is AbilityEffect.WRITE
        assert save_visitor_details.idempotency_key is not None

    def test_it_does_not_end_the_turn(self) -> None:
        # The agent still has to reply to whatever the visitor actually asked.
        assert save_visitor_details.ends_turn is False

    def test_twice_a_turn_is_enough(self) -> None:
        assert save_visitor_details.limits.max_calls_per_turn == 2

    def test_saying_the_same_thing_twice_is_one_answer(self) -> None:
        context = AbilityContext(workspace_id="w1", conversation_id="c1")
        assert save_visitor_details.idempotency_key is not None
        key = save_visitor_details.idempotency_key
        assert key({"email": "a@b.co"}, context) == key({"email": "a@b.co"}, context)

    def test_the_order_the_model_listed_them_in_does_not_matter(self) -> None:
        context = AbilityContext(workspace_id="w1", conversation_id="c1")
        key = save_visitor_details.idempotency_key
        assert key is not None
        assert key({"a": "1", "b": "2"}, context) == key({"b": "2", "a": "1"}, context)

    def test_a_different_answer_is_a_different_call(self) -> None:
        context = AbilityContext(workspace_id="w1", conversation_id="c1")
        key = save_visitor_details.idempotency_key
        assert key is not None
        assert key({"email": "a@b.co"}, context) != key({"email": "c@d.co"}, context)

    def test_the_same_answer_in_another_conversation_is_another_call(self) -> None:
        key = save_visitor_details.idempotency_key
        assert key is not None
        one = key({"email": "a@b.co"}, AbilityContext(workspace_id="w1", conversation_id="c1"))
        other = key({"email": "a@b.co"}, AbilityContext(workspace_id="w1", conversation_id="c2"))
        assert one != other


class TestTheArgumentsTheModelIsOffered:
    def test_they_are_the_workspaces_own_fields(self) -> None:
        schema = input_json_schema(save_visitor_details, AGENT_SETTINGS)
        assert set(schema["properties"]) == {
            "full_name",
            "email",
            "company_size",
            "visitorType",
        }

    def test_none_of_them_are_required(self) -> None:
        # A required argument pushes the model into inventing the rest.
        assert input_json_schema(save_visitor_details, AGENT_SETTINGS)["required"] == []

    def test_the_description_is_the_question_the_agent_asked(self) -> None:
        schema = input_json_schema(save_visitor_details, AGENT_SETTINGS)
        assert "Who am I speaking with?" in schema["properties"]["full_name"]["description"]

    def test_a_number_field_is_still_offered_as_text(self) -> None:
        # Visitors answer "about 40". A number-typed argument leaves the model
        # guessing a figure or dropping the answer.
        schema = input_json_schema(save_visitor_details, AGENT_SETTINGS)
        assert schema["properties"]["company_size"]["type"] == "string"

    def test_who_the_visitor_is_comes_from_a_fixed_list(self) -> None:
        schema = input_json_schema(save_visitor_details, AGENT_SETTINGS)
        assert "competitor" in schema["properties"]["visitorType"]["enum"]

    def test_two_workspaces_are_offered_different_arguments(self) -> None:
        other = input_json_schema(
            save_visitor_details,
            {"qualificationFields": [{"key": "phone", "question": "Phone?", "requiredToBook": True}]},
        )
        assert set(other["properties"]) == {"phone", "visitorType"}

    def test_a_field_the_workspace_does_not_collect_is_refused(self) -> None:
        with pytest.raises(AbilityInputInvalid):
            validate_input(save_visitor_details, {"salary": "100k"}, AGENT_SETTINGS)


class TestWhatGetsStored:
    def test_what_the_visitor_said_is_written_down(self) -> None:
        result, store = tell({"full_name": "  Priya  Raman ", "email": "Priya@Acme.IO"})
        assert sorted(result.output["saved"]) == ["email", "full_name"]
        assert store.written[0][1].details == {
            "full_name": "Priya Raman",
            "email": "priya@acme.io",
        }

    def test_it_is_merged_with_what_was_already_known(self) -> None:
        result, store = tell(
            {"email": "priya@acme.io"},
            known=VisitorRecord(details={"full_name": "Priya Raman"}, status="qualifying"),
        )
        assert store.written[0][1].details == {
            "full_name": "Priya Raman",
            "email": "priya@acme.io",
        }
        assert result.output["saved"] == ["email"]

    def test_a_bad_answer_comes_back_with_something_to_do_about_it(self) -> None:
        result, store = tell({"full_name": "Priya", "email": "priya at acme"})
        assert result.output["saved"] == ["full_name"]
        assert result.output["rejected"] == [
            {
                "field": "email",
                "reason": "That does not look like an email address — ask the visitor to check it",
            }
        ]
        # The good half is still kept: the visitor should not have to repeat
        # their name because they mistyped their address.
        assert store.written[0][1].details == {"full_name": "Priya"}

    def test_the_workspaces_work_email_rule_is_applied(self) -> None:
        result, _ = tell({"email": "priya@gmail.com"}, workEmailOnly=True)
        assert result.output["saved"] == []
        assert "work email" in result.output["rejected"][0]["reason"]

    def test_and_is_off_unless_the_workspace_asks_for_it(self) -> None:
        result, _ = tell({"email": "priya@gmail.com"})
        assert result.output["saved"] == ["email"]

    def test_the_record_is_attached_to_the_right_conversation(self) -> None:
        _, store = tell({"full_name": "Priya"})
        assert store.loaded == [ConversationRef(workspace_id="w1", conversation_id="c1")]
        assert store.written[0][0] == ConversationRef(workspace_id="w1", conversation_id="c1")

    def test_a_call_that_changes_nothing_is_not_written(self) -> None:
        _, store = tell(
            {"email": "priya at acme"},
            known=VisitorRecord(
                details={"full_name": "Priya"},
                missing=("email",),
                status="qualifying",
            ),
        )
        assert store.written == []

    def test_learning_only_who_someone_is_still_counts_as_a_change(self) -> None:
        _, store = tell(
            {"visitorType": "prospect"},
            known=VisitorRecord(details={"full_name": "Priya"}, missing=("email",), status="qualifying"),
        )
        assert store.written[0][1].visitor_type == "prospect"

    def test_who_the_visitor_is_is_remembered_between_calls(self) -> None:
        _, store = tell(
            {"full_name": "Priya"},
            known=VisitorRecord(visitor_type="prospect"),
        )
        assert store.written[0][1].visitor_type == "prospect"

    def test_the_derived_verdict_is_stored_alongside_the_facts(self) -> None:
        # So a leads list can filter on status without re-running the rules
        # over every conversation it displays.
        _, store = tell({"full_name": "Priya", "email": "priya@acme.io"})
        record = store.written[0][1]
        assert record.status == VisitorStatus.QUALIFIED
        assert record.missing == ()

    def test_nothing_is_stored_outside_a_conversation(self) -> None:
        result, store = tell({"full_name": "Priya"}, conversation_id=None)
        assert store.written == []
        assert result.output["saved"] == []
        assert "nothing was saved" in result.output["note"]


class TestWhatTheModelIsToldBack:
    def test_a_half_finished_visitor_is_told_what_is_still_needed(self) -> None:
        result, _ = tell({"full_name": "Priya"})
        assert result.output["status"] == "qualifying"
        # The question, not the key: the model has to ask it out loud.
        assert result.output["stillNeeded"] == ["What's the best email for the invite?"]
        assert "at most one" in result.output["note"]

    def test_a_finished_visitor_is_told_not_to_promise_anything(self) -> None:
        result, _ = tell({"full_name": "Priya", "email": "priya@acme.io"})
        assert result.output["status"] == "qualified"
        assert "do not say that anything is booked" in result.output["note"].lower()

    def test_a_visitor_who_is_not_a_fit_is_still_to_be_helped(self) -> None:
        result, _ = tell({"full_name": "A Rival", "visitorType": "competitor"})
        assert result.output["status"] == "not_a_fit"
        note = result.output["note"]
        assert "Keep helping" in note
        assert "do not push for a demo" in note.lower()
        # And the agent must not invent a policy to explain itself.
        assert "there is not one" in note

    def test_the_reason_is_passed_on_so_the_agent_does_not_contradict_it(self) -> None:
        result, _ = tell({"visitorType": "job_seeker"})
        assert "job seeker" in result.output["note"]

    def test_the_verdict_is_not_the_models_to_give(self) -> None:
        # There is no "status" argument. The model reports what was said; the
        # rules decide what it means.
        assert "status" not in input_json_schema(save_visitor_details, AGENT_SETTINGS)["properties"]

    def test_a_visitor_type_the_workspace_welcomes_is_not_turned_away(self) -> None:
        result, _ = tell(
            {"full_name": "Priya", "email": "priya@acme.io", "visitorType": "existing_customer"}
        )
        assert result.output["status"] == "qualified"

    def test_telemetry_says_what_happened(self) -> None:
        result, _ = tell({"full_name": "Priya"})
        assert result.meta == {"status": "qualifying", "detailsKnown": 1, "written": True}


class TestWhenTheSettingsDisagree:
    def test_a_field_the_arguments_were_checked_against_but_the_handler_has_not(
        self,
    ) -> None:
        # Normally unreachable: a runner validates and runs against one set of
        # settings, so validate_input refuses an unknown field long before the
        # handler sees it. This constructs the one case that gets past it — two
        # different settings snapshots — because the alternative to handling it
        # is a KeyError that takes the whole turn down.
        store = FakeVisitors()
        context = AbilityContext(
            workspace_id="w1",
            conversation_id="c1",
            settings={"workEmailOnly": False, "notAFitVisitors": []},
            # The admin dropped full_name after the arguments were checked.
            agent_settings={"qualificationFields": [FIELDS[1]]},
            services=FakeServices(visitors=store),
        )
        arguments = validate_input(
            save_visitor_details, {"full_name": "Priya"}, AGENT_SETTINGS
        )
        result = asyncio.run(save_visitor_details.handler(arguments, context))

        assert result.output["rejected"] == [
            {"field": "full_name", "reason": '"full_name" is not something this company collects'}
        ]
        assert result.output["saved"] == []
        assert store.written == []


class TestInTheShippedDefinition:
    def test_the_marketing_agent_switches_it_on(self) -> None:
        registry = builtin_abilities()
        report = validate_definition(load_definition("marketing_v1"), abilities=registry)
        assert report.ok
        assert report.definition is not None
        config = resolve_config(report.definition, None, abilities=registry)
        resolved = config.ability("save_visitor_details")
        assert resolved is not None
        assert resolved.enabled is True

    def test_its_settings_resolve_to_everything_the_handler_reads(self) -> None:
        registry = builtin_abilities()
        definition = validate_definition(
            load_definition("marketing_v1"), abilities=registry
        ).definition
        assert definition is not None
        resolved = resolve_config(definition, None, abilities=registry).ability(
            "save_visitor_details"
        )
        assert resolved is not None
        assert set(resolved.settings) == {"workEmailOnly", "notAFitVisitors"}

    def test_the_fields_it_offers_are_the_ones_the_definition_declares(self) -> None:
        registry = builtin_abilities()
        definition = validate_definition(
            load_definition("marketing_v1"), abilities=registry
        ).definition
        assert definition is not None
        settings = resolve_config(definition, None, abilities=registry).settings
        schema = input_json_schema(save_visitor_details, settings)
        assert set(schema["properties"]) == {
            "full_name",
            "email",
            "company_size",
            "timeline",
            "visitorType",
        }

    def test_the_port_is_satisfied_structurally(self) -> None:
        assert isinstance(FakeVisitors(), VisitorRecords)
