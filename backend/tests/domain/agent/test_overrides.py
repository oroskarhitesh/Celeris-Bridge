"""Tests for workspace overrides.

The property under test throughout: a workspace stores only what differs from
its definition, so publishing a better default reaches everyone who never
touched that setting.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from celeris_bridge.domain.agent.definition_schema import (
    AgentDefinition,
    validate_definition,
)
from celeris_bridge.domain.agent.field_spec import FieldSpec
from celeris_bridge.domain.agent.overrides import (
    AbilityOverride,
    Overrides,
    OverridesRejected,
    RejectionCode,
    apply_override_patch,
    _same,
    carry_overrides,
    empty_overrides,
    reset_overrides,
    resolve_config,
)


class FakeAbility:
    def __init__(self, label: str, **specs: Mapping[str, Any]) -> None:
        self.label = label
        self.settings = {k: FieldSpec.model_validate(v) for k, v in specs.items()}


REGISTRY = {
    "search_knowledge": FakeAbility("Search knowledge"),
    "save_visitor_details": FakeAbility(
        "Save visitor details",
        maxFields={
            "label": "Max fields", "type": "number", "default": 5,
            "min": 1, "max": 10, "integer": True, "editable": True,
        },
        notify={"label": "Notify the team", "type": "boolean", "default": False},
    ),
}

SETTINGS: dict[str, Any] = {
    "greeting": {
        "label": "Greeting", "type": "string", "default": "Hi there!",
        "editable": True, "maxLength": 200,
    },
    "tone": {
        "label": "Tone", "type": "enum", "default": "friendly", "editable": True,
        "options": ["friendly", "formal"],
    },
    "brandColor": {
        "label": "Brand colour", "type": "color", "default": "#0d8f7c", "editable": True,
    },
    "topics": {
        "label": "Topics to avoid", "type": "stringList", "default": [], "editable": True,
    },
    "conversationModel": {
        "label": "Conversation model", "type": "string", "default": "anthropic:claude",
    },
    "qualificationFields": {
        "label": "Qualification fields", "type": "list", "editable": True,
        "uniqueBy": "key",
        "item": {
            "key": {"label": "Key", "type": "string", "maxLength": 40},
            "requiredToBook": {"label": "Required to book", "type": "boolean"},
        },
        "default": [
            {"key": "name", "requiredToBook": True},
            {"key": "email", "requiredToBook": True},
        ],
    },
}


def build(**overrides: Any) -> AgentDefinition:
    raw: dict[str, Any] = {
        "identity": {"name": "Marketing", "channels": ["web_chat"]},
        "settings": SETTINGS,
        "instructions": [{"id": "role", "title": "Role", "text": "Assist {{company}}."}],
        "lockedRules": [{"id": "truthful", "text": "Only state approved facts."}],
        "abilities": [
            {"key": "search_knowledge", "enabledByDefault": True, "editable": False},
            {
                "key": "save_visitor_details", "enabledByDefault": True,
                "editable": True, "settings": {"maxFields": 4},
            },
        ],
    }
    raw.update(overrides)
    report = validate_definition(raw, abilities=REGISTRY)
    assert report.definition is not None, report.problems
    return report.definition


DEFINITION = build()


def patch(current: Overrides | None = None, **patch_body: Any) -> Overrides:
    return apply_override_patch(DEFINITION, current, patch_body, abilities=REGISTRY)


def rejections(**patch_body: Any) -> list[tuple[str, RejectionCode]]:
    with pytest.raises(OverridesRejected) as caught:
        patch(**patch_body)
    return [(r.path, r.code) for r in caught.value.rejections]


# --------------------------------------------------------------------------
# resolve_config
# --------------------------------------------------------------------------


def test_no_overrides_resolves_to_every_default() -> None:
    config = resolve_config(DEFINITION, abilities=REGISTRY)
    assert config.settings["greeting"] == "Hi there!"
    assert config.settings["tone"] == "friendly"
    assert config.settings["qualificationFields"] == SETTINGS["qualificationFields"]["default"]


def test_an_override_wins_over_the_default() -> None:
    config = resolve_config(
        DEFINITION, Overrides(settings={"tone": "formal"}), abilities=REGISTRY
    )
    assert config.settings["tone"] == "formal"
    assert config.settings["greeting"] == "Hi there!"


def test_locked_rules_come_through_for_the_prompt() -> None:
    config = resolve_config(DEFINITION, abilities=REGISTRY)
    assert [rule.id for rule in config.locked_rules] == ["truthful"]


def test_a_stored_override_of_a_now_locked_setting_is_ignored_at_once() -> None:
    """The safe direction: do not wait for an upgrade to stop honouring it."""
    locked = build(settings={**SETTINGS, "tone": {**SETTINGS["tone"], "editable": False}})
    config = resolve_config(
        locked, Overrides(settings={"tone": "formal"}), abilities=REGISTRY
    )
    assert config.settings["tone"] == "friendly"


def test_the_resolved_config_cannot_be_mutated_through_into_the_overrides() -> None:
    stored = Overrides(settings={"topics": ["pricing"]})
    config = resolve_config(DEFINITION, stored, abilities=REGISTRY)
    config.settings["topics"].append("refunds")
    assert stored.settings["topics"] == ["pricing"]


def test_defaults_cannot_be_mutated_through_a_resolved_config() -> None:
    first = resolve_config(DEFINITION, abilities=REGISTRY)
    first.settings["qualificationFields"].clear()
    second = resolve_config(DEFINITION, abilities=REGISTRY)
    assert len(second.settings["qualificationFields"]) == 2


# --------------------------------------------------------------------------
# resolve_config: abilities
# --------------------------------------------------------------------------


def test_ability_defaults_come_from_the_definition_over_the_ability() -> None:
    """The definition says maxFields 4; the ability itself says 5."""
    config = resolve_config(DEFINITION, abilities=REGISTRY)
    saver = config.ability("save_visitor_details")
    assert saver is not None
    assert saver.settings["maxFields"] == 4
    assert saver.settings["notify"] is False


def test_an_ability_override_wins_over_both() -> None:
    config = resolve_config(
        DEFINITION,
        Overrides(abilities={"save_visitor_details": AbilityOverride(settings={"maxFields": 9})}),
        abilities=REGISTRY,
    )
    saver = config.ability("save_visitor_details")
    assert saver is not None and saver.settings["maxFields"] == 9


def test_an_editable_ability_can_be_switched_off() -> None:
    config = resolve_config(
        DEFINITION,
        Overrides(abilities={"save_visitor_details": AbilityOverride(enabled=False)}),
        abilities=REGISTRY,
    )
    saver = config.ability("save_visitor_details")
    assert saver is not None and saver.enabled is False
    assert [a.key for a in config.enabled_abilities] == ["search_knowledge"]


def test_a_non_editable_ability_cannot_be_switched_off_by_a_stored_value() -> None:
    config = resolve_config(
        DEFINITION,
        Overrides(abilities={"search_knowledge": AbilityOverride(enabled=False)}),
        abilities=REGISTRY,
    )
    searcher = config.ability("search_knowledge")
    assert searcher is not None and searcher.enabled is True


def test_an_ability_the_build_no_longer_has_is_skipped_and_reported() -> None:
    """Better to run without a tool than to offer the model one that is gone."""
    config = resolve_config(DEFINITION, abilities={"search_knowledge": REGISTRY["search_knowledge"]})
    assert [a.key for a in config.abilities] == ["search_knowledge"]
    assert config.missing_abilities == ("save_visitor_details",)


def test_nothing_is_missing_when_the_registry_is_complete() -> None:
    assert resolve_config(DEFINITION, abilities=REGISTRY).missing_abilities == ()


# --------------------------------------------------------------------------
# apply_override_patch: the delta stays a delta
# --------------------------------------------------------------------------


def test_a_changed_value_is_stored() -> None:
    assert patch(settings={"tone": "formal"}).settings == {"tone": "formal"}


def test_a_value_equal_to_the_default_is_not_stored() -> None:
    assert patch(settings={"tone": "friendly"}).settings == {}


def test_setting_a_value_back_to_the_default_removes_an_existing_override() -> None:
    stored = Overrides(settings={"tone": "formal"})
    assert patch(stored, settings={"tone": "friendly"}).settings == {}


def test_a_value_that_only_differs_by_whitespace_is_not_an_override() -> None:
    """field_spec trims as it validates, which is what makes this work."""
    assert patch(settings={"greeting": "  Hi there!  "}).settings == {}


def test_a_colour_that_only_differs_by_case_is_not_an_override() -> None:
    assert patch(settings={"brandColor": "#0D8F7C"}).settings == {}


def test_a_list_whose_rows_arrive_in_a_different_key_order_is_not_an_override() -> None:
    reordered = [
        {"requiredToBook": True, "key": "name"},
        {"requiredToBook": True, "key": "email"},
    ]
    assert patch(settings={"qualificationFields": reordered}).settings == {}


def test_a_genuinely_different_list_is_stored() -> None:
    changed = [
        {"key": "name", "requiredToBook": True},
        {"key": "email", "requiredToBook": True},
        {"key": "company", "requiredToBook": False},
    ]
    assert len(patch(settings={"qualificationFields": changed}).settings) == 1


def test_a_stored_value_is_the_normalised_one() -> None:
    assert patch(settings={"greeting": "  Hello  "}).settings == {"greeting": "Hello"}


def test_the_patch_does_not_mutate_what_was_passed_in() -> None:
    stored = Overrides(settings={"tone": "formal"})
    patch(stored, settings={"greeting": "Hello"})
    assert stored.settings == {"tone": "formal"}


def test_an_empty_patch_leaves_overrides_as_they_were() -> None:
    stored = Overrides(settings={"tone": "formal"})
    assert patch(stored).settings == {"tone": "formal"}


def test_unrelated_overrides_survive_a_patch() -> None:
    stored = Overrides(settings={"tone": "formal"})
    result = patch(stored, settings={"greeting": "Hello"})
    assert result.settings == {"tone": "formal", "greeting": "Hello"}


# --------------------------------------------------------------------------
# apply_override_patch: refusals
# --------------------------------------------------------------------------


def test_an_unknown_setting_is_refused() -> None:
    assert rejections(settings={"nope": 1}) == [
        ("settings.nope", RejectionCode.SETTING_UNKNOWN)
    ]


def test_a_locked_setting_is_refused() -> None:
    assert rejections(settings={"conversationModel": "openai:gpt"}) == [
        ("settings.conversationModel", RejectionCode.SETTING_LOCKED)
    ]


def test_an_invalid_value_is_refused_with_the_label_a_person_sees() -> None:
    with pytest.raises(OverridesRejected) as caught:
        patch(settings={"tone": "sarcastic"})
    (rejection,) = caught.value.rejections
    assert rejection.code == RejectionCode.SETTING_INVALID
    assert rejection.message.startswith("Tone ")
    assert "friendly, formal" in rejection.message


def test_every_reason_is_reported_not_just_the_first() -> None:
    assert rejections(settings={
        "nope": 1,
        "conversationModel": "openai:gpt",
        "tone": "sarcastic",
    }) == [
        ("settings.nope", RejectionCode.SETTING_UNKNOWN),
        ("settings.conversationModel", RejectionCode.SETTING_LOCKED),
        ("settings.tone", RejectionCode.SETTING_INVALID),
    ]


def test_nothing_is_applied_when_any_part_is_refused() -> None:
    stored = Overrides(settings={"tone": "formal"})
    with pytest.raises(OverridesRejected):
        patch(stored, settings={"greeting": "Hello", "nope": 1})
    assert stored.settings == {"tone": "formal"}


def test_an_unknown_ability_is_refused() -> None:
    assert rejections(abilities={"send_invoice": {"enabled": True}}) == [
        ("abilities.send_invoice", RejectionCode.ABILITY_UNKNOWN)
    ]


def test_a_non_editable_ability_cannot_be_switched() -> None:
    assert rejections(abilities={"search_knowledge": {"enabled": False}}) == [
        ("abilities.search_knowledge.enabled", RejectionCode.ABILITY_LOCKED)
    ]


def test_an_ability_change_must_be_a_map_of_enabled_and_settings() -> None:
    assert rejections(abilities={"save_visitor_details": True}) == [
        ("abilities.save_visitor_details", RejectionCode.ABILITY_INVALID)
    ]
    assert rejections(abilities={"save_visitor_details": {"turbo": 1}}) == [
        ("abilities.save_visitor_details", RejectionCode.ABILITY_INVALID)
    ]


def test_enabled_must_be_a_boolean() -> None:
    assert rejections(abilities={"save_visitor_details": {"enabled": "yes"}}) == [
        ("abilities.save_visitor_details.enabled", RejectionCode.ABILITY_INVALID)
    ]


def test_an_unknown_ability_setting_is_refused() -> None:
    assert rejections(
        abilities={"save_visitor_details": {"settings": {"maxFeilds": 3}}}
    ) == [("abilities.save_visitor_details.settings.maxFeilds", RejectionCode.ABILITY_SETTING_UNKNOWN)]


def test_a_locked_ability_setting_is_refused() -> None:
    assert rejections(
        abilities={"save_visitor_details": {"settings": {"notify": True}}}
    ) == [("abilities.save_visitor_details.settings.notify", RejectionCode.ABILITY_SETTING_LOCKED)]


def test_an_invalid_ability_setting_value_is_refused() -> None:
    assert rejections(
        abilities={"save_visitor_details": {"settings": {"maxFields": 99}}}
    ) == [("abilities.save_visitor_details.settings.maxFields", RejectionCode.ABILITY_SETTING_INVALID)]


def test_a_malformed_patch_body_is_refused_rather_than_crashing() -> None:
    assert rejections(settings=["tone"]) == [("settings", RejectionCode.SETTING_INVALID)]
    assert rejections(abilities="all") == [("abilities", RejectionCode.ABILITY_INVALID)]


# --------------------------------------------------------------------------
# apply_override_patch: ability deltas stay minimal
# --------------------------------------------------------------------------


def test_switching_an_ability_off_is_stored() -> None:
    result = patch(abilities={"save_visitor_details": {"enabled": False}})
    assert result.abilities["save_visitor_details"].enabled is False


def test_switching_an_ability_back_to_its_default_stores_nothing() -> None:
    stored = Overrides(abilities={"save_visitor_details": AbilityOverride(enabled=False)})
    result = patch(stored, abilities={"save_visitor_details": {"enabled": True}})
    assert result.abilities == {}


def test_an_ability_setting_equal_to_the_definitions_choice_stores_nothing() -> None:
    """The definition sets maxFields to 4, so 4 is not an override."""
    assert patch(abilities={"save_visitor_details": {"settings": {"maxFields": 4}}}).abilities == {}


def test_an_ability_setting_equal_to_the_abilitys_own_default_is_still_an_override() -> None:
    """The ability says 5 but this definition says 4, so 5 is a real change."""
    result = patch(abilities={"save_visitor_details": {"settings": {"maxFields": 5}}})
    assert result.abilities["save_visitor_details"].settings == {"maxFields": 5}


def test_an_emptied_ability_entry_is_removed_entirely() -> None:
    stored = Overrides(
        abilities={"save_visitor_details": AbilityOverride(settings={"maxFields": 9})}
    )
    result = patch(stored, abilities={"save_visitor_details": {"settings": {"maxFields": 4}}})
    assert result.abilities == {}


# --------------------------------------------------------------------------
# apply_override_patch: rules
# --------------------------------------------------------------------------


EMAIL_RULE = {
    "type": "listRequiresItem",
    "setting": "qualificationFields",
    "match": {"key": "email"},
    "require": {"requiredToBook": True},
    "message": '"email" must be collected and required to book',
}


def test_a_patch_that_breaks_a_rule_is_refused() -> None:
    with_rule = build(rules=[EMAIL_RULE])
    with pytest.raises(OverridesRejected) as caught:
        apply_override_patch(
            with_rule,
            None,
            {"settings": {"qualificationFields": [{"key": "name", "requiredToBook": True}]}},
            abilities=REGISTRY,
        )
    (rejection,) = caught.value.rejections
    assert rejection.path == "settings.qualificationFields"
    assert rejection.code == RejectionCode.SETTINGS_RULE
    assert "required to book" in rejection.message


def test_a_patch_that_keeps_a_rule_satisfied_is_accepted() -> None:
    with_rule = build(rules=[EMAIL_RULE])
    result = apply_override_patch(
        with_rule,
        None,
        {"settings": {"qualificationFields": [
            {"key": "email", "requiredToBook": True},
            {"key": "company", "requiredToBook": False},
        ]}},
        abilities=REGISTRY,
    )
    assert "qualificationFields" in result.settings


def test_a_field_error_is_not_buried_under_a_rule_error() -> None:
    """A rule cannot judge a value the workspace never managed to set."""
    with_rule = build(rules=[EMAIL_RULE])
    with pytest.raises(OverridesRejected) as caught:
        apply_override_patch(
            with_rule, None, {"settings": {"tone": "sarcastic"}}, abilities=REGISTRY
        )
    assert [r.code for r in caught.value.rejections] == [RejectionCode.SETTING_INVALID]


# --------------------------------------------------------------------------
# reset_overrides
# --------------------------------------------------------------------------


def test_resetting_a_setting_restores_the_default() -> None:
    stored = Overrides(settings={"tone": "formal", "greeting": "Hello"})
    result = reset_overrides(DEFINITION, stored, settings=["tone"])
    assert result.settings == {"greeting": "Hello"}
    assert resolve_config(DEFINITION, result, abilities=REGISTRY).settings["tone"] == "friendly"


def test_resetting_an_ability_restores_its_defaults() -> None:
    stored = Overrides(abilities={"save_visitor_details": AbilityOverride(enabled=False)})
    result = reset_overrides(DEFINITION, stored, abilities=["save_visitor_details"])
    assert result.abilities == {}


def test_resetting_everything_clears_the_lot() -> None:
    stored = Overrides(
        settings={"tone": "formal"},
        abilities={"save_visitor_details": AbilityOverride(enabled=False)},
    )
    assert reset_overrides(DEFINITION, stored, everything=True) == empty_overrides()


def test_resetting_an_unknown_key_is_refused() -> None:
    with pytest.raises(OverridesRejected) as caught:
        reset_overrides(DEFINITION, None, settings=["nope"], abilities=["gone"])
    assert [r.code for r in caught.value.rejections] == [
        RejectionCode.SETTING_UNKNOWN,
        RejectionCode.ABILITY_UNKNOWN,
    ]


def test_resetting_a_setting_that_was_never_overridden_is_harmless() -> None:
    assert reset_overrides(DEFINITION, None, settings=["tone"]).settings == {}


# --------------------------------------------------------------------------
# carry_overrides: moving to another definition version
# --------------------------------------------------------------------------


def carry(stored: Overrides, definition: AgentDefinition, **kwargs: Any):
    return carry_overrides(stored, definition, abilities=REGISTRY, **kwargs)


def test_an_override_that_still_applies_is_carried() -> None:
    result = carry(Overrides(settings={"tone": "formal"}), DEFINITION)
    assert result.overrides.settings == {"tone": "formal"}
    assert result.dropped == []


def test_a_removed_setting_is_dropped_with_a_reason() -> None:
    trimmed = build(settings={k: v for k, v in SETTINGS.items() if k != "brandColor"})
    result = carry(Overrides(settings={"brandColor": "#ff0000"}), trimmed)
    assert result.overrides.settings == {}
    assert [(d.path, d.reason) for d in result.dropped] == [
        ("settings.brandColor", "This setting no longer exists")
    ]


def test_a_newly_locked_setting_is_dropped_with_its_label() -> None:
    locked = build(settings={**SETTINGS, "tone": {**SETTINGS["tone"], "editable": False}})
    result = carry(Overrides(settings={"tone": "formal"}), locked)
    assert result.overrides.settings == {}
    assert result.dropped[0].reason == '"Tone" can no longer be changed'


def test_a_value_the_new_version_disallows_is_dropped() -> None:
    narrowed = build(settings={
        **SETTINGS,
        "tone": {**SETTINGS["tone"], "options": ["friendly"], "default": "friendly"},
    })
    result = carry(Overrides(settings={"tone": "formal"}), narrowed)
    assert result.overrides.settings == {}
    assert "no longer allowed" in result.dropped[0].reason


def test_an_override_that_now_equals_the_new_default_stops_being_an_override() -> None:
    """The point of the whole design: a better default reaches this workspace."""
    moved = build(settings={**SETTINGS, "tone": {**SETTINGS["tone"], "default": "formal"}})
    result = carry(Overrides(settings={"tone": "formal"}), moved)
    assert result.overrides.settings == {}
    assert result.dropped == []
    assert resolve_config(moved, result.overrides, abilities=REGISTRY).settings["tone"] == "formal"


def test_a_renamed_setting_keeps_its_value() -> None:
    renamed = build(settings={
        **{k: v for k, v in SETTINGS.items() if k != "tone"},
        "voice": {**SETTINGS["tone"], "label": "Voice"},
    })
    result = carry(
        Overrides(settings={"tone": "formal"}), renamed, renames=[{"tone": "voice"}]
    )
    assert result.overrides.settings == {"voice": "formal"}
    assert result.dropped == []


def test_a_setting_renamed_twice_still_arrives() -> None:
    renamed = build(settings={
        **{k: v for k, v in SETTINGS.items() if k != "tone"},
        "style": {**SETTINGS["tone"], "label": "Style"},
    })
    result = carry(
        Overrides(settings={"tone": "formal"}),
        renamed,
        renames=[{"tone": "voice"}, {"voice": "style"}],
    )
    assert result.overrides.settings == {"style": "formal"}


def test_a_rename_onto_a_key_the_workspace_also_set_is_reported() -> None:
    """The reference drops the old value silently here."""
    renamed = build()
    result = carry(
        Overrides(settings={"greeting": "Old", "tone": "formal"}),
        renamed,
        renames=[{"greeting": "tone"}],
    )
    assert result.overrides.settings == {"tone": "formal"}
    assert [(d.path, d.reason) for d in result.dropped] == [
        ("settings.greeting", 'Replaced by "tone", which you had also set')
    ]


def test_a_removed_ability_is_dropped() -> None:
    trimmed = build(abilities=[{"key": "search_knowledge"}])
    result = carry(
        Overrides(abilities={"save_visitor_details": AbilityOverride(enabled=False)}),
        trimmed,
    )
    assert result.overrides.abilities == {}
    assert result.dropped[0].reason == "This ability no longer exists"


def test_an_ability_that_can_no_longer_be_switched_is_dropped_with_a_reason() -> None:
    fixed = build(abilities=[
        {"key": "search_knowledge"},
        {"key": "save_visitor_details", "editable": False, "settings": {"maxFields": 4}},
    ])
    result = carry(
        Overrides(abilities={"save_visitor_details": AbilityOverride(enabled=False)}),
        fixed,
    )
    assert result.overrides.abilities == {}
    assert result.dropped[0].path == "abilities.save_visitor_details.enabled"
    assert "can no longer be switched" in result.dropped[0].reason


def test_an_ability_setting_equal_to_a_new_definition_default_is_dropped_quietly() -> None:
    moved = build(abilities=[
        {"key": "search_knowledge"},
        {"key": "save_visitor_details", "editable": True, "settings": {"maxFields": 9}},
    ])
    result = carry(
        Overrides(abilities={"save_visitor_details": AbilityOverride(settings={"maxFields": 9})}),
        moved,
    )
    assert result.overrides.abilities == {}
    assert result.dropped == []


def test_an_override_breaking_a_new_rule_is_reset_to_the_default() -> None:
    with_rule = build(rules=[EMAIL_RULE])
    result = carry(
        Overrides(settings={
            "tone": "formal",
            "qualificationFields": [{"key": "name", "requiredToBook": True}],
        }),
        with_rule,
    )
    assert result.overrides.settings == {"tone": "formal"}
    (drop,) = result.dropped
    assert drop.path == "settings.qualificationFields"
    assert drop.reason.startswith("Reset to the default:")


def test_carrying_nothing_yields_nothing() -> None:
    result = carry(empty_overrides(), DEFINITION)
    assert result.overrides == empty_overrides()
    assert result.dropped == []


def test_carrying_does_not_mutate_the_stored_overrides() -> None:
    stored = Overrides(settings={"tone": "formal", "topics": ["pricing"]})
    trimmed = build(settings={k: v for k, v in SETTINGS.items() if k != "tone"})
    carry(stored, trimmed)
    assert stored.settings == {"tone": "formal", "topics": ["pricing"]}


def test_a_carried_result_is_accepted_by_the_target_version() -> None:
    """Whatever survives a carry must be a patch the target would allow."""
    with_rule = build(rules=[EMAIL_RULE])
    result = carry(
        Overrides(settings={
            "tone": "formal",
            "qualificationFields": [{"key": "name", "requiredToBook": True}],
        }),
        with_rule,
    )
    reapplied = apply_override_patch(
        with_rule, None, {"settings": result.overrides.settings}, abilities=REGISTRY
    )
    assert reapplied.settings == result.overrides.settings


# --------------------------------------------------------------------------
# The property the whole design exists for
# --------------------------------------------------------------------------


def test_a_better_default_reaches_a_workspace_that_never_touched_it() -> None:
    stored = patch(settings={"tone": "formal"})
    assert "greeting" not in stored.settings

    improved = build(settings={
        **SETTINGS,
        "greeting": {**SETTINGS["greeting"], "default": "Hello! How can I help?"},
    })
    carried = carry(stored, improved)
    config = resolve_config(improved, carried.overrides, abilities=REGISTRY)

    assert config.settings["greeting"] == "Hello! How can I help?"
    assert config.settings["tone"] == "formal"
    assert carried.dropped == []


def test_a_workspace_that_did_touch_it_keeps_its_choice() -> None:
    stored = patch(settings={"greeting": "Hi, I'm Ava."})
    improved = build(settings={
        **SETTINGS,
        "greeting": {**SETTINGS["greeting"], "default": "Hello! How can I help?"},
    })
    config = resolve_config(improved, carry(stored, improved).overrides, abilities=REGISTRY)
    assert config.settings["greeting"] == "Hi, I'm Ava."


# --------------------------------------------------------------------------
# Guards that the public API cannot currently reach
# --------------------------------------------------------------------------


def test_same_does_not_confuse_booleans_with_numbers() -> None:
    """
    Every public path validates before comparing, so a type mix-up cannot
    arrive today. The guard is for the next caller that forgets, because the
    failure it prevents is silent: an override quietly deleted as "same as the
    default".
    """
    assert not _same(True, 1)
    assert not _same(1, True)
    assert not _same(False, 0)
    assert not _same(0.0, False)
    assert _same(True, True)
    assert _same(1, 1.0)
    assert _same({"a": [1, 2]}, {"a": [1, 2]})
    assert not _same([1, 2], [2, 1])


def test_an_ability_in_the_definition_but_missing_from_the_build_cannot_be_patched() -> None:
    with pytest.raises(OverridesRejected) as caught:
        apply_override_patch(
            DEFINITION,
            None,
            {"abilities": {"save_visitor_details": {"enabled": False}}},
            abilities={"search_knowledge": REGISTRY["search_knowledge"]},
        )
    assert [r.code for r in caught.value.rejections] == [RejectionCode.ABILITY_UNKNOWN]
