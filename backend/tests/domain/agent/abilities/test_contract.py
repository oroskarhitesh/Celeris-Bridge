"""
Tests for the ability contract, grouped by who reads the declaration.

The property worth protecting: the JSON Schema the model is given and the
validator its arguments meet are built from the same declaration, so the model
can never be told a shape that would then be rejected.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from celeris_bridge.domain.agent.abilities import (
    Ability,
    AbilityContext,
    AbilityEffect,
    AbilityInputInvalid,
    AbilityResult,
    InputField,
    InvalidAbility,
    input_json_schema,
    input_specs,
    validate_input,
)
from celeris_bridge.domain.agent.definition_schema import AbilityContract


async def handler(arguments: Mapping[str, Any], context: AbilityContext) -> AbilityResult:
    return AbilityResult(output=dict(arguments))


def ability(**changed: Any) -> Ability:
    """A declaration that is valid, so a test can change one thing about it."""
    base: dict[str, Any] = {
        "key": "search_knowledge",
        "version": "1.0.0",
        "label": "Search company knowledge",
        "description": "Finds facts in the pages and documents this agent uses.",
        "ai_description": "Search the company's approved knowledge.",
        "effect": "read",
        "handler": handler,
    }
    return Ability(**{**base, **changed})


class TestDeclaringOne:
    def test_a_minimal_declaration_is_enough(self) -> None:
        declared = ability()
        assert declared.key == "search_knowledge"
        assert declared.effect is AbilityEffect.READ
        assert declared.input == {}
        assert declared.ends_turn is False

    def test_the_runner_gets_limits_whether_or_not_they_were_declared(self) -> None:
        # An ability with no stated limits still cannot hang a turn or be
        # called in a loop.
        assert ability().limits.timeout_ms == 5_000
        assert ability().limits.max_calls_per_turn == 3

    def test_a_declaration_cannot_be_changed_afterwards(self) -> None:
        declared = ability()
        with pytest.raises(Exception):
            declared.key = "something_else"  # type: ignore[misc]

    @pytest.mark.parametrize("key", ["Search", "s", "search-knowledge", "1search", ""])
    def test_a_key_that_is_not_a_tool_name_is_refused(self, key: str) -> None:
        # The key becomes the tool name sent to the model, so it has to be one.
        with pytest.raises(InvalidAbility):
            ability(key=key)

    @pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1"])
    def test_a_version_that_is_not_a_version_is_refused(self, version: str) -> None:
        with pytest.raises(InvalidAbility):
            ability(version=version)

    def test_an_ability_that_changes_something_needs_an_idempotency_key(self) -> None:
        # A tool call can be retried — by the runner, or by a model that did not
        # see the first result. Twice is not an option for a booking.
        with pytest.raises(InvalidAbility) as caught:
            ability(key="book_slot", effect="write")
        assert "idempotencyKey" in str(caught.value)

    def test_an_ability_that_only_reads_must_not_carry_one(self) -> None:
        with pytest.raises(InvalidAbility):
            ability(idempotency_key=lambda arguments, context: "k")

    def test_an_ability_that_changes_something_cannot_end_the_turn(self) -> None:
        with pytest.raises(InvalidAbility):
            ability(
                key="book_slot",
                effect="write",
                idempotency_key=lambda arguments, context: "k",
                ends_turn=True,
            )

    def test_declaring_both_a_fixed_and_a_dynamic_input_is_refused(self) -> None:
        # The reference silently ignores the fixed one, which leaves a
        # declaration that reads as if those arguments exist.
        with pytest.raises(InvalidAbility) as caught:
            ability(
                input={"query": {"type": "string", "description": "A query."}},
                input_for=lambda settings: {},
            )
        assert "inputFor" in str(caught.value)

    @pytest.mark.parametrize("name", ["1query", "a query", "", "a" * 70])
    def test_an_argument_name_that_is_not_an_identifier_is_refused(self, name: str) -> None:
        with pytest.raises(InvalidAbility):
            ability(input={name: {"type": "string", "description": "A query."}})

    def test_an_argument_must_tell_the_model_what_it_is_for(self) -> None:
        # Optional on a setting, where a label and a form do the explaining.
        # Compulsory here: the description is all the model gets.
        with pytest.raises(InvalidAbility):
            ability(input={"query": {"type": "string"}})

    def test_a_choice_argument_needs_choices(self) -> None:
        with pytest.raises(InvalidAbility):
            ability(input={"mode": {"type": "enum", "description": "Which mode."}})

    def test_a_list_argument_needs_a_row_shape(self) -> None:
        with pytest.raises(InvalidAbility):
            ability(input={"rows": {"type": "list", "description": "The rows."}})

    def test_a_constraint_that_does_not_belong_to_the_type_is_refused(self) -> None:
        with pytest.raises(InvalidAbility):
            ability(
                input={"count": {"type": "number", "description": "How many.", "maxLength": 5}}
            )

    def test_a_setting_whose_default_its_own_spec_would_reject_is_refused(self) -> None:
        with pytest.raises(InvalidAbility):
            ability(
                settings={
                    "maxResults": {
                        "label": "Results",
                        "type": "number",
                        "min": 1,
                        "default": 0,
                    }
                }
            )

    def test_every_kind_of_mistake_raises_the_same_exception(self) -> None:
        # A misspelled key is a structural error and a missing idempotency key
        # is a semantic one. One exception type, or every caller has to catch
        # two for one class of mistake.
        with pytest.raises(InvalidAbility):
            ability(key="Bad")
        with pytest.raises(InvalidAbility):
            ability(key="book_slot", effect="write")

    def test_the_error_names_the_ability_and_lists_every_problem(self) -> None:
        with pytest.raises(InvalidAbility) as caught:
            ability(
                key="book_slot",
                effect="write",
                ends_turn=True,
                input={"1bad": {"type": "string", "description": "d"}},
            )
        assert caught.value.key == "book_slot"
        assert len(caught.value.problems) == 3

    def test_a_declaration_satisfies_what_a_definition_checks_against(self) -> None:
        # definition_schema asks for a Mapping[str, AbilityContract]; this is
        # the assertion that an Ability is one.
        assert isinstance(ability(), AbilityContract)


class TestWhatTheModelIsGiven:
    def test_an_ability_with_no_arguments_still_gets_a_schema(self) -> None:
        assert input_json_schema(ability()) == {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    def test_the_schema_carries_the_description_and_the_limits(self) -> None:
        schema = input_json_schema(
            ability(
                input={
                    "query": {
                        "type": "string",
                        "description": "A clear, standalone search query.",
                        "required": True,
                        "maxLength": 500,
                    }
                }
            )
        )
        assert schema["required"] == ["query"]
        assert schema["properties"]["query"] == {
            "type": "string",
            "maxLength": 500,
            "description": "A clear, standalone search query.",
        }

    def test_unknown_arguments_are_closed_off_in_the_schema(self) -> None:
        # Matched by validate_input, which rejects them. Where the two disagree
        # a model invents a field, is told nothing, and invents it again.
        assert input_json_schema(ability())["additionalProperties"] is False

    def test_an_optional_argument_is_not_listed_as_required(self) -> None:
        schema = input_json_schema(
            ability(
                input={
                    "query": {"type": "string", "description": "A query.", "required": True},
                    "topic": {"type": "string", "description": "Optional narrowing."},
                }
            )
        )
        assert schema["required"] == ["query"]
        assert set(schema["properties"]) == {"query", "topic"}

    def test_a_string_with_no_stated_limit_is_still_bounded_for_the_model(self) -> None:
        # The schema has to state the same ceiling the validator enforces, or
        # the model writes 2,000 characters and is told it was too long.
        schema = input_json_schema(
            ability(input={"query": {"type": "string", "description": "A query."}})
        )
        limit = schema["properties"]["query"]["maxLength"]
        declared = ability(input={"query": {"type": "string", "description": "A query."}})
        with pytest.raises(AbilityInputInvalid):
            validate_input(declared, {"query": "x" * (limit + 1)})
        assert validate_input(declared, {"query": "x" * limit})["query"] == "x" * limit

    def test_a_choice_argument_reaches_the_model_as_an_enum(self) -> None:
        schema = input_json_schema(
            ability(
                input={
                    "mode": {
                        "type": "enum",
                        "description": "Which mode.",
                        "options": ["fast", "thorough"],
                    }
                }
            )
        )
        assert schema["properties"]["mode"] == {
            "type": "string",
            "enum": ["fast", "thorough"],
            "description": "Which mode.",
        }


class TestArgumentsBuiltFromTheAgentsSettings:
    """``input_for``: abilities whose arguments depend on how the agent is set up."""

    @staticmethod
    def from_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
        return {
            row["key"]: {
                "type": "string",
                "description": f"The visitor's answer to: {row['question']}",
                "required": bool(row["requiredToBook"]),
            }
            for row in settings.get("qualificationFields", [])
        }

    @pytest.fixture
    def save_details(self) -> Ability:
        return ability(
            key="save_visitor_details",
            effect="write",
            idempotency_key=lambda arguments, context: str(context.conversation_id),
            input_for=self.from_settings,
        )

    def test_the_arguments_follow_the_agents_configuration(self, save_details: Ability) -> None:
        schema = input_json_schema(
            save_details,
            {
                "qualificationFields": [
                    {"key": "full_name", "question": "Who are you?", "requiredToBook": True},
                    {"key": "timeline", "question": "When?", "requiredToBook": False},
                ]
            },
        )
        assert set(schema["properties"]) == {"full_name", "timeline"}
        assert schema["required"] == ["full_name"]

    def test_two_agents_get_two_different_schemas(self, save_details: Ability) -> None:
        one = input_json_schema(
            save_details,
            {"qualificationFields": [{"key": "email", "question": "Email?", "requiredToBook": True}]},
        )
        other = input_json_schema(
            save_details,
            {"qualificationFields": [{"key": "phone", "question": "Phone?", "requiredToBook": True}]},
        )
        assert set(one["properties"]) == {"email"}
        assert set(other["properties"]) == {"phone"}

    def test_what_the_ability_returned_is_never_written_back_into(self) -> None:
        # An ability may well be returning a module-level constant. Parsing it
        # in place would corrupt every later call.
        shared = {"query": {"type": "string", "description": "A query."}}
        declared = ability(input_for=lambda settings: shared)

        input_specs(declared)
        input_specs(declared)

        assert shared == {"query": {"type": "string", "description": "A query."}}

    def test_a_built_argument_is_checked_like_a_declared_one(self) -> None:
        declared = ability(input_for=lambda settings: {"query": {"type": "string"}})
        with pytest.raises(InvalidAbility):
            input_json_schema(declared)

    def test_a_built_name_that_is_not_an_identifier_is_refused(self) -> None:
        declared = ability(
            input_for=lambda settings: {"not a name": {"type": "string", "description": "d"}}
        )
        with pytest.raises(InvalidAbility):
            input_specs(declared)

    def test_returning_something_that_is_not_a_map_is_refused(self) -> None:
        declared = ability(input_for=lambda settings: ["query"])  # type: ignore[arg-type]
        with pytest.raises(InvalidAbility):
            input_specs(declared)

    def test_an_already_parsed_spec_is_accepted_as_it_is(self) -> None:
        spec = InputField(type="string", description="A query.", required=True)
        declared = ability(input_for=lambda settings: {"query": spec})
        assert input_specs(declared)["query"] is spec


class TestWhatTheModelSent:
    @pytest.fixture
    def searcher(self) -> Ability:
        return ability(
            input={
                "query": {
                    "type": "string",
                    "description": "A clear, standalone search query.",
                    "required": True,
                    "maxLength": 100,
                },
                "topic": {"type": "string", "description": "Optional narrowing."},
                "limit": {
                    "type": "number",
                    "description": "How many passages.",
                    "integer": True,
                    "min": 1,
                    "max": 10,
                },
            }
        )

    def test_good_arguments_come_back_normalised(self, searcher: Ability) -> None:
        # Normalising here, through the same function a settings form uses, is
        # what lets a handler trust what it is handed.
        assert validate_input(searcher, {"query": "  what is the pricing?  "}) == {
            "query": "what is the pricing?"
        }

    def test_a_missing_required_argument_is_named(self, searcher: Ability) -> None:
        with pytest.raises(AbilityInputInvalid) as caught:
            validate_input(searcher, {"topic": "pricing"})
        assert [(p.path, p.message) for p in caught.value.problems] == [
            ("query", "is required")
        ]

    def test_an_argument_the_tool_does_not_have_is_rejected_with_the_real_names(
        self, searcher: Ability
    ) -> None:
        # Telling the model only "that is wrong" earns another wrong guess.
        with pytest.raises(AbilityInputInvalid) as caught:
            validate_input(searcher, {"query": "pricing", "q": "pricing"})
        problem = caught.value.problems[0]
        assert problem.path == "q"
        assert "query" in problem.message

    def test_null_for_an_optional_argument_means_it_was_left_out(
        self, searcher: Ability
    ) -> None:
        # Models do this constantly. Rejecting it would be a retry loop over
        # nothing, and an argument has no "null means not chosen" state.
        assert validate_input(searcher, {"query": "pricing", "topic": None}) == {
            "query": "pricing"
        }

    def test_null_for_a_required_argument_is_still_missing(self, searcher: Ability) -> None:
        with pytest.raises(AbilityInputInvalid) as caught:
            validate_input(searcher, {"query": None})
        assert caught.value.problems[0].message == "is required"

    def test_every_problem_comes_back_at_once(self, searcher: Ability) -> None:
        # The whole list goes to the model as the tool result; one problem at a
        # time turns a single bad call into several round trips.
        with pytest.raises(AbilityInputInvalid) as caught:
            validate_input(searcher, {"limit": 99, "nope": 1})
        assert {p.path for p in caught.value.problems} == {"query", "limit", "nope"}

    def test_a_value_of_the_wrong_type_is_rejected(self, searcher: Ability) -> None:
        with pytest.raises(AbilityInputInvalid) as caught:
            validate_input(searcher, {"query": {"text": "pricing"}})
        assert caught.value.problems[0].message == "must be text"

    def test_no_arguments_at_all_is_the_same_as_an_empty_object(self) -> None:
        assert validate_input(ability(), None) == {}

    def test_arguments_that_are_not_an_object_are_rejected(self, searcher: Ability) -> None:
        with pytest.raises(AbilityInputInvalid) as caught:
            validate_input(searcher, "pricing")
        assert caught.value.problems[0].message == "arguments must be an object"

    def test_a_problem_inside_a_list_argument_says_which_row(self) -> None:
        declared = ability(
            input={
                "rows": {
                    "type": "list",
                    "description": "The details collected.",
                    "required": True,
                    "item": {
                        "key": {"label": "Key", "type": "string"},
                        "value": {"label": "Value", "type": "string", "maxLength": 5},
                    },
                }
            }
        )
        with pytest.raises(AbilityInputInvalid) as caught:
            validate_input(
                declared,
                {"rows": [{"key": "a", "value": "ok"}, {"key": "b", "value": "far too long"}]},
            )
        assert caught.value.problems[0].path == "rows[1].value"

    def test_the_message_names_the_ability(self, searcher: Ability) -> None:
        with pytest.raises(AbilityInputInvalid) as caught:
            validate_input(searcher, {})
        assert "search_knowledge" in str(caught.value)
        assert caught.value.key == "search_knowledge"


class TestTheResultAHandlerGivesBack:
    def test_a_result_defaults_to_empty_rather_than_absent(self) -> None:
        result = AbilityResult()
        assert result.output == {}
        assert result.sources == ()
        assert result.display is None
        assert result.meta == {}

    def test_a_context_carries_the_tenant_and_the_resolved_settings(self) -> None:
        context = AbilityContext(
            workspace_id="w1",
            agent_instance_id="a1",
            settings={"maxResults": 5},
            agent_settings={"tone": "friendly"},
        )
        assert context.workspace_id == "w1"
        assert context.settings["maxResults"] == 5
        with pytest.raises(Exception):
            context.workspace_id = "w2"  # type: ignore[misc]
