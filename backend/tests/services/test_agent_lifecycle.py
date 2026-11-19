"""
Tests for the agent lifecycle: types, instances, and resolving one to run.

The real in-memory stores, the real registry, the real shipped definition.
Nothing is faked here at all — the stores are adapters that pass the same
contract suite the Mongo ones do, so a test that passes here is a test of the
services and not of a mock's memory of them.

What these pin down is the set of invariants that span two records and so
cannot live on either:

* a definition is validated **before** it is published, never after,
* an instance pins a template version and only moves off it when asked,
* live traffic reads the published snapshot and never the draft,
* a rollback is a new version, and no version is ever edited,
* nothing lets one workspace see or change another's agent.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest

from celeris_bridge.adapters.in_memory_stores import (
    InMemoryInstanceStore,
    InMemoryTemplateStore,
)
from celeris_bridge.definitions import load_definition
from celeris_bridge.domain.agent.abilities.builtins import builtin_abilities
from celeris_bridge.domain.agent.overrides import OverridesRejected, RejectionCode
from celeris_bridge.domain.agent.records import (
    Deployment,
    InstanceStatus,
    PausedReason,
    RevisionConflict,
    TemplateStatus,
)
from celeris_bridge.services.instances import (
    InstanceLimitReached,
    InstanceService,
    NothingToPublish,
)
from celeris_bridge.services.resolution import (
    AgentNotFound,
    AgentNotLive,
    BrokenDefinition,
    DefinitionCache,
    resolve_draft,
    resolve_live,
)
from celeris_bridge.services.templates import (
    DefinitionInvalid,
    TemplateNotFound,
    TemplateNotPublishable,
    TemplateService,
)

KEY = "marketing_v1"


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


@pytest.fixture
def world():
    """A published agent type and an empty workspace."""
    registry = builtin_abilities()
    templates = InMemoryTemplateStore()
    instances = InMemoryInstanceStore()
    cache = DefinitionCache()
    types = TemplateService(templates=templates, registry=registry)
    agents = InstanceService(
        instances=instances, templates=templates, registry=registry, cache=cache
    )

    document = load_definition(KEY)
    template = run(types.create(key=KEY, draft=document))
    run(types.publish(KEY, expect_revision=template.draft_revision))

    return {
        "registry": registry,
        "templates": templates,
        "instances": instances,
        "types": types,
        "agents": agents,
        "cache": cache,
        "document": document,
    }


def live(world: dict[str, Any], instance_id: str):
    return run(
        resolve_live(
            workspace_id="w1",
            instance_id=instance_id,
            templates=world["templates"],
            instances=world["instances"],
            registry=world["registry"],
            cache=world["cache"],
        )
    )


def draft(world: dict[str, Any], instance_id: str, *, workspace_id: str = "w1"):
    return run(
        resolve_draft(
            workspace_id=workspace_id,
            instance_id=instance_id,
            templates=world["templates"],
            instances=world["instances"],
            registry=world["registry"],
            cache=world["cache"],
        )
    )


def make_agent(world: dict[str, Any], *, workspace_id: str = "w1", publish: bool = True, **changed):
    agents: InstanceService = world["agents"]
    instance = run(
        agents.create(
            workspace_id=workspace_id,
            template_key=KEY,
            label=changed.pop("label", "Website agent"),
            **changed,
        )
    )
    if publish:
        run(
            agents.publish(
                workspace_id, instance.id, expect_revision=instance.draft_revision
            )
        )
    return run(agents.get(workspace_id, instance.id))


def publish_another_version(world: dict[str, Any], change) -> int:
    """Edit the draft definition, publish it, and return the new version."""
    types: TemplateService = world["types"]
    template = run(types.get(KEY))
    document = deepcopy(dict(template.draft))
    change(document)
    saved = run(types.save_draft(KEY, document, expect_revision=template.draft_revision))
    version = run(types.publish(KEY, expect_revision=saved.draft_revision))
    return version.version


class TestPublishingAnAgentType:
    def test_the_shipped_definition_publishes(self, world) -> None:
        latest = run(world["templates"].latest(KEY))
        assert latest.version == 1

    def test_a_draft_may_be_as_broken_as_you_like(self, world) -> None:
        # A definition is written in several passes. A draft that has to be
        # valid to be saved cannot be written at all.
        types: TemplateService = world["types"]
        template = run(types.get(KEY))
        saved = run(
            types.save_draft(KEY, {"identity": {}}, expect_revision=template.draft_revision)
        )
        assert saved.draft == {"identity": {}}

    def test_but_it_does_not_publish(self, world) -> None:
        types: TemplateService = world["types"]
        template = run(types.get(KEY))
        saved = run(types.save_draft(KEY, {"nope": 1}, expect_revision=template.draft_revision))
        with pytest.raises(DefinitionInvalid):
            run(types.publish(KEY, expect_revision=saved.draft_revision))

    def test_a_refused_publish_writes_no_version(self, world) -> None:
        # The check has to be before both writes, or there is a published
        # version nothing can resolve.
        types: TemplateService = world["types"]
        template = run(types.get(KEY))
        saved = run(types.save_draft(KEY, {"nope": 1}, expect_revision=template.draft_revision))
        with pytest.raises(DefinitionInvalid):
            run(types.publish(KEY, expect_revision=saved.draft_revision))
        assert run(world["templates"].latest(KEY)).version == 1

    def test_every_problem_is_reported_at_once(self, world) -> None:
        # So a form marks all its bad fields, rather than one per round trip.
        types: TemplateService = world["types"]
        template = run(types.get(KEY))
        broken = deepcopy(dict(template.draft))
        broken["abilities"] = [{"key": "no_such_ability", "enabledByDefault": True}]
        broken["settings"] = {"brandVoice": {"type": "nonsense"}}
        saved = run(types.save_draft(KEY, broken, expect_revision=template.draft_revision))
        with pytest.raises(DefinitionInvalid) as caught:
            run(types.publish(KEY, expect_revision=saved.draft_revision))
        assert len(caught.value.problems) >= 2

    def test_an_ability_this_build_does_not_have_is_caught(self, world) -> None:
        # The reason the registry is a required argument: an unchecked ability
        # reference publishes and then fails on a visitor.
        types: TemplateService = world["types"]
        template = run(types.get(KEY))
        document = deepcopy(dict(template.draft))
        document["abilities"].append({"key": "launch_rocket", "enabledByDefault": True})
        saved = run(types.save_draft(KEY, document, expect_revision=template.draft_revision))
        with pytest.raises(DefinitionInvalid) as caught:
            run(types.publish(KEY, expect_revision=saved.draft_revision))
        assert any("launch_rocket" in message for _, message in caught.value.problems)

    def test_check_draft_says_what_publishing_would_do(self, world) -> None:
        types: TemplateService = world["types"]
        template = run(types.get(KEY))
        document = deepcopy(dict(template.draft))
        document["settings"]["newKnob"] = {
            "type": "string",
            "label": "A new knob",
            "default": "",
        }
        # ``greeting`` because the instructions do not interpolate it: removing
        # one they do would fail validation for an unrelated reason and this
        # test would stop being about removal.
        removed = "greeting"
        document["settings"].pop(removed)
        run(types.save_draft(KEY, document, expect_revision=template.draft_revision))

        check = run(types.check_draft(KEY))
        assert check.ok is True
        assert "newKnob" in check.added_settings
        # The one that matters: removing a setting drops every workspace's
        # value for it, and that should be visible before the button is pressed.
        assert removed in check.removed_settings

    def test_check_draft_never_raises(self, world) -> None:
        types: TemplateService = world["types"]
        template = run(types.get(KEY))
        run(types.save_draft(KEY, {"rubbish": True}, expect_revision=template.draft_revision))
        check = run(types.check_draft(KEY))
        assert check.ok is False
        assert check.problems

    def test_an_archived_type_takes_no_new_versions(self, world) -> None:
        types: TemplateService = world["types"]
        template = run(types.get(KEY))
        run(
            types.set_status(
                KEY, TemplateStatus.ARCHIVED, expect_revision=template.draft_revision
            )
        )
        with pytest.raises(TemplateNotPublishable):
            run(types.publish(KEY, expect_revision=run(types.get(KEY)).draft_revision))

    def test_a_version_is_never_edited_to_go_back(self, world) -> None:
        # A rollback restores the *draft* from a version. The history stays a
        # history and publishing is still a deliberate second step.
        types: TemplateService = world["types"]
        second = publish_another_version(
            world, lambda document: document["identity"].update({"name": "Marketing II"})
        )
        template = run(types.get(KEY))
        run(types.restore_draft_from(KEY, 1, expect_revision=template.draft_revision))

        assert run(types.version(KEY, second)).definition["identity"]["name"] == "Marketing II"
        assert run(types.get(KEY)).latest_version == second

    def test_an_unknown_type_is_not_found(self, world) -> None:
        with pytest.raises(TemplateNotFound):
            run(world["types"].get("email_triage_v1"))


class TestCreatingAnAgent:
    def test_it_starts_as_a_draft(self, world) -> None:
        # An agent appears when its owner publishes it, not when they name it.
        agents: InstanceService = world["agents"]
        instance = run(
            agents.create(workspace_id="w1", template_key=KEY, label="Website agent")
        )
        assert instance.status is InstanceStatus.DRAFT
        assert instance.published_version is None

    def test_it_is_pinned_to_the_latest_version(self, world) -> None:
        instance = make_agent(world, publish=False)
        assert instance.draft_template_version == 1

    def test_a_value_set_at_creation_goes_through_the_real_validator(self, world) -> None:
        # No back door that accepts something the product would not.
        agents: InstanceService = world["agents"]
        with pytest.raises(OverridesRejected) as caught:
            run(
                agents.create(
                    workspace_id="w1",
                    template_key=KEY,
                    label="x",
                    patch={"settings": {"noSuchSetting": 1}},
                )
            )
        assert caught.value.rejections[0].code is RejectionCode.SETTING_UNKNOWN

    def test_only_what_differs_is_stored(self, world) -> None:
        # What makes a better default reach every workspace that never touched
        # the setting.
        agents: InstanceService = world["agents"]
        instance = run(
            agents.create(
                workspace_id="w1",
                template_key=KEY,
                label="x",
                patch={"settings": {"greeting": "Hello there"}},
            )
        )
        assert set(instance.draft_overrides.settings) == {"greeting"}

    def test_the_definitions_own_limit_is_enforced(self, world) -> None:
        agents: InstanceService = world["agents"]
        limit = run(world["types"].check_draft(KEY)).definition.limits.max_agents_per_workspace
        for number in range(limit):
            run(agents.create(workspace_id="w1", template_key=KEY, label=f"a{number}"))
        with pytest.raises(InstanceLimitReached):
            run(agents.create(workspace_id="w1", template_key=KEY, label="one too many"))

    def test_the_limit_is_per_workspace(self, world) -> None:
        agents: InstanceService = world["agents"]
        limit = run(world["types"].check_draft(KEY)).definition.limits.max_agents_per_workspace
        for number in range(limit):
            run(agents.create(workspace_id="w1", template_key=KEY, label=f"a{number}"))
        assert run(agents.create(workspace_id="w2", template_key=KEY, label="theirs"))

    def test_an_unpublished_type_cannot_have_agents(self, world) -> None:
        with pytest.raises(BrokenDefinition):
            run(
                world["agents"].create(
                    workspace_id="w1", template_key="never_published", label="x"
                )
            )


class TestEditingSettings:
    def test_a_change_lands_on_the_draft(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        run(
            agents.edit(
                "w1",
                instance.id,
                {"settings": {"greeting": "Hello there"}},
                expect_revision=instance.draft_revision,
            )
        )
        assert run(agents.get("w1", instance.id)).draft_overrides.settings == {
            "greeting": "Hello there"
        }

    def test_and_not_on_what_visitors_are_talking_to(self, world) -> None:
        # An admin typing in a form is not changing the live agent. This is
        # the single most important separation in the product.
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        before = live(world, instance.id).config.settings["greeting"]
        run(
            agents.edit(
                "w1",
                instance.id,
                {"settings": {"greeting": "A new greeting"}},
                expect_revision=instance.draft_revision,
            )
        )
        assert live(world, instance.id).config.settings["greeting"] == before
        assert draft(world, instance.id).config.settings["greeting"] == "A new greeting"

    def test_an_invalid_change_is_refused_in_full(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        with pytest.raises(OverridesRejected):
            run(
                agents.edit(
                    "w1",
                    instance.id,
                    {"settings": {"greeting": "Hello there", "noSuchSetting": 1}},
                    expect_revision=instance.draft_revision,
                )
            )
        # Nothing applied, including the half that was fine.
        assert run(agents.get("w1", instance.id)).draft_overrides.settings == {}

    def test_two_admins_with_the_same_form_open_do_not_overwrite_each_other(
        self, world
    ) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        stale = instance.draft_revision
        run(
            agents.edit(
                "w1", instance.id, {"settings": {"greeting": "First"}},
                expect_revision=stale,
            )
        )
        with pytest.raises(RevisionConflict):
            run(
                agents.edit(
                    "w1", instance.id, {"settings": {"greeting": "Second"}},
                    expect_revision=stale,
                )
            )
        assert run(agents.get("w1", instance.id)).draft_overrides.settings == {
            "greeting": "First"
        }

    def test_resetting_is_a_deletion_and_not_a_write(self, world) -> None:
        # So the setting follows the default from now on, including when a
        # later version improves it.
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        edited = run(
            agents.edit(
                "w1", instance.id, {"settings": {"greeting": "Hello there"}},
                expect_revision=instance.draft_revision,
            )
        )
        reset = run(
            agents.reset(
                "w1", instance.id, settings=["greeting"],
                expect_revision=edited.draft_revision,
            )
        )
        assert reset.draft_overrides.settings == {}

    def test_the_settings_form_has_both_the_declaration_and_the_value(self, world) -> None:
        # The form needs the declaration to draw a control and the resolved
        # value to fill it in.
        instance = make_agent(world)
        definition, config = run(world["agents"].settings_form("w1", instance.id))
        assert "greeting" in definition.settings
        assert "greeting" in config.settings


class TestPublishingAnAgent:
    def test_publishing_is_what_makes_it_live(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world, publish=False)
        with pytest.raises(AgentNotLive):
            live(world, instance.id)
        run(agents.publish("w1", instance.id, expect_revision=instance.draft_revision))
        assert live(world, instance.id).instance_version == 1

    def test_a_snapshot_records_the_template_version_it_was_published_against(
        self, world
    ) -> None:
        # Which is what live traffic follows afterwards, not the template's
        # latest.
        instance = make_agent(world)
        snapshot = run(world["instances"].published("w1", instance.id))
        assert snapshot.template_version == 1

    def test_publishing_the_same_draft_twice_is_refused(self, world) -> None:
        # A list of twenty identical versions is a list nobody can find
        # anything in.
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        with pytest.raises(NothingToPublish):
            run(agents.publish("w1", instance.id, expect_revision=instance.draft_revision))

    def test_each_publish_is_a_new_version(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        edited = run(
            agents.edit(
                "w1", instance.id, {"settings": {"greeting": "Hello there"}},
                expect_revision=instance.draft_revision,
            )
        )
        second = run(
            agents.publish("w1", instance.id, expect_revision=edited.draft_revision)
        )
        assert second.version == 2
        assert [one.version for one in run(agents.versions("w1", instance.id))] == [2, 1]


class TestRollingBack:
    def test_going_back_publishes_a_new_version(self, world) -> None:
        # A history that can be rewritten is not a history, and "we rolled
        # back on Tuesday" is the fact someone will need.
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        edited = run(
            agents.edit(
                "w1", instance.id, {"settings": {"greeting": "Wrong"}},
                expect_revision=instance.draft_revision,
            )
        )
        run(agents.publish("w1", instance.id, expect_revision=edited.draft_revision))

        current = run(agents.get("w1", instance.id))
        third = run(
            agents.rollback(
                "w1", instance.id, to_version=1, expect_revision=current.draft_revision
            )
        )
        assert third.version == 3
        assert third.rolled_back_from == 1
        assert [one.version for one in run(agents.versions("w1", instance.id))] == [3, 2, 1]

    def test_the_old_settings_come_back(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        edited = run(
            agents.edit(
                "w1", instance.id, {"settings": {"greeting": "Wrong"}},
                expect_revision=instance.draft_revision,
            )
        )
        run(agents.publish("w1", instance.id, expect_revision=edited.draft_revision))
        assert live(world, instance.id).config.settings["greeting"] == "Wrong"

        current = run(agents.get("w1", instance.id))
        run(
            agents.rollback(
                "w1", instance.id, to_version=1, expect_revision=current.draft_revision
            )
        )
        assert live(world, instance.id).config.settings["greeting"] != "Wrong"

    def test_the_draft_follows_the_rollback(self, world) -> None:
        # Otherwise the next publish silently undoes it.
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        edited = run(
            agents.edit(
                "w1", instance.id, {"settings": {"greeting": "Wrong"}},
                expect_revision=instance.draft_revision,
            )
        )
        run(agents.publish("w1", instance.id, expect_revision=edited.draft_revision))
        current = run(agents.get("w1", instance.id))
        run(
            agents.rollback(
                "w1", instance.id, to_version=1, expect_revision=current.draft_revision
            )
        )
        assert "greeting" not in run(agents.get("w1", instance.id)).draft_overrides.settings

    def test_rolling_back_to_a_version_that_is_not_there(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        with pytest.raises(AgentNotFound):
            run(
                agents.rollback(
                    "w1", instance.id, to_version=99,
                    expect_revision=instance.draft_revision,
                )
            )


class TestUpgradingToANewerDefinition:
    def test_a_new_template_version_does_not_arrive_on_its_own(self, world) -> None:
        # The whole reason an instance pins a version. A definition arriving
        # unasked is how an upgrade breaks a customer's agent overnight.
        instance = make_agent(world)
        publish_another_version(
            world, lambda document: document["identity"].update({"name": "Marketing II"})
        )
        assert live(world, instance.id).template_version == 1
        assert live(world, instance.id).definition.identity.name == "Marketing"

    def test_upgrading_moves_the_pin(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        second = publish_another_version(
            world, lambda document: document["identity"].update({"name": "Marketing II"})
        )
        run(agents.upgrade("w1", instance.id, expect_revision=instance.draft_revision))
        assert run(agents.get("w1", instance.id)).draft_template_version == second

    def test_but_changes_nothing_live_until_it_is_published(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        publish_another_version(
            world, lambda document: document["identity"].update({"name": "Marketing II"})
        )
        run(agents.upgrade("w1", instance.id, expect_revision=instance.draft_revision))
        assert live(world, instance.id).template_version == 1
        assert draft(world, instance.id).template_version == 2

    def test_a_preview_says_what_would_be_lost_before_anything_is_written(
        self, world
    ) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        edited = run(
            agents.edit(
                "w1", instance.id, {"settings": {"greeting": "Hello there"}},
                expect_revision=instance.draft_revision,
            )
        )
        publish_another_version(world, lambda document: document["settings"].pop("greeting"))

        preview = run(agents.preview_upgrade("w1", instance.id))
        assert preview.from_version == 1
        assert preview.to_version == 2
        assert preview.loses_nothing is False
        assert [one.path for one in preview.dropped] == ["settings.greeting"]
        # Nothing moved: the workspace can still say no.
        assert run(agents.get("w1", instance.id)).draft_template_version == 1

    def test_a_dropped_setting_is_dropped_with_a_reason(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        edited = run(
            agents.edit(
                "w1", instance.id, {"settings": {"greeting": "Hello there"}},
                expect_revision=instance.draft_revision,
            )
        )
        publish_another_version(world, lambda document: document["settings"].pop("greeting"))
        run(agents.upgrade("w1", instance.id, expect_revision=edited.draft_revision))
        assert "greeting" not in run(agents.get("w1", instance.id)).draft_overrides.settings

    def test_a_renamed_setting_keeps_its_value(self, world) -> None:
        # The reason upgrade.renamedSettings exists: a rename should not cost
        # a workspace the value it configured.
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        edited = run(
            agents.edit(
                "w1", instance.id, {"settings": {"greeting": "Hello there"}},
                expect_revision=instance.draft_revision,
            )
        )

        def rename(document: dict[str, Any]) -> None:
            document["settings"]["openingLine"] = document["settings"].pop("greeting")
            document["upgrade"] = {"renamedSettings": {"greeting": "openingLine"}}

        publish_another_version(world, rename)
        run(agents.upgrade("w1", instance.id, expect_revision=edited.draft_revision))
        stored = run(agents.get("w1", instance.id)).draft_overrides.settings
        assert stored == {"openingLine": "Hello there"}

    def test_a_setting_renamed_twice_still_arrives(self, world) -> None:
        # One rename map per version stepped through, oldest first.
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        edited = run(
            agents.edit(
                "w1", instance.id, {"settings": {"greeting": "Hello there"}},
                expect_revision=instance.draft_revision,
            )
        )

        def rename(old: str, new: str):
            def change(document: dict[str, Any]) -> None:
                document["settings"][new] = document["settings"].pop(old)
                document["upgrade"] = {"renamedSettings": {old: new}}

            return change

        publish_another_version(world, rename("greeting", "openingLine"))
        publish_another_version(world, rename("openingLine", "firstLine"))

        run(agents.upgrade("w1", instance.id, expect_revision=edited.draft_revision))
        assert run(agents.get("w1", instance.id)).draft_overrides.settings == {
            "firstLine": "Hello there"
        }


class TestStatus:
    def test_a_paused_agent_stops_answering(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        run(
            agents.pause(
                "w1", instance.id, expect_revision=instance.draft_revision,
            )
        )
        with pytest.raises(AgentNotLive) as caught:
            live(world, instance.id)
        assert caught.value.status == str(InstanceStatus.PAUSED)

    def test_resuming_puts_it_back_where_it_was(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        paused = run(
            agents.pause(
                "w1", instance.id, expect_revision=instance.draft_revision,
            )
        )
        run(agents.resume("w1", instance.id, expect_revision=paused.draft_revision))
        assert live(world, instance.id)

    def test_resuming_a_paused_draft_does_not_publish_it(self, world) -> None:
        # The status it was paused *from* is what it goes back to.
        agents: InstanceService = world["agents"]
        instance = make_agent(world, publish=False)
        paused = run(
            agents.pause(
                "w1", instance.id, expect_revision=instance.draft_revision,
            )
        )
        resumed = run(agents.resume("w1", instance.id, expect_revision=paused.draft_revision))
        assert resumed.status is InstanceStatus.DRAFT

    def test_a_deleted_agent_is_gone_from_every_path(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        run(agents.delete("w1", instance.id, expect_revision=instance.draft_revision))
        with pytest.raises(AgentNotFound):
            run(agents.get("w1", instance.id))
        with pytest.raises(AgentNotFound):
            live(world, instance.id)
        with pytest.raises(AgentNotFound):
            draft(world, instance.id)

    def test_but_its_versions_are_still_there(self, world) -> None:
        # A deleted agent still has conversations and a transcript someone may
        # need to read. Nothing here hard-deletes.
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        run(agents.delete("w1", instance.id, expect_revision=instance.draft_revision))
        assert run(world["instances"].versions("w1", instance.id))

    def test_the_allowed_origins_apply_at_once(self, world) -> None:
        # Unversioned on purpose: taking a domain off the list is a security
        # control and must not wait for the next publish.
        agents: InstanceService = world["agents"]
        instance = make_agent(world)
        updated = run(
            agents.set_deployment(
                "w1",
                instance.id,
                Deployment(allowed_origins=("https://acme.example",)),
                expect_revision=instance.draft_revision,
            )
        )
        assert updated.deployment.allowed_origins == ("https://acme.example",)
        assert live(world, instance.id).instance.deployment.allowed_origins == (
            "https://acme.example",
        )


class TestTenancy:
    def test_another_workspace_cannot_read_an_agent(self, world) -> None:
        instance = make_agent(world, workspace_id="w1")
        with pytest.raises(AgentNotFound):
            run(world["agents"].get("w2", instance.id))

    def test_or_resolve_it(self, world) -> None:
        instance = make_agent(world, workspace_id="w1")
        with pytest.raises(AgentNotFound):
            draft(world, instance.id, workspace_id="w2")

    def test_or_edit_it(self, world) -> None:
        instance = make_agent(world, workspace_id="w1")
        with pytest.raises(AgentNotFound):
            run(
                world["agents"].edit(
                    "w2", instance.id, {"settings": {"greeting": "Theirs"}},
                    expect_revision=instance.draft_revision,
                )
            )

    def test_or_delete_it(self, world) -> None:
        instance = make_agent(world, workspace_id="w1")
        with pytest.raises(AgentNotFound):
            run(
                world["agents"].delete(
                    "w2", instance.id, expect_revision=instance.draft_revision
                )
            )

    def test_a_workspace_only_lists_its_own(self, world) -> None:
        make_agent(world, workspace_id="w1", label="ours")
        make_agent(world, workspace_id="w2", label="theirs")
        assert [one.label for one in run(world["agents"].list("w1"))] == ["ours"]


class TestResolving:
    def test_the_definition_is_parsed_once_per_version(self, world) -> None:
        # Validating a definition is schema work on every field, and a
        # published version cannot change -- so the cache needs no
        # invalidation, which is the only kind worth having on this path.
        instance = make_agent(world)
        world["cache"].clear()
        live(world, instance.id)
        after_first = world["cache"].misses
        for _ in range(20):
            live(world, instance.id)
        assert world["cache"].misses == after_first
        assert world["cache"].hits >= 20

    def test_the_cache_is_keyed_on_the_version_too(self, world) -> None:
        # A key alone would serve version 2's definition to an instance pinned
        # to version 1, which is the exact failure pinning prevents.
        agents: InstanceService = world["agents"]
        first = make_agent(world, label="stays")
        publish_another_version(
            world, lambda document: document["identity"].update({"name": "Marketing II"})
        )
        second = make_agent(world, label="moves")
        assert live(world, first.id).definition.identity.name == "Marketing"
        assert live(world, second.id).definition.identity.name == "Marketing II"

    def test_a_missing_template_version_is_a_broken_definition(self, world) -> None:
        agents: InstanceService = world["agents"]
        instance = make_agent(world, publish=False)
        run(
            world["instances"].pin_template_version(
                "w1", instance.id, 99, expect_revision=instance.draft_revision
            )
        )
        with pytest.raises(BrokenDefinition):
            draft(world, instance.id)

    def test_an_agent_that_does_not_exist_is_not_found(self, world) -> None:
        with pytest.raises(AgentNotFound):
            live(world, "no-such-agent")

    def test_resolving_gives_the_abilities_the_prompt_needs(self, world) -> None:
        instance = make_agent(world)
        resolved = live(world, instance.id)
        assert {brief.name for brief in resolved.briefs} == {
            "search_knowledge",
            "save_visitor_details",
            "suggest_replies",
        }
