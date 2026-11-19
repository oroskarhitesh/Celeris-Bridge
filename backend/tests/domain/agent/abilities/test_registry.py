"""
Tests for the registry, and for the join it exists to make: a definition may
only switch on an ability this build actually has.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

import pytest

from celeris_bridge.definitions import load_definition
from celeris_bridge.domain.agent.abilities import (
    Ability,
    AbilityContext,
    AbilityRegistry,
    AbilityResult,
    DuplicateAbility,
    UnknownAbility,
)
from celeris_bridge.domain.agent.definition_schema import validate_definition
from celeris_bridge.domain.agent.overrides import (
    OverridesRejected,
    RejectionCode,
    apply_override_patch,
    resolve_config,
)


async def handler(arguments: Mapping[str, Any], context: AbilityContext) -> AbilityResult:
    return AbilityResult()


def ability(key: str, **changed: Any) -> Ability:
    base: dict[str, Any] = {
        "key": key,
        "version": "1.0.0",
        "label": key.replace("_", " ").title(),
        "description": f"Does {key}.",
        "ai_description": f"Use this to {key}.",
        "effect": "read",
        "handler": handler,
    }
    return Ability(**{**base, **changed})


class TestHoldingAbilities:
    def test_a_new_registry_is_empty(self) -> None:
        assert len(AbilityRegistry()) == 0

    def test_an_ability_is_found_by_its_key(self) -> None:
        declared = ability("search_knowledge")
        registry = AbilityRegistry([declared])
        assert registry["search_knowledge"] is declared
        assert registry.get("search_knowledge") is declared
        assert registry.require("search_knowledge") is declared

    def test_two_abilities_cannot_claim_the_same_key(self) -> None:
        # The key is the tool name the model calls. Two answers to one name is
        # a coin toss decided by import order.
        registry = AbilityRegistry([ability("search_knowledge")])
        with pytest.raises(DuplicateAbility):
            registry.register(ability("search_knowledge"))

    def test_replacing_one_has_to_be_asked_for(self) -> None:
        registry = AbilityRegistry([ability("search_knowledge")])
        newer = ability("search_knowledge", version="2.0.0")
        registry.register(newer, replace=True)
        assert registry["search_knowledge"] is newer
        assert len(registry) == 1

    def test_asking_for_one_that_does_not_exist_says_what_does(self) -> None:
        registry = AbilityRegistry([ability("search_knowledge"), ability("book_slot")])
        with pytest.raises(UnknownAbility) as caught:
            registry.require("searchKnowledge")
        assert "book_slot" in str(caught.value)
        assert "search_knowledge" in str(caught.value)
        assert caught.value.key == "searchKnowledge"

    def test_a_missing_ability_can_also_be_asked_about_without_an_exception(self) -> None:
        # Reporting code wants None; the runner wants the exception.
        assert AbilityRegistry().get("search_knowledge") is None

    def test_it_behaves_as_a_mapping(self) -> None:
        registry = AbilityRegistry([ability("search_knowledge"), ability("book_slot")])
        assert "book_slot" in registry
        assert sorted(registry) == ["book_slot", "search_knowledge"]
        assert sorted(dict(registry)) == ["book_slot", "search_knowledge"]
        assert len(registry) == 2

    def test_two_registries_do_not_see_each_other(self) -> None:
        # The reason this is an object and not a module-level map: one test
        # registering an ability must not change what another test sees.
        one = AbilityRegistry([ability("search_knowledge")])
        other = AbilityRegistry()
        assert "search_knowledge" not in other
        assert "search_knowledge" in one


class TestWhatADefinitionMaySwitchOn:
    @pytest.fixture
    def registry(self) -> AbilityRegistry:
        return AbilityRegistry(
            [
                ability(
                    "search_knowledge",
                    settings={
                        "maxResults": {
                            "label": "Passages per search",
                            "type": "number",
                            "integer": True,
                            "min": 1,
                            "max": 10,
                            "default": 5,
                        },
                        "minRelevance": {
                            "label": "Minimum relevance",
                            "type": "number",
                            "min": 0,
                            "max": 1,
                            "default": 0.2,
                        },
                    },
                )
            ]
        )

    def marketing_with(self, abilities: list[dict[str, Any]]) -> dict[str, Any]:
        """The shipped definition plus ability references, left on disk alone."""
        raw = copy.deepcopy(load_definition("marketing_v1"))
        raw["abilities"] = abilities
        return raw

    def test_the_registry_drops_straight_into_the_validator(
        self, registry: AbilityRegistry
    ) -> None:
        # No adapter, no conversion: a registry is the Mapping the validator asks
        # for. This is the whole reason it is a Mapping.
        report = validate_definition(
            self.marketing_with([{"key": "search_knowledge", "enabledByDefault": True}]),
            abilities=registry,
        )
        assert report.ok, [f"{p.path}: {p.message}" for p in report.problems]

    def test_switching_on_an_ability_this_build_does_not_have_is_not_publishable(
        self, registry: AbilityRegistry
    ) -> None:
        report = validate_definition(
            self.marketing_with([{"key": "book_the_jet"}]), abilities=registry
        )
        assert not report.ok
        assert report.problems[0].path == "abilities[0].key"

    def test_a_definition_may_tune_an_abilitys_settings(
        self, registry: AbilityRegistry
    ) -> None:
        report = validate_definition(
            self.marketing_with([{"key": "search_knowledge", "settings": {"maxResults": 8}}]),
            abilities=registry,
        )
        assert report.ok

    def test_a_definition_cannot_tune_a_setting_the_ability_does_not_have(
        self, registry: AbilityRegistry
    ) -> None:
        report = validate_definition(
            self.marketing_with([{"key": "search_knowledge", "settings": {"maxResult": 8}}]),
            abilities=registry,
        )
        assert not report.ok
        assert report.problems[0].path == "abilities[0].settings.maxResult"

    def test_a_definition_cannot_set_a_value_the_ability_would_reject(
        self, registry: AbilityRegistry
    ) -> None:
        report = validate_definition(
            self.marketing_with([{"key": "search_knowledge", "settings": {"maxResults": 50}}]),
            abilities=registry,
        )
        assert not report.ok
        assert "at most 10" in report.problems[0].message


class TestAWorkspaceUsingThem:
    @pytest.fixture
    def registry(self) -> AbilityRegistry:
        return AbilityRegistry(
            [
                ability(
                    "search_knowledge",
                    settings={
                        # Two different switches: AbilityRef.editable says
                        # whether a workspace may turn the ability on and off,
                        # this says whether it may touch this one knob.
                        "maxResults": {
                            "label": "Passages per search",
                            "type": "number",
                            "integer": True,
                            "min": 1,
                            "max": 10,
                            "default": 5,
                            "editable": True,
                        },
                        "minRelevance": {
                            "label": "Minimum relevance",
                            "type": "number",
                            "min": 0,
                            "max": 1,
                            "default": 0.2,
                        },
                    },
                )
            ]
        )

    @pytest.fixture
    def definition(self, registry: AbilityRegistry):
        raw = copy.deepcopy(load_definition("marketing_v1"))
        raw["abilities"] = [
            {
                "key": "search_knowledge",
                "enabledByDefault": True,
                "editable": True,
                "settings": {"maxResults": 6},
            }
        ]
        report = validate_definition(raw, abilities=registry)
        assert report.definition is not None
        return report.definition

    def test_the_definitions_value_beats_the_abilitys_own_default(
        self, definition, registry: AbilityRegistry
    ) -> None:
        config = resolve_config(definition, None, abilities=registry)
        resolved = config.ability("search_knowledge")
        assert resolved is not None
        assert resolved.enabled is True
        assert resolved.settings["maxResults"] == 6

    def test_a_workspace_can_tune_it_further(self, definition, registry: AbilityRegistry) -> None:
        stored = apply_override_patch(
            definition,
            None,
            {"abilities": {"search_knowledge": {"settings": {"maxResults": 9}}}},
            abilities=registry,
        )
        config = resolve_config(definition, stored, abilities=registry)
        assert config.ability("search_knowledge").settings["maxResults"] == 9

    def test_a_workspace_cannot_exceed_the_abilitys_own_limit(
        self, definition, registry: AbilityRegistry
    ) -> None:
        with pytest.raises(OverridesRejected) as caught:
            apply_override_patch(
                definition,
                None,
                {"abilities": {"search_knowledge": {"settings": {"maxResults": 999}}}},
                abilities=registry,
            )
        assert caught.value.rejections[0].code is RejectionCode.ABILITY_SETTING_INVALID

    def test_a_workspace_cannot_touch_a_knob_the_ability_keeps_to_itself(
        self, definition, registry: AbilityRegistry
    ) -> None:
        # minRelevance is tuned against a particular embedding model. Switching
        # the ability on is the workspace's call; that number is not.
        with pytest.raises(OverridesRejected) as caught:
            apply_override_patch(
                definition,
                None,
                {"abilities": {"search_knowledge": {"settings": {"minRelevance": 0.9}}}},
                abilities=registry,
            )
        assert caught.value.rejections[0].code is RejectionCode.ABILITY_SETTING_LOCKED

    def test_switching_the_ability_off_is_the_workspaces_call(
        self, definition, registry: AbilityRegistry
    ) -> None:
        stored = apply_override_patch(
            definition,
            None,
            {"abilities": {"search_knowledge": {"enabled": False}}},
            abilities=registry,
        )
        config = resolve_config(definition, stored, abilities=registry)
        assert config.ability("search_knowledge").enabled is False
        assert config.enabled_abilities == ()

    def test_an_ability_removed_from_the_build_is_reported_not_hollowed_out(
        self, definition
    ) -> None:
        # A published definition naming an ability that no longer exists means
        # a deploy removed something still in use. The reference resolves it as
        # present-and-enabled with no settings; this says so instead.
        config = resolve_config(definition, None, abilities=AbilityRegistry())
        assert config.missing_abilities == ("search_knowledge",)
        assert config.ability("search_knowledge") is None
