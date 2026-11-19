"""
The definitions this build ships must publish cleanly, and must behave once a
workspace starts editing them.

Everything here runs against the real ``marketing_v1.json``, not a fixture. A
definition that only passes against a hand-made document proves nothing about
the one that reaches a customer.
"""

from __future__ import annotations

import pytest

from celeris_bridge.definitions import load_definition, shipped_definitions
from celeris_bridge.domain.agent.abilities.builtins import builtin_abilities
from celeris_bridge.domain.agent.definition_schema import (
    AgentDefinition,
    defaults_of,
    placeholders_in,
    validate_definition,
)
from celeris_bridge.domain.agent.overrides import (
    OverridesRejected,
    RejectionCode,
    apply_override_patch,
    resolve_config,
)
from celeris_bridge.domain.agent.rules import evaluate_rules

# The real registry, not a fake. A shipped definition may only switch on an
# ability this build actually has, and that is precisely what these tests are
# for: catching the deploy that removes an ability a published document names.
ABILITIES = builtin_abilities()


def report_for(key: str):
    return validate_definition(load_definition(key), abilities=ABILITIES)


@pytest.mark.parametrize("key", shipped_definitions())
def test_every_shipped_definition_is_publishable(key: str) -> None:
    report = report_for(key)
    assert report.ok, [f"{p.path}: {p.message}" for p in report.problems]


@pytest.fixture
def marketing() -> AgentDefinition:
    report = report_for("marketing_v1")
    assert report.definition is not None
    return report.definition


class TestTheDocumentHangsTogether:
    def test_there_is_at_least_one_definition_to_ship(self) -> None:
        assert "marketing_v1" in shipped_definitions()

    def test_every_setting_belongs_to_a_declared_group(
        self, marketing: AgentDefinition
    ) -> None:
        # Checked by validate_definition only when a group is named. A setting
        # with no group at all is legal but lands at the bottom of the admin
        # form, away from the things it relates to.
        ungrouped = [key for key, spec in marketing.settings.items() if not spec.group]
        assert ungrouped == []

    def test_a_workspace_can_edit_everything_that_is_not_the_platforms_business(
        self, marketing: AgentDefinition
    ) -> None:
        locked = {
            key
            for key, spec in marketing.settings.items()
            if not spec.editable and not spec.admin_only
        }
        assert locked == set()

    def test_model_choices_stay_with_the_platform(
        self, marketing: AgentDefinition
    ) -> None:
        for key in ("chatModel", "replyEffort", "knowledgeSnippets"):
            spec = marketing.settings[key]
            assert spec.admin_only is True
            assert spec.editable is False

    def test_the_defaults_satisfy_the_definitions_own_rules(
        self, marketing: AgentDefinition
    ) -> None:
        assert evaluate_rules(marketing.rules, defaults_of(marketing)) == []

    def test_locked_rules_are_not_templated(self, marketing: AgentDefinition) -> None:
        # The engine appends these verbatim, so a placeholder would reach the
        # model as braces. Asserted here as well as in the validator because
        # this is the one that would ship.
        for rule in marketing.locked_rules:
            assert placeholders_in(rule.text) == []

    def test_a_blank_workspace_renders_only_the_unconditional_blocks(
        self, marketing: AgentDefinition
    ) -> None:
        # On defaults, companyBlurb, offerings, websiteUrl, bookingUrl,
        # handoffEmail and signOff are all unset. Their blocks must drop out,
        # or a new workspace's prompt carries empty headings the model then
        # tries to make sense of.
        values = defaults_of(marketing)
        rendered = [
            block.id
            for block in marketing.instructions
            if block.when is None or _condition_holds(block, values)
        ]
        assert rendered == [
            "role",
            "tone",
            "one_question",
            "qualify",
            "off_limits",
            "competitors_decline",
        ]


def _condition_holds(block, values: dict) -> bool:
    """A stand-in for the engine, which does not exist yet.

    Deliberately crude: it exists to prove the *definition* switches blocks on
    and off sensibly, not to be the engine's implementation.
    """
    assert block.when is not None
    value = values[block.when.setting]
    if block.when.tests_equality:
        return value == block.when.equals
    return value not in (None, "", [], {})


class TestAWorkspaceEditingIt:
    def test_a_workspace_that_changes_nothing_stores_nothing(
        self, marketing: AgentDefinition
    ) -> None:
        stored = apply_override_patch(marketing, None, {}, abilities=ABILITIES)
        assert stored.settings == {}

    def test_restating_a_default_is_not_an_override(
        self, marketing: AgentDefinition
    ) -> None:
        # What the settings form does on every save: it posts every field it
        # shows, whether or not the person touched it.
        every_editable = {
            key: spec.default
            for key, spec in marketing.settings.items()
            if spec.editable
        }
        stored = apply_override_patch(
            marketing, None, {"settings": every_editable}, abilities=ABILITIES
        )
        assert stored.settings == {}

    def test_a_real_edit_is_stored_and_nothing_else_is(
        self, marketing: AgentDefinition
    ) -> None:
        stored = apply_override_patch(
            marketing,
            None,
            {"settings": {"agentName": "Noor", "tone": "direct"}},
            abilities=ABILITIES,
        )
        assert stored.settings == {"agentName": "Noor", "tone": "direct"}

        config = resolve_config(marketing, stored, abilities=ABILITIES)
        assert config.settings["agentName"] == "Noor"
        assert config.settings["greeting"] == marketing.settings["greeting"].default

    def test_a_workspace_cannot_choose_its_own_model(
        self, marketing: AgentDefinition
    ) -> None:
        with pytest.raises(OverridesRejected) as caught:
            apply_override_patch(
                marketing,
                None,
                {"settings": {"chatModel": "claude-opus-5-5"}},
                abilities=ABILITIES,
            )
        codes = [r.code for r in caught.value.rejections]
        assert codes == [RejectionCode.SETTING_LOCKED]

    def test_removing_the_email_field_is_refused(
        self, marketing: AgentDefinition
    ) -> None:
        # The rule exists because the booking confirmation has nowhere to go
        # without it. A workspace may reorder and relabel the rest.
        without_email = [
            row
            for row in marketing.settings["qualificationFields"].default
            if row["key"] != "email"
        ]
        with pytest.raises(OverridesRejected) as caught:
            apply_override_patch(
                marketing,
                None,
                {"settings": {"qualificationFields": without_email}},
                abilities=ABILITIES,
            )
        rejection = caught.value.rejections[0]
        assert rejection.code is RejectionCode.SETTINGS_RULE
        assert rejection.path == "settings.qualificationFields"

    def test_making_the_email_field_optional_is_refused(
        self, marketing: AgentDefinition
    ) -> None:
        relaxed = [
            {**row, "requiredToBook": False} if row["key"] == "email" else row
            for row in marketing.settings["qualificationFields"].default
        ]
        with pytest.raises(OverridesRejected) as caught:
            apply_override_patch(
                marketing, None, {"settings": {"qualificationFields": relaxed}},
                abilities=ABILITIES,
            )
        assert caught.value.rejections[0].code is RejectionCode.SETTINGS_RULE

    def test_reordering_and_extending_the_list_is_allowed(
        self, marketing: AgentDefinition
    ) -> None:
        rows = list(reversed(marketing.settings["qualificationFields"].default))
        rows.append(
            {
                "key": "current_tool",
                "question": "What are you using for this today?",
                "requiredToBook": False,
                "format": "text",
            }
        )
        stored = apply_override_patch(
            marketing, None, {"settings": {"qualificationFields": rows}},
            abilities=ABILITIES,
        )
        assert len(stored.settings["qualificationFields"]) == 5

    def test_an_unset_handover_address_stays_unset(
        self, marketing: AgentDefinition
    ) -> None:
        # handoffEmail is nullable rather than "": a pattern cannot match an
        # empty string, so null is how this setting says "not configured".
        config = resolve_config(marketing, None, abilities=ABILITIES)
        assert config.settings["handoffEmail"] is None

    def test_a_malformed_handover_address_is_refused(
        self, marketing: AgentDefinition
    ) -> None:
        with pytest.raises(OverridesRejected) as caught:
            apply_override_patch(
                marketing, None, {"settings": {"handoffEmail": "sales at example.com"}},
                abilities=ABILITIES,
            )
        assert caught.value.rejections[0].code is RejectionCode.SETTING_INVALID

    def test_a_booking_link_must_be_a_real_address(
        self, marketing: AgentDefinition
    ) -> None:
        with pytest.raises(OverridesRejected):
            apply_override_patch(
                marketing, None, {"settings": {"bookingUrl": "example.com/demo"}},
                abilities=ABILITIES,
            )
