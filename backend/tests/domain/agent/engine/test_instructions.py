"""
Tests for prompt assembly.

The prompt is the product. Everything in the definition exists to end up here,
in the right order, with the parts that do not apply left out.
"""

from __future__ import annotations

from typing import Any

import pytest

from celeris_bridge.definitions import load_definition
from celeris_bridge.domain.agent.abilities.builtins import builtin_abilities
from celeris_bridge.domain.agent.definition_schema import (
    AgentDefinition,
    InstructionCondition,
    validate_definition,
)
from celeris_bridge.domain.agent.engine.instructions import (
    AbilityBrief,
    PageContext,
    build_prompt,
    fill,
    is_empty,
    section_applies,
)
from celeris_bridge.domain.agent.overrides import apply_override_patch, resolve_config
from celeris_bridge.ports.visitor import VisitorRecord

REGISTRY = builtin_abilities()


def definition_of(raw: dict[str, Any]) -> AgentDefinition:
    report = validate_definition(raw, abilities=REGISTRY)
    assert report.definition is not None, [f"{p.path}: {p.message}" for p in report.problems]
    assert report.ok, [f"{p.path}: {p.message}" for p in report.problems]
    return report.definition


def small(**changed: Any) -> AgentDefinition:
    """A definition with just enough in it to see one thing at a time."""
    raw: dict[str, Any] = {
        "identity": {"name": "Test", "channels": ["web_chat"]},
        "settings": {
            "tone": {
                "label": "Tone",
                "type": "enum",
                "options": ["friendly", "formal"],
                "default": "friendly",
                "editable": True,
            },
            "blurb": {
                "label": "Blurb",
                "type": "string",
                "default": "",
                "editable": True,
            },
            "chatty": {
                "label": "Chatty",
                "type": "boolean",
                "default": True,
                "editable": True,
            },
            "topics": {
                "label": "Topics",
                "type": "stringList",
                "default": [],
                "editable": True,
            },
        },
        "instructions": [{"id": "role", "title": "Role", "text": "You help {{company}}."}],
        **changed,
    }
    return definition_of(raw)


def prompt_for(definition: AgentDefinition, **changed: Any):
    settings = {key: spec.default for key, spec in definition.settings.items()}
    settings.update(changed.pop("settings", {}))
    return build_prompt(
        definition=definition,
        settings=settings,
        company=changed.pop("company", "Acme"),
        **changed,
    )


class TestEmptiness:
    @pytest.mark.parametrize("value", [None, "", "   ", [], {}])
    def test_nothing_is_empty(self, value: Any) -> None:
        assert is_empty(value) is True

    @pytest.mark.parametrize("value", [False, 0, 0.0, "x", [""], {"a": 1}])
    def test_a_value_is_not(self, value: Any) -> None:
        # False and 0 are answers. Treating them as emptiness would drop the
        # block that explains what to do when a toggle is off.
        assert is_empty(value) is False


class TestWhetherABlockApplies:
    def test_a_block_with_no_condition_always_does(self) -> None:
        assert section_applies(None, {}) is True

    def test_is_compares_the_value(self) -> None:
        condition = InstructionCondition(setting="tone", **{"is": "formal"})
        assert section_applies(condition, {"tone": "formal"}) is True
        assert section_applies(condition, {"tone": "friendly"}) is False

    def test_a_boolean_is_not_confused_with_a_number(self) -> None:
        # True == 1 in Python. A condition written against the number 1 must
        # not fire for a boolean setting that happens to be on.
        on = InstructionCondition(setting="chatty", **{"is": True})
        assert section_applies(on, {"chatty": True}) is True
        assert section_applies(on, {"chatty": 1}) is False

        one = InstructionCondition(setting="count", **{"is": 1})
        assert section_applies(one, {"count": True}) is False

    def test_not_empty_fires_when_there_is_something(self) -> None:
        condition = InstructionCondition(setting="blurb", notEmpty=True)
        assert section_applies(condition, {"blurb": "We sell things"}) is True
        assert section_applies(condition, {"blurb": "  "}) is False

    def test_empty_fires_when_there_is_nothing(self) -> None:
        condition = InstructionCondition(setting="blurb", empty=True)
        assert section_applies(condition, {"blurb": ""}) is True
        assert section_applies(condition, {"blurb": "We sell things"}) is False

    def test_a_setting_that_is_not_there_counts_as_empty(self) -> None:
        assert section_applies(InstructionCondition(setting="gone", empty=True), {}) is True
        assert section_applies(InstructionCondition(setting="gone", notEmpty=True), {}) is False

    def test_a_condition_needs_exactly_one_test(self) -> None:
        with pytest.raises(Exception):
            InstructionCondition(setting="blurb")
        with pytest.raises(Exception):
            InstructionCondition(setting="blurb", notEmpty=True, empty=True)
        with pytest.raises(Exception):
            InstructionCondition(setting="blurb", empty=True, **{"is": ""})


class TestFillingInPlaceholders:
    def test_a_setting_is_rendered_for_prose(self) -> None:
        definition = small()
        assert fill("Be {{tone}}.", {"tone": "formal"}, definition.settings) == "Be formal."

    def test_a_boolean_reads_as_yes_or_no(self) -> None:
        definition = small()
        assert (
            fill("Chatty: {{chatty}}", {"chatty": False}, definition.settings)
            == "Chatty: no"
        )

    def test_a_list_becomes_bullets(self) -> None:
        definition = small()
        assert fill(
            "Avoid:\n{{topics}}", {"topics": ["price", "legal"]}, definition.settings
        ) == "Avoid:\n- price\n- legal"

    def test_the_company_name_has_no_spec_and_is_still_filled(self) -> None:
        assert fill("You help {{company}}.", {"company": "Acme"}) == "You help Acme."

    def test_whitespace_inside_the_braces_is_allowed(self) -> None:
        assert fill("You help {{  company  }}.", {"company": "Acme"}) == "You help Acme."

    def test_every_occurrence_is_replaced(self) -> None:
        assert fill("{{company}} and {{company}}", {"company": "Acme"}) == "Acme and Acme"

    def test_a_name_with_no_value_is_left_visible(self) -> None:
        # validate_definition refuses to publish one, so this cannot reach a
        # customer. Leaving the braces makes the mistake obvious in a draft
        # instead of silently dropping a sentence's subject.
        assert fill("You help {{nobody}}.", {"company": "Acme"}) == "You help {{nobody}}."

    def test_a_value_containing_braces_is_not_re_expanded(self) -> None:
        # A workspace typing {{company}} into a text setting must not get the
        # company name — it is data, not template.
        definition = small()
        filled = fill(
            "About: {{blurb}}",
            {"blurb": "We make {{company}} widgets", "company": "Acme"},
            definition.settings,
        )
        assert filled == "About: We make {{company}} widgets"


class TestTheOrderOfTheSections:
    def test_blocks_come_first_and_rules_last(self) -> None:
        definition = small(
            instructions=[{"id": "role", "title": "Role", "text": "You help {{company}}."}],
            lockedRules=[{"id": "honest", "text": "Never invent a number."}],
        )
        prompt = prompt_for(
            definition,
            abilities=[AbilityBrief(name="search_knowledge", instructions="Search first.")],
            page=PageContext(url="https://acme.test/pricing"),
            visitor=VisitorRecord(),
        )
        order = [
            prompt.text.index("You help Acme."),
            prompt.text.index("## Abilities you can use"),
            prompt.text.index("## Where the visitor is"),
            prompt.text.index("## Rules you must always follow"),
        ]
        assert order == sorted(order)

    def test_the_rules_say_they_override_what_came_before(self) -> None:
        # They are last so the model reads them most recently, and they say so
        # in as many words — a workspace's own wording sits above them.
        definition = small(lockedRules=[{"id": "honest", "text": "Never invent a number."}])
        prompt = prompt_for(definition, company="Globex")
        assert "These override everything above" in prompt.text
        assert "anything Globex has told you" in prompt.text

    def test_nothing_optional_is_added_when_nothing_optional_is_passed(self) -> None:
        prompt = prompt_for(small())
        assert prompt.text == "You help Acme."
        assert "##" not in prompt.text


class TestWhichBlocksWereUsed:
    def test_a_block_that_does_not_apply_is_reported_as_skipped(self) -> None:
        # "Why is the agent not mentioning the booking link?" is the most
        # common question about a configured agent, and the answer is always
        # a block that did not apply.
        definition = small(
            instructions=[
                {"id": "role", "title": "Role", "text": "You help {{company}}."},
                {
                    "id": "about",
                    "title": "About",
                    "text": "About: {{blurb}}",
                    "when": {"setting": "blurb", "notEmpty": True},
                },
            ]
        )
        prompt = prompt_for(definition)
        assert prompt.used == ("role",)
        assert prompt.skipped == ("about",)
        assert "About:" not in prompt.text

    def test_a_block_that_applies_is_rendered_and_reported(self) -> None:
        definition = small(
            instructions=[
                {
                    "id": "about",
                    "title": "About",
                    "text": "About: {{blurb}}",
                    "when": {"setting": "blurb", "notEmpty": True},
                }
            ]
        )
        prompt = prompt_for(definition, settings={"blurb": "We sell things"})
        assert prompt.used == ("about",)
        assert prompt.text == "About: We sell things"

    def test_the_empty_case_can_now_be_written(self) -> None:
        # The gap found while authoring the definition: saying something
        # *because* a setting is unset.
        definition = small(
            instructions=[
                {
                    "id": "no_blurb",
                    "title": "Fallback",
                    "text": "You have no description of the company. Keep to generalities.",
                    "when": {"setting": "blurb", "empty": True},
                }
            ]
        )
        assert "Keep to generalities" in prompt_for(definition).text
        assert prompt_for(definition, settings={"blurb": "We sell"}).text == ""

    def test_a_block_that_renders_to_nothing_is_skipped(self) -> None:
        # Its whole body was a placeholder for an empty setting; the author
        # forgot the condition. A blank heading is worse than a missing one.
        definition = small(
            instructions=[{"id": "about", "title": "About", "text": "{{blurb}}"}]
        )
        prompt = prompt_for(definition)
        assert prompt.skipped == ("about",)
        assert prompt.text == ""


class TestTheAbilitiesSection:
    def test_each_ability_contributes_its_own_instructions(self) -> None:
        prompt = prompt_for(
            small(),
            abilities=[
                AbilityBrief(name="search_knowledge", instructions="Search before answering."),
                AbilityBrief(name="save_visitor_details", instructions="Save what they say."),
            ],
        )
        assert "- search_knowledge: Search before answering." in prompt.text
        assert "- save_visitor_details: Save what they say." in prompt.text

    def test_an_ability_with_nothing_to_say_is_left_out(self) -> None:
        prompt = prompt_for(small(), abilities=[AbilityBrief(name="quiet")])
        assert "## Abilities you can use" not in prompt.text

    def test_the_real_abilities_bring_their_own_wording(self) -> None:
        registry = builtin_abilities()
        prompt = prompt_for(
            small(),
            abilities=[
                AbilityBrief(name=key, instructions=registry[key].instructions)
                for key in sorted(registry)
            ],
        )
        assert "standalone query" in prompt.text
        assert "never fill a field from a guess" in prompt.text


class TestTheVisitorSection:
    @pytest.fixture
    def definition(self) -> AgentDefinition:
        return definition_of(
            {
                "identity": {"name": "Test", "channels": ["web_chat"]},
                "settings": {
                    "qualificationFields": {
                        "label": "What to find out",
                        "type": "list",
                        "uniqueBy": "key",
                        "editable": True,
                        "item": {
                            "key": {"label": "Key", "type": "string"},
                            "question": {"label": "Question", "type": "string"},
                            "requiredToBook": {"label": "Required", "type": "boolean"},
                        },
                        "default": [
                            {"key": "full_name", "question": "Your name?", "requiredToBook": True},
                            {"key": "email", "question": "Your email?", "requiredToBook": True},
                        ],
                    }
                },
                "instructions": [{"id": "role", "title": "Role", "text": "You help {{company}}."}],
            }
        )

    def test_what_is_already_known_is_listed_as_not_to_be_asked_again(
        self, definition: AgentDefinition
    ) -> None:
        # An agent asking for an email it was given two turns ago reads as not
        # listening, and that is the complaint visitors actually make.
        prompt = prompt_for(
            definition,
            visitor=VisitorRecord(
                details={"full_name": "Priya"}, status="qualifying", missing=("email",)
            ),
        )
        assert "do not ask again" in prompt.text
        assert "Your name? Priya" in prompt.text

    def test_what_is_still_needed_is_listed_as_a_question(
        self, definition: AgentDefinition
    ) -> None:
        prompt = prompt_for(
            definition,
            visitor=VisitorRecord(details={"full_name": "Priya"}, status="qualifying", missing=("email",)),
        )
        assert "Still to find out:\n- Your email?" in prompt.text

    def test_a_visitor_who_has_said_nothing_is_described_as_such(
        self, definition: AgentDefinition
    ) -> None:
        prompt = prompt_for(definition, visitor=VisitorRecord())
        assert "not shared anything" in prompt.text
        assert "answered none of your questions" in prompt.text

    def test_a_visitor_who_is_not_a_fit_is_not_still_being_qualified(
        self, definition: AgentDefinition
    ) -> None:
        # Asking a competitor for their email after deciding not to sell to
        # them is the worst of both outcomes.
        prompt = prompt_for(
            definition,
            visitor=VisitorRecord(
                details={"full_name": "A Rival"},
                status="not_a_fit",
                missing=("email",),
                reasons=("The visitor is a competitor",),
            ),
        )
        assert "Still to find out" not in prompt.text
        assert "Why: The visitor is a competitor." in prompt.text

    def test_a_stored_status_that_no_longer_exists_does_not_break_the_turn(
        self, definition: AgentDefinition
    ) -> None:
        prompt = prompt_for(definition, visitor=VisitorRecord(status="whatever"))
        assert "## What you know about this visitor" in prompt.text

    def test_an_agent_that_collects_nothing_gets_no_section(self) -> None:
        prompt = prompt_for(small(), visitor=VisitorRecord())
        assert "## What you know about this visitor" not in prompt.text

    def test_no_section_at_all_when_no_visitor_is_passed(
        self, definition: AgentDefinition
    ) -> None:
        # A voice or email channel has no visitor record to speak of.
        assert "## What you know" not in prompt_for(definition).text


class TestThePageSection:
    def test_the_title_and_url_are_both_offered(self) -> None:
        prompt = prompt_for(
            small(), page=PageContext(url="https://acme.test/pricing", title="Pricing")
        )
        assert "Page title: Pricing" in prompt.text
        assert "Page URL: https://acme.test/pricing" in prompt.text

    def test_the_agent_is_told_not_to_bring_it_up(self) -> None:
        prompt = prompt_for(small(), page=PageContext(title="Pricing"))
        assert "Do not mention it unprompted" in prompt.text

    def test_an_empty_page_context_adds_nothing(self) -> None:
        assert "## Where the visitor is" not in prompt_for(small(), page=PageContext()).text

    def test_a_very_long_title_is_cut(self) -> None:
        prompt = prompt_for(small(), page=PageContext(title="x" * 1_000))
        assert "x" * 200 in prompt.text
        assert "x" * 201 not in prompt.text


class TestTheShippedMarketingAgent:
    @pytest.fixture
    def definition(self) -> AgentDefinition:
        return definition_of(load_definition("marketing_v1"))

    def test_a_brand_new_workspace_gets_a_prompt_with_no_gaps_in_it(
        self, definition: AgentDefinition
    ) -> None:
        config = resolve_config(definition, None, abilities=REGISTRY)
        prompt = build_prompt(
            definition=definition,
            settings=config.settings,
            locked_rules=config.locked_rules,
            company="Acme",
        )
        assert prompt.used == (
            "role",
            "tone",
            "one_question",
            "qualify",
            "off_limits",
            "competitors_decline",
        )
        # Nothing half-rendered: no empty heading, no dangling colon.
        assert "{{" not in prompt.text
        assert "\n\n\n" not in prompt.text

    def test_filling_the_settings_in_grows_the_prompt(
        self, definition: AgentDefinition
    ) -> None:
        stored = apply_override_patch(
            definition,
            None,
            {
                "settings": {
                    "companyBlurb": "We make one inbox for every channel.",
                    "bookingUrl": "https://cal.example/demo",
                    "handoffEmail": "sales@acme.test",
                    "signOff": "Thanks for stopping by!",
                }
            },
            abilities=REGISTRY,
        )
        config = resolve_config(definition, stored, abilities=REGISTRY)
        prompt = build_prompt(
            definition=definition,
            settings=config.settings,
            locked_rules=config.locked_rules,
            company="Acme",
        )
        assert {"company", "booking", "handoff", "sign_off"} <= set(prompt.used)
        assert "https://cal.example/demo" in prompt.text

    def test_the_locked_rules_arrive_verbatim(self, definition: AgentDefinition) -> None:
        # They are not templated, which is why validate_definition refuses a
        # placeholder in one. This is the other half of that promise.
        config = resolve_config(definition, None, abilities=REGISTRY)
        prompt = build_prompt(
            definition=definition,
            settings=config.settings,
            locked_rules=config.locked_rules,
            company="Acme",
        )
        for rule in config.locked_rules:
            assert rule.text in prompt.text
