"""
Tests for record/document mapping.

No database. This is where the bugs that matter live: a wrong query fails on
the first call, a wrong mapping loses a field quietly and is noticed a week
later when somebody's setting has reverted to the default.

Two properties are asserted over and over: a record survives a round trip, and
a document missing a field this build expects still reads.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId

from celeris_bridge.adapters.mongo.codec import (
    conversation_from_document,
    conversation_to_document,
    identifier,
    instance_from_document,
    instance_to_document,
    instance_version_from_document,
    instance_version_to_document,
    object_id,
    overrides_from_document,
    overrides_to_document,
    template_from_document,
    template_to_document,
    template_version_from_document,
    template_version_to_document,
    turn_from_document,
    turn_to_document,
)
from celeris_bridge.domain.agent.overrides import AbilityOverride, Overrides
from celeris_bridge.domain.agent.records import (
    AgentInstance,
    AgentInstanceVersion,
    AgentTemplate,
    AgentTemplateVersion,
    Deployment,
    InstanceStatus,
    PausedReason,
    TemplateStatus,
)
from celeris_bridge.domain.conversation import Channel, Conversation, Speaker, Turn

WHEN = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)


class TestIds:
    def test_an_object_id_becomes_a_string(self) -> None:
        stored = ObjectId()
        assert identifier(stored) == str(stored)

    def test_a_string_id_survives_a_round_trip(self) -> None:
        stored = ObjectId()
        assert object_id(identifier(stored)) == stored

    def test_a_missing_id_is_an_empty_string_not_a_crash(self) -> None:
        assert identifier(None) == ""

    def test_an_id_that_is_not_an_object_id_is_kept_as_it_is(self) -> None:
        # Seeded fixtures and imported data have their own keys. A lookup that
        # cannot match should return nothing, not raise.
        assert object_id("marketing_v1") == "marketing_v1"

    def test_the_domain_never_sees_a_bson_type(self) -> None:
        document = instance_to_document(
            AgentInstance(id=str(ObjectId()), workspace_id="w1", template_key="k", label="L")
        )
        record = instance_from_document(document)
        assert isinstance(record.id, str)


class TestOverrides:
    def test_a_round_trip_keeps_settings_and_abilities(self) -> None:
        overrides = Overrides(
            settings={"tone": "formal", "maxTurns": 30},
            abilities={
                "search_knowledge": AbilityOverride(
                    enabled=False, settings={"maxPassages": 8}
                )
            },
        )
        back = overrides_from_document(overrides_to_document(overrides))
        assert back.settings == overrides.settings
        assert back.abilities["search_knowledge"].enabled is False
        assert back.abilities["search_knowledge"].settings == {"maxPassages": 8}

    def test_an_ability_that_was_only_tuned_keeps_enabled_unset(self) -> None:
        # "enabled is None" means the workspace never touched the switch, which
        # is different from switching it on. Writing False here would freeze it.
        overrides = Overrides(
            abilities={"search_knowledge": AbilityOverride(settings={"maxPassages": 8})}
        )
        document = overrides_to_document(overrides)
        assert "enabled" not in document["abilities"]["search_knowledge"]
        back = overrides_from_document(document)
        assert back.abilities["search_knowledge"].enabled is None

    def test_nothing_overridden_round_trips_as_nothing(self) -> None:
        back = overrides_from_document(overrides_to_document(Overrides()))
        assert back.settings == {}
        assert back.abilities == {}

    @pytest.mark.parametrize("document", [None, {}, "nonsense", 7, []])
    def test_a_document_that_is_not_overrides_reads_as_empty(self, document) -> None:
        back = overrides_from_document(document)
        assert back.settings == {} and back.abilities == {}

    def test_a_malformed_ability_entry_is_skipped_not_fatal(self) -> None:
        back = overrides_from_document(
            {"abilities": {"good": {"settings": {"a": 1}}, "bad": "not an object"}}
        )
        assert set(back.abilities) == {"good"}


class TestTemplates:
    def test_a_template_survives_a_round_trip(self) -> None:
        template = AgentTemplate(
            key="marketing_v1",
            draft={"identity": {"name": "Marketing"}},
            status=TemplateStatus.ARCHIVED,
            draft_revision=7,
            draft_updated_at=WHEN,
            latest_version=3,
            last_published_at=WHEN,
            copied_from="support_v1",
        )
        back = template_from_document(template_to_document(template))
        assert back == template

    def test_the_key_is_the_document_id(self) -> None:
        # Permanent and unique by nature, so it needs no second index.
        document = template_to_document(AgentTemplate(key="marketing_v1"))
        assert document["_id"] == "marketing_v1"

    def test_a_document_from_an_older_build_still_reads(self) -> None:
        back = template_from_document({"_id": "marketing_v1"})
        assert back.key == "marketing_v1"
        assert back.status is TemplateStatus.ACTIVE
        assert back.draft == {}
        assert back.draft_revision == 0
        assert back.latest_version is None

    def test_a_status_this_build_does_not_know_falls_back(self) -> None:
        # Written by a newer build. Falling back beats refusing to read it and
        # taking the template out of the catalogue.
        back = template_from_document({"_id": "k", "status": "quarantined"})
        assert back.status is TemplateStatus.ACTIVE

    def test_a_published_version_survives_a_round_trip(self) -> None:
        version = AgentTemplateVersion(
            template_key="marketing_v1",
            version=4,
            definition={"identity": {"name": "Marketing"}},
            change_summary="better greeting",
            published_at=WHEN,
            published_by="admin-1",
        )
        assert template_version_from_document(template_version_to_document(version)) == version

    def test_a_version_with_no_publisher_reads_as_none(self) -> None:
        back = template_version_from_document({"templateKey": "k", "version": 1})
        assert back.published_by is None
        assert back.definition == {}


class TestInstances:
    def test_an_instance_survives_a_round_trip(self) -> None:
        instance = AgentInstance(
            id=str(ObjectId()),
            workspace_id="w1",
            template_key="marketing_v1",
            label="Main website",
            draft_template_version=2,
            draft_overrides=Overrides(settings={"tone": "formal"}),
            draft_revision=4,
            status=InstanceStatus.PAUSED,
            paused_reason=PausedReason.ACCESS_REVOKED,
            paused_from=InstanceStatus.LIVE,
            deployment=Deployment(
                allowed_origins=("https://acme.test",),
                widget_enabled=False,
                launcher_position="left",
                launcher_text="Chat to us",
                last_seen_at=WHEN,
                last_seen_origin="https://acme.test",
            ),
            published_version=3,
            last_published_at=WHEN,
            created_at=WHEN,
            updated_at=WHEN,
            deleted_at=None,
        )
        back = instance_from_document(instance_to_document(instance))
        assert back == instance

    def test_what_to_restore_on_un_pause_is_kept(self) -> None:
        # Without it, un-pausing has to guess, and guessing "live" puts an
        # agent back on a website by accident.
        instance = AgentInstance(
            id="i1", workspace_id="w1", template_key="k", label="L"
        ).paused(PausedReason.ACCESS_REVOKED)
        back = instance_from_document(instance_to_document(instance))
        assert back.paused_from is InstanceStatus.DRAFT
        assert back.resumed().status is InstanceStatus.DRAFT

    def test_a_new_instance_is_written_without_an_id(self) -> None:
        # So the database assigns one.
        document = instance_to_document(
            AgentInstance(id="", workspace_id="w1", template_key="k", label="L")
        )
        assert "_id" not in document

    def test_a_document_from_an_older_build_still_reads(self) -> None:
        back = instance_from_document(
            {"_id": "i1", "workspaceId": "w1", "templateKey": "k", "label": "L"}
        )
        assert back.status is InstanceStatus.DRAFT
        assert back.draft_template_version == 1
        assert back.deployment.widget_enabled is True
        assert back.deployment.allowed_origins == ()
        assert back.paused_reason is None

    def test_a_launcher_position_that_is_nonsense_becomes_right(self) -> None:
        back = instance_from_document({"deployment": {"launcherPosition": "ceiling"}})
        assert back.deployment.launcher_position == "right"

    def test_an_instance_version_survives_a_round_trip(self) -> None:
        version = AgentInstanceVersion(
            id=str(ObjectId()),
            workspace_id="w1",
            instance_id=str(ObjectId()),
            version=5,
            template_key="marketing_v1",
            template_version=2,
            overrides=Overrides(settings={"greeting": "Hi!"}),
            change_summary="new greeting",
            rolled_back_from=3,
            published_at=WHEN,
            published_by="user-1",
        )
        back = instance_version_from_document(instance_version_to_document(version))
        assert back == version

    def test_the_template_version_defaults_to_one_not_zero(self) -> None:
        # Version 0 does not exist, so a missing field must not resolve to a
        # version nothing can be loaded from.
        back = instance_version_from_document({"version": 1})
        assert back.template_version == 1


class TestConversations:
    def test_a_turn_survives_a_round_trip(self) -> None:
        turn = Turn(
            speaker=Speaker.AGENT,
            text="The Starter plan is 29 per seat.",
            at=WHEN,
            calls=({"key": "search_knowledge", "ok": True},),
            sources=({"name": "pricing.pdf"},),
            ending="answered",
            model="claude-opus-5-5",
            input_tokens=120,
            output_tokens=34,
        )
        assert turn_from_document(turn_to_document(turn)) == turn

    def test_a_conversation_survives_a_round_trip(self) -> None:
        conversation = Conversation(
            id=str(ObjectId()),
            workspace_id="w1",
            instance_id=str(ObjectId()),
            channel=Channel.EMAIL,
            turns=(
                Turn(speaker=Speaker.VISITOR, text="how much?", at=WHEN),
                Turn(speaker=Speaker.AGENT, text="29 per seat.", at=WHEN),
            ),
            template_version=2,
            instance_version=3,
            page_url="https://acme.test/pricing",
            page_title="Pricing",
            started_at=WHEN,
            updated_at=WHEN,
        )
        assert conversation_from_document(conversation_to_document(conversation)) == conversation

    def test_a_speaker_this_build_does_not_know_does_not_lose_the_turn(self) -> None:
        # The text is the thing somebody needs to read.
        back = turn_from_document({"speaker": "supervisor", "text": "please hold"})
        assert back.text == "please hold"
        assert back.speaker is Speaker.VISITOR

    def test_a_malformed_turn_in_the_list_is_skipped(self) -> None:
        back = conversation_from_document(
            {"turns": [{"speaker": "agent", "text": "ok"}, "nonsense", None]}
        )
        assert len(back.turns) == 1

    def test_an_empty_conversation_reads(self) -> None:
        back = conversation_from_document({"_id": "c1", "workspaceId": "w1"})
        assert back.turns == ()
        assert back.channel is Channel.WEB_CHAT


class TestDates:
    def test_a_naive_stored_date_comes_back_aware(self) -> None:
        # Mongo stores UTC and hands it back naive. Comparing a naive date with
        # an aware one raises, and the comparison that matters is "is this
        # booking in the past".
        back = template_from_document({"_id": "k", "draftUpdatedAt": datetime(2026, 11, 10, 14, 30)})
        assert back.draft_updated_at is not None
        assert back.draft_updated_at.tzinfo is timezone.utc

    def test_an_aware_date_is_left_alone(self) -> None:
        back = template_from_document({"_id": "k", "draftUpdatedAt": WHEN})
        assert back.draft_updated_at == WHEN

    def test_something_that_is_not_a_date_reads_as_none(self) -> None:
        back = template_from_document({"_id": "k", "draftUpdatedAt": "yesterday"})
        assert back.draft_updated_at is None
