"""Tests for the agent definition document.

The subject is ``validate_definition``: it must never raise, must report a path
the admin UI can point at a field, and must catch anything that would publish
cleanly and then misbehave on a real visitor.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from celeris_bridge.domain.agent.definition_schema import (
    CHANNELS,
    MAX_DEFINITION_BYTES,
    AgentDefinition,
    DefinitionReport,
    defaults_of,
    empty_definition,
    placeholders_in,
    validate_definition,
)
from celeris_bridge.domain.agent.field_spec import FieldSpec


class FakeAbility:
    """Stands in for a registered ability; only its settings are consulted."""

    def __init__(self, label: str = "Ability", **specs: Mapping[str, Any]) -> None:
        self.label = label
        self.settings = {
            key: FieldSpec.model_validate(spec) for key, spec in specs.items()
        }


REGISTRY = {
    "search_knowledge": FakeAbility(),
    "save_visitor_details": FakeAbility(
        maxFields={"label": "Max fields", "type": "number", "default": 5, "min": 1, "max": 10}
    ),
}


def definition(**overrides: Any) -> dict[str, Any]:
    """A small definition that validates cleanly, with sections overridden."""
    base: dict[str, Any] = {
        "identity": {"name": "Marketing", "channels": ["web_chat"]},
        "settingGroups": ["Company", "Behaviour"],
        "settings": {
            "companySummary": {
                "label": "Company summary",
                "group": "Company",
                "type": "string",
                "default": "",
                "editable": True,
                "maxLength": 600,
            },
            "tone": {
                "label": "Tone",
                "group": "Behaviour",
                "type": "enum",
                "default": "friendly",
                "editable": True,
                "options": ["friendly", "formal"],
            },
            "maxTurns": {
                "label": "Max turns",
                "group": "Behaviour",
                "type": "number",
                "default": 40,
                "integer": True,
                "min": 1,
                "max": 200,
            },
        },
        "instructions": [
            {"id": "role", "title": "Who you are", "text": "You are {{company}}'s assistant."},
            {
                "id": "about",
                "title": "About",
                "text": "Approved facts:\n{{companySummary}}",
                "when": {"setting": "companySummary", "notEmpty": True},
            },
        ],
        "lockedRules": [{"id": "truthful", "text": "Only state approved facts."}],
        "abilities": [{"key": "search_knowledge", "enabledByDefault": True}],
    }
    base.update(overrides)
    return base


def check(**overrides: Any) -> DefinitionReport:
    return validate_definition(definition(**overrides), abilities=REGISTRY)


def messages(report: DefinitionReport) -> list[str]:
    return [f"{p.path}: {p.message}" for p in report.problems]


# --------------------------------------------------------------------------
# The happy path, and the contract of the report
# --------------------------------------------------------------------------


def test_a_sound_definition_has_no_problems() -> None:
    report = check()
    assert messages(report) == []
    assert report.ok
    assert isinstance(report.definition, AgentDefinition)


def test_omitted_sections_are_filled_in() -> None:
    report = check()
    assert report.definition is not None
    assert report.definition.limits.max_agents_per_workspace == 3
    assert report.definition.rules == []
    assert report.definition.upgrade.renamed_settings == {}


def test_the_blank_starter_definition_validates() -> None:
    report = validate_definition(empty_definition(), abilities=REGISTRY)
    assert messages(report) == []


def test_defaults_of_gives_every_setting() -> None:
    report = check()
    assert report.definition is not None
    assert defaults_of(report.definition) == {
        "companySummary": "",
        "tone": "friendly",
        "maxTurns": 40,
    }


@pytest.mark.parametrize("junk", [None, [], "text", 7, True])
def test_a_non_object_is_reported_not_raised(junk: Any) -> None:
    report = validate_definition(junk, abilities=REGISTRY)
    assert not report.ok
    assert report.definition is None
    assert "must be an object" in report.problems[0].message


def test_a_structural_problem_yields_no_definition() -> None:
    report = check(identity={"name": "", "channels": ["web_chat"]})
    assert not report.ok
    assert report.definition is None


def test_a_semantic_problem_still_yields_a_definition_so_a_draft_can_be_saved() -> None:
    report = check(settings={**definition()["settings"], "orphan": {
        "label": "Orphan", "type": "string", "default": "", "group": "Nowhere",
    }})
    assert not report.ok
    assert report.definition is not None


def test_an_oversized_definition_is_refused() -> None:
    report = check(instructions=[
        {"id": f"r{i}", "title": "Bulk", "text": "x" * 8000} for i in range(40)
    ])
    assert not report.ok
    assert "too large" in report.problems[0].message
    assert str(MAX_DEFINITION_BYTES) in report.problems[0].message


def test_values_json_cannot_represent_are_refused() -> None:
    report = validate_definition({"identity": object()}, abilities=REGISTRY)
    assert "must be plain JSON" in report.problems[0].message


# --------------------------------------------------------------------------
# Settings: flat, grouped, and not contradictory
# --------------------------------------------------------------------------


def test_nested_settings_are_named_as_the_mistake_they_are() -> None:
    """The exact shape of the migration-013 bug: a group of groups."""
    report = check(settings={
        "quickReplies": {
            "bookDemo": {"label": "Book a demo", "type": "boolean", "default": False},
        },
    })
    assert not report.ok
    (problem,) = report.problems
    assert problem.path == "settings.quickReplies"
    assert "flat list" in problem.message
    assert "bookDemo" in problem.message
    assert 'use "group"' in problem.message


def test_a_setting_that_is_not_an_object_is_reported_against_its_key() -> None:
    report = check(settings={"tone": "friendly"})
    assert report.problems[0].path == "settings.tone"
    assert "must be an object" in report.problems[0].message


def test_settings_must_be_a_map() -> None:
    report = check(settings=[{"label": "Tone", "type": "string", "default": ""}])
    assert "map of setting key to spec" in report.problems[0].message


def test_a_group_not_in_setting_groups_is_reported() -> None:
    report = check(
        settings={"tone": {
            "label": "Tone", "type": "string", "default": "", "group": "Vibes",
        }},
        instructions=[{"id": "role", "title": "Role", "text": "Assist {{company}}."}],
    )
    assert messages(report) == ['settings.tone.group: Group "Vibes" is not in settingGroups']


def test_groups_are_unconstrained_when_none_are_declared() -> None:
    report = check(
        settingGroups=[],
        settings={"tone": {
            "label": "Tone", "type": "string", "default": "", "group": "Anything",
        }},
        instructions=[{"id": "role", "title": "Role", "text": "Assist {{company}}."}],
    )
    assert messages(report) == []


def test_duplicate_setting_groups_are_reported_once() -> None:
    report = check(settingGroups=["Company", "Behaviour", "Company", "Company"])
    assert messages(report) == ['settingGroups: Duplicate group "Company"']


def test_admin_only_cannot_also_be_editable() -> None:
    report = check(settings={
        "riskTopics": {
            "label": "Risk topics", "type": "stringList", "default": [],
            "adminOnly": True, "editable": True,
        },
    })
    assert report.problems[0].path == "settings.riskTopics.adminOnly"
    assert "cannot also be workspace-editable" in report.problems[0].message


def test_a_bad_setting_default_is_reported_by_the_spec_itself() -> None:
    report = check(settings={
        "tone": {
            "label": "Tone", "type": "enum", "default": "sarcastic",
            "options": ["friendly", "formal"],
        },
    })
    assert not report.ok
    assert any("default" in m for m in messages(report))


# --------------------------------------------------------------------------
# Instructions: every placeholder and condition must be able to do something
# --------------------------------------------------------------------------


def test_placeholders_are_found_in_order() -> None:
    assert placeholders_in("{{company}} and {{ tone }} and {{company}}") == [
        "company",
        "tone",
        "company",
    ]


def test_an_unknown_placeholder_is_reported() -> None:
    report = check(instructions=[
        {"id": "role", "title": "Role", "text": "You work for {{compnay}}."},
    ])
    assert messages(report) == ["instructions[0].text: Unknown placeholder {{compnay}}"]


def test_a_malformed_placeholder_is_reported_rather_than_silently_shipped() -> None:
    report = check(instructions=[
        {"id": "role", "title": "Role", "text": "You work for {{company name}}."},
    ])
    assert report.problems[0].path == "instructions[0].text"
    assert "not a usable placeholder" in report.problems[0].message
    assert "sent to the model as written" in report.problems[0].message


def test_builtin_placeholders_need_no_setting() -> None:
    report = check(instructions=[
        {"id": "role", "title": "Role", "text": "You work for {{company}}."},
    ])
    assert messages(report) == []


def test_duplicate_instruction_ids_are_reported() -> None:
    report = check(instructions=[
        {"id": "role", "title": "A", "text": "One."},
        {"id": "role", "title": "B", "text": "Two."},
    ])
    assert messages(report) == ['instructions: Duplicate instruction id "role"']


def test_a_condition_on_an_unknown_setting_is_reported() -> None:
    report = check(instructions=[
        {"id": "x", "title": "X", "text": "Text.", "when": {"setting": "nope", "notEmpty": True}},
    ])
    assert messages(report) == ['instructions[0].when.setting: Unknown setting "nope"']


def test_a_condition_needs_exactly_one_test() -> None:
    both = check(instructions=[
        {"id": "x", "title": "X", "text": "T.", "when": {"setting": "tone", "is": "formal", "notEmpty": True}},
    ])
    assert not both.ok
    neither = check(instructions=[
        {"id": "x", "title": "X", "text": "T.", "when": {"setting": "tone"}},
    ])
    assert not neither.ok


def test_a_condition_value_the_setting_can_never_hold_is_reported() -> None:
    """Otherwise the block is dead prompt text that silently never renders."""
    report = check(instructions=[
        {"id": "x", "title": "X", "text": "T.", "when": {"setting": "tone", "is": "sarcastic"}},
    ])
    assert report.problems[0].path == "instructions[0].when.is"
    assert "can never hold this value" in report.problems[0].message


def test_a_valid_condition_value_passes() -> None:
    report = check(instructions=[
        {"id": "x", "title": "X", "text": "T.", "when": {"setting": "tone", "is": "formal"}},
    ])
    assert messages(report) == []


def test_is_cannot_compare_a_list_setting() -> None:
    report = check(
        settings={
            "topics": {"label": "Topics", "type": "stringList", "default": []},
        },
        instructions=[
            {"id": "x", "title": "X", "text": "T.", "when": {"setting": "topics", "is": "pricing"}},
        ],
    )
    assert report.problems[0].path == "instructions[0].when.is"
    assert 'use "notEmpty"' in report.problems[0].message


def test_not_empty_is_pointless_on_a_number() -> None:
    report = check(instructions=[
        {"id": "x", "title": "X", "text": "T.", "when": {"setting": "maxTurns", "notEmpty": True}},
    ])
    assert report.problems[0].path == "instructions[0].when.notEmpty"
    assert "never empty" in report.problems[0].message


def test_not_empty_must_be_true_not_false() -> None:
    report = check(instructions=[
        {"id": "x", "title": "X", "text": "T.", "when": {"setting": "tone", "notEmpty": False}},
    ])
    assert not report.ok


# --------------------------------------------------------------------------
# Locked rules
# --------------------------------------------------------------------------


def test_a_locked_rule_cannot_carry_a_placeholder() -> None:
    """The engine appends locked rules verbatim, so braces would reach the model."""
    report = check(lockedRules=[
        {"id": "truthful", "text": "Never invent facts about {{company}}."},
    ])
    assert report.problems[0].path == "lockedRules[0].text"
    assert "used as written" in report.problems[0].message
    assert "{{company}}" in report.problems[0].message


def test_duplicate_locked_rule_ids_are_reported() -> None:
    report = check(lockedRules=[
        {"id": "truthful", "text": "One."},
        {"id": "truthful", "text": "Two."},
    ])
    assert messages(report) == ['lockedRules: Duplicate locked rule id "truthful"']


# --------------------------------------------------------------------------
# Abilities: only ones that exist, configured only with settings they have
# --------------------------------------------------------------------------


def test_an_unregistered_ability_is_reported() -> None:
    report = check(abilities=[{"key": "send_invoice"}])
    assert messages(report) == ['abilities[0].key: No ability called "send_invoice"']


def test_an_empty_registry_reports_every_ability() -> None:
    report = validate_definition(definition(), abilities={})
    assert messages(report) == ['abilities[0].key: No ability called "search_knowledge"']


def test_duplicate_abilities_are_reported() -> None:
    report = check(abilities=[{"key": "search_knowledge"}, {"key": "search_knowledge"}])
    assert 'abilities: Duplicate ability "search_knowledge"' in messages(report)


def test_an_ability_setting_that_does_not_exist_is_reported() -> None:
    report = check(abilities=[
        {"key": "save_visitor_details", "settings": {"maxFeilds": 3}},
    ])
    assert messages(report) == [
        'abilities[0].settings.maxFeilds: "maxFeilds" is not a setting of "save_visitor_details"'
    ]


def test_an_ability_setting_value_is_validated_against_the_ability() -> None:
    report = check(abilities=[
        {"key": "save_visitor_details", "settings": {"maxFields": 99}},
    ])
    assert report.problems[0].path == "abilities[0].settings.maxFields"
    assert "at most 10" in report.problems[0].message


def test_a_valid_ability_setting_passes() -> None:
    report = check(abilities=[
        {"key": "save_visitor_details", "settings": {"maxFields": 3}},
    ])
    assert messages(report) == []


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


QUALIFICATION_FIELDS = {
    "label": "Qualification fields",
    "type": "list",
    "editable": True,
    "uniqueBy": "key",
    "item": {
        "key": {"label": "Key", "type": "string", "maxLength": 40},
        "requiredToBook": {"label": "Required to book", "type": "boolean"},
    },
    "default": [
        {"key": "name", "requiredToBook": True},
        {"key": "email", "requiredToBook": True},
    ],
}

EMAIL_RULE = {
    "type": "listRequiresItem",
    "setting": "qualificationFields",
    "match": {"key": "email"},
    "require": {"requiredToBook": True},
    "message": '"email" must be collected and required to book',
}


def with_rule(**changes: Any) -> DefinitionReport:
    fields = {**QUALIFICATION_FIELDS, **changes.pop("field", {})}
    return check(
        settingGroups=[],
        settings={"qualificationFields": fields},
        instructions=[{"id": "role", "title": "Role", "text": "Assist {{company}}."}],
        rules=[{**EMAIL_RULE, **changes}],
    )


def test_a_rule_the_defaults_satisfy_passes() -> None:
    assert messages(with_rule()) == []


def test_a_rule_the_defaults_break_is_reported_against_the_default() -> None:
    report = with_rule(field={"default": [{"key": "name", "requiredToBook": True}]})
    assert report.problems[0].path == "settings.qualificationFields.default"
    assert "The default breaks a rule" in report.problems[0].message
    assert "must be collected and required to book" in report.problems[0].message


def test_a_rule_on_an_unknown_setting_is_reported() -> None:
    report = with_rule(setting="nope")
    assert messages(report) == ['rules[0]: Unknown setting "nope"']


def test_a_rule_on_a_setting_that_is_not_a_list_is_reported() -> None:
    report = check(
        settingGroups=[],
        settings={"qualificationFields": {
            "label": "Fields", "type": "string", "default": "",
        }},
        instructions=[{"id": "role", "title": "Role", "text": "Assist {{company}}."}],
        rules=[EMAIL_RULE],
    )
    assert messages(report) == ['rules[0]: "qualificationFields" is not a list setting']


def test_a_rule_naming_a_field_the_item_lacks_is_reported() -> None:
    report = with_rule(require={"mandatory": True})
    assert messages(report) == [
        'rules[0]: "mandatory" is not a field of "qualificationFields" items'
    ]


def test_a_rule_that_could_never_match_is_reported() -> None:
    """requiredToBook is a boolean, so it can never equal the string "yes"."""
    report = with_rule(require={"requiredToBook": "yes"})
    assert messages(report) == [
        'rules[0]: "requiredToBook" of "qualificationFields" can never equal \'yes\''
    ]


def test_an_unsound_rule_does_not_also_report_the_defaults() -> None:
    """One mistake, one message: a broken reference cannot judge a default."""
    report = with_rule(setting="nope")
    assert len(report.problems) == 1


def test_a_rule_needs_something_to_match_on() -> None:
    report = with_rule(match={})
    assert not report.ok


# --------------------------------------------------------------------------
# Upgrade
# --------------------------------------------------------------------------


def test_a_rename_to_a_live_setting_is_accepted() -> None:
    report = check(upgrade={"renamedSettings": {"voiceTone": "tone"}})
    assert messages(report) == []


def test_a_rename_to_an_unknown_setting_is_reported() -> None:
    report = check(upgrade={"renamedSettings": {"voiceTone": "timbre"}})
    assert messages(report) == [
        'upgrade.renamedSettings.voiceTone: Renamed to "timbre", which is not a setting'
    ]


def test_a_rename_from_a_setting_that_still_exists_is_reported() -> None:
    report = check(upgrade={"renamedSettings": {"tone": "companySummary"}})
    assert messages(report) == [
        'upgrade.renamedSettings.tone: "tone" still exists, so it was not renamed'
    ]


def test_a_rename_to_itself_is_reported_once() -> None:
    report = check(upgrade={"renamedSettings": {"tone": "tone"}})
    assert messages(report) == ['upgrade.renamedSettings.tone: "tone" is renamed to itself']


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def test_every_channel_is_declared() -> None:
    assert CHANNELS == ("web_chat", "email", "voice", "whatsapp")


def test_an_unknown_channel_is_refused() -> None:
    report = check(identity={"name": "Marketing", "channels": ["telepathy"]})
    assert not report.ok
    assert report.definition is None


def test_at_least_one_channel_is_required() -> None:
    report = check(identity={"name": "Marketing", "channels": []})
    assert not report.ok


def test_a_repeated_channel_is_refused() -> None:
    report = check(identity={"name": "Marketing", "channels": ["web_chat", "web_chat"]})
    assert not report.ok
    assert "twice" in messages(report)[0]


def test_unknown_top_level_keys_are_refused() -> None:
    report = check(voiceConfig={"provider": "telnyx"})
    assert not report.ok
    assert any("voiceConfig" in m for m in messages(report))


# --------------------------------------------------------------------------
# Every problem is addressed to something an admin can find
# --------------------------------------------------------------------------


def test_every_problem_carries_a_usable_path_and_message() -> None:
    report = check(
        settingGroups=["Company", "Company"],
        settings={
            "tone": {"label": "T", "type": "string", "default": "", "group": "Gone"},
            "hidden": {
                "label": "H", "type": "string", "default": "",
                "adminOnly": True, "editable": True,
            },
        },
        instructions=[
            {"id": "a", "title": "A", "text": "{{missing}}"},
            {"id": "a", "title": "A", "text": "ok"},
        ],
        lockedRules=[{"id": "r", "text": "About {{company}}."}],
        abilities=[{"key": "nope"}],
        upgrade={"renamedSettings": {"old": "gone"}},
    )
    assert len(report.problems) >= 7
    for problem in report.problems:
        assert problem.path and not problem.path.startswith(".")
        assert problem.message and problem.message[0] == problem.message[0].upper()
