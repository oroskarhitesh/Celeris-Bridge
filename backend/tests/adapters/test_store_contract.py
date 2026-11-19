"""
One suite, both stores.

Every test here runs twice: against the in-memory stores and against Mongo.
That is the point. An in-memory store is what development and most tests run
against, so anywhere it is more forgiving than the real one is a bug that
cannot be caught until production — and "more forgiving" is exactly what a
hand-written fake drifts towards.

The Mongo half skips when no server answers, which keeps the suite runnable on
a laptop without one. ``CB_TEST_MONGO_URL`` points it somewhere else.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from celeris_bridge.adapters.in_memory_stores import (
    InMemoryConversationStore,
    InMemoryInstanceStore,
    InMemoryTemplateStore,
)
from celeris_bridge.adapters.mongo.stores import AlreadyExists, MissingRecord
from celeris_bridge.domain.agent.overrides import AbilityOverride, Overrides
from celeris_bridge.domain.agent.records import (
    AgentInstance,
    AgentInstanceVersion,
    AgentTemplate,
    AgentTemplateVersion,
    Deployment,
    InstanceStatus,
    PausedReason,
    RevisionConflict,
    TemplateStatus,
)
from celeris_bridge.domain.conversation import Channel, Conversation, Speaker, Turn

MONGO_URL = os.environ.get("CB_TEST_MONGO_URL", "mongodb://127.0.0.1:27017")


#: One loop for the whole module, not one per call.
#:
#: ``AsyncMongoClient`` binds to the event loop it is first used on and refuses
#: to be used from another, so ``asyncio.run`` per operation -- a fresh loop
#: each time -- fails on the second call. The same constraint applies in
#: production, which is why ``connect`` says so.
_LOOP = asyncio.new_event_loop()


def run(coroutine: Any) -> Any:
    return _LOOP.run_until_complete(coroutine)


def mongo_is_reachable() -> bool:
    try:
        from pymongo import MongoClient

        MongoClient(MONGO_URL, serverSelectionTimeoutMS=400).admin.command("ping")
        return True
    except Exception:
        return False


HAVE_MONGO = mongo_is_reachable()
needs_mongo = pytest.mark.skipif(
    not HAVE_MONGO, reason=f"no Mongo answering at {MONGO_URL}"
)


@pytest.fixture(params=["memory", pytest.param("mongo", marks=needs_mongo)])
def stores(request: pytest.FixtureRequest):
    """Both store families, behind the same names."""
    if request.param == "memory":
        yield {
            "templates": InMemoryTemplateStore(),
            "instances": InMemoryInstanceStore(),
            "conversations": InMemoryConversationStore(),
        }
        return

    from celeris_bridge.adapters.mongo import (
        MongoConversationStore,
        MongoInstanceStore,
        MongoTemplateStore,
        connect,
        ensure_indexes,
    )

    name = f"celeris_test_{uuid.uuid4().hex[:12]}"
    client, database = connect(MONGO_URL, name)
    run(ensure_indexes(database))
    try:
        yield {
            "templates": MongoTemplateStore(database),
            "instances": MongoInstanceStore(database),
            "conversations": MongoConversationStore(database),
        }
    finally:
        run(client.drop_database(name))
        run(client.close())


def a_template(**changed: Any) -> AgentTemplate:
    base: dict[str, Any] = {"key": "marketing_v1", "draft": {"identity": {"name": "M"}}}
    return AgentTemplate(**{**base, **changed})


def an_instance(**changed: Any) -> AgentInstance:
    base: dict[str, Any] = {
        "id": "",
        "workspace_id": "w1",
        "template_key": "marketing_v1",
        "label": "Main website",
        "draft_template_version": 1,
    }
    return AgentInstance(**{**base, **changed})


class TestTemplates:
    def test_a_created_template_can_be_read_back(self, stores) -> None:
        created = run(stores["templates"].create(a_template()))
        assert created.key == "marketing_v1"
        assert created.created_at is not None
        found = run(stores["templates"].get("marketing_v1"))
        assert found is not None
        assert found.draft == {"identity": {"name": "M"}}

    def test_a_template_that_does_not_exist_reads_as_none(self, stores) -> None:
        assert run(stores["templates"].get("nope")) is None

    def test_a_key_cannot_be_reused(self, stores) -> None:
        # A workspace's agent names its template by key forever, so reusing one
        # would point live agents at a different agent type.
        run(stores["templates"].create(a_template()))
        with pytest.raises(AlreadyExists):
            run(stores["templates"].create(a_template()))

    def test_templates_can_be_listed_by_status(self, stores) -> None:
        run(stores["templates"].create(a_template()))
        run(stores["templates"].create(a_template(key="support_v1")))
        run(stores["templates"].set_status("support_v1", TemplateStatus.ARCHIVED, expect_revision=0))

        active = run(stores["templates"].list(status=TemplateStatus.ACTIVE))
        archived = run(stores["templates"].list(status=TemplateStatus.ARCHIVED))
        assert [t.key for t in active] == ["marketing_v1"]
        assert [t.key for t in archived] == ["support_v1"]
        assert len(run(stores["templates"].list())) == 2

    def test_saving_a_draft_moves_the_revision_on(self, stores) -> None:
        run(stores["templates"].create(a_template()))
        saved = run(
            stores["templates"].save_draft(
                "marketing_v1", {"identity": {"name": "Marketing"}}, expect_revision=0
            )
        )
        assert saved.draft_revision == 1
        assert saved.draft == {"identity": {"name": "Marketing"}}
        assert saved.draft_updated_at is not None

    def test_two_admins_saving_at_once_is_a_conflict_not_a_silent_overwrite(
        self, stores
    ) -> None:
        # Both have the form open on revision 0. The second must be told.
        run(stores["templates"].create(a_template()))
        run(stores["templates"].save_draft("marketing_v1", {"a": 1}, expect_revision=0))
        with pytest.raises(RevisionConflict) as caught:
            run(stores["templates"].save_draft("marketing_v1", {"b": 2}, expect_revision=0))
        assert caught.value.expected == 0
        assert caught.value.found == 1
        # And the first save survived.
        found = run(stores["templates"].get("marketing_v1"))
        assert found is not None and found.draft == {"a": 1}

    def test_changing_something_that_is_not_there_is_a_missing_record(
        self, stores
    ) -> None:
        # A different answer from a conflict: nothing to reload.
        with pytest.raises(MissingRecord):
            run(stores["templates"].save_draft("nope", {}, expect_revision=0))

    def test_publishing_writes_a_version_and_points_the_template_at_it(
        self, stores
    ) -> None:
        run(stores["templates"].create(a_template()))
        template = run(
            stores["templates"].publish(
                AgentTemplateVersion(
                    template_key="marketing_v1",
                    version=1,
                    definition={"identity": {"name": "M"}},
                    change_summary="first",
                ),
                expect_revision=0,
            )
        )
        assert template.latest_version == 1
        assert template.last_published_at is not None
        version = run(stores["templates"].version("marketing_v1", 1))
        assert version is not None
        assert version.change_summary == "first"
        assert version.published_at is not None

    def test_the_same_version_cannot_be_published_twice(self, stores) -> None:
        # Two publishes racing must produce one version, not two documents
        # both claiming to be v1.
        run(stores["templates"].create(a_template()))
        version = AgentTemplateVersion(template_key="marketing_v1", version=1, definition={})
        run(stores["templates"].publish(version, expect_revision=0))
        with pytest.raises(AlreadyExists):
            run(stores["templates"].publish(version, expect_revision=1))

    def test_the_latest_version_is_the_highest_not_the_newest_written(
        self, stores
    ) -> None:
        run(stores["templates"].create(a_template()))
        for number in (1, 2, 3):
            run(
                stores["templates"].publish(
                    AgentTemplateVersion(
                        template_key="marketing_v1", version=number, definition={}
                    ),
                    expect_revision=number - 1,
                )
            )
        latest = run(stores["templates"].latest("marketing_v1"))
        assert latest is not None and latest.version == 3

    def test_versions_come_back_newest_first(self, stores) -> None:
        run(stores["templates"].create(a_template()))
        for number in (1, 2, 3):
            run(
                stores["templates"].publish(
                    AgentTemplateVersion(
                        template_key="marketing_v1", version=number, definition={}
                    ),
                    expect_revision=number - 1,
                )
            )
        assert [v.version for v in run(stores["templates"].versions("marketing_v1"))] == [3, 2, 1]

    def test_a_published_version_never_changes(self, stores) -> None:
        # There is no method to change one, which is the point. This asserts
        # the surface rather than the behaviour.
        assert not hasattr(stores["templates"], "update_version")
        assert not hasattr(stores["templates"], "delete_version")

    def test_one_templates_versions_are_not_anothers(self, stores) -> None:
        run(stores["templates"].create(a_template()))
        run(stores["templates"].create(a_template(key="support_v1")))
        run(
            stores["templates"].publish(
                AgentTemplateVersion(template_key="marketing_v1", version=1, definition={}),
                expect_revision=0,
            )
        )
        assert run(stores["templates"].versions("support_v1")) == []
        assert run(stores["templates"].latest("support_v1")) is None


class TestInstances:
    def test_a_created_instance_gets_an_id(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        assert created.id
        assert created.created_at is not None
        found = run(stores["instances"].get("w1", created.id))
        assert found is not None and found.label == "Main website"

    def test_another_workspace_cannot_read_it(self, stores) -> None:
        # An id alone is guessable. The workspace is part of the lookup, not a
        # check somebody remembers to do afterwards.
        created = run(stores["instances"].create(an_instance()))
        assert run(stores["instances"].get("w2", created.id)) is None

    def test_another_workspace_cannot_change_it(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        with pytest.raises(MissingRecord):
            run(
                stores["instances"].save_draft(
                    "w2", created.id, overrides=Overrides(), expect_revision=0
                )
            )

    def test_overrides_survive_a_save(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        overrides = Overrides(
            settings={"tone": "formal"},
            abilities={"search_knowledge": AbilityOverride(enabled=False)},
        )
        saved = run(
            stores["instances"].save_draft(
                "w1", created.id, overrides=overrides, expect_revision=0
            )
        )
        assert saved.draft_overrides.settings == {"tone": "formal"}
        assert saved.draft_overrides.abilities["search_knowledge"].enabled is False
        assert saved.draft_revision == 1

    def test_a_stale_save_is_refused(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        run(
            stores["instances"].save_draft(
                "w1", created.id, overrides=Overrides(settings={"a": 1}), expect_revision=0
            )
        )
        with pytest.raises(RevisionConflict):
            run(
                stores["instances"].save_draft(
                    "w1", created.id, overrides=Overrides(settings={"b": 2}), expect_revision=0
                )
            )

    def test_a_label_can_be_changed_with_the_draft(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        saved = run(
            stores["instances"].save_draft(
                "w1", created.id, overrides=Overrides(), expect_revision=0, label="Pricing page"
            )
        )
        assert saved.label == "Pricing page"

    def test_a_returned_record_is_not_a_live_view_of_the_store(self, stores) -> None:
        # A caller holding a record must not see later changes — no real store
        # behaves that way, and a test would pass on an aliasing bug.
        created = run(stores["instances"].create(an_instance()))
        before = run(stores["instances"].get("w1", created.id))
        run(
            stores["instances"].save_draft(
                "w1", created.id, overrides=Overrides(settings={"tone": "formal"}), expect_revision=0
            )
        )
        assert before is not None and before.draft_overrides.settings == {}

    def test_instances_are_listed_per_workspace(self, stores) -> None:
        run(stores["instances"].create(an_instance()))
        run(stores["instances"].create(an_instance(workspace_id="w2", label="Theirs")))
        assert [i.label for i in run(stores["instances"].list("w1"))] == ["Main website"]

    def test_a_deleted_instance_drops_out_of_the_list_but_still_exists(
        self, stores
    ) -> None:
        # It still has conversations and leads somebody may need to read.
        created = run(stores["instances"].create(an_instance()))
        run(stores["instances"].soft_delete("w1", created.id, expect_revision=0))
        assert run(stores["instances"].list("w1")) == []
        found = run(stores["instances"].get("w1", created.id))
        assert found is not None and found.deleted_at is not None

    def test_the_count_for_a_template_ignores_deleted_ones(self, stores) -> None:
        # It is what the definition's maxAgentsPerWorkspace is checked against,
        # so counting deleted agents would lock a workspace out of its allowance.
        first = run(stores["instances"].create(an_instance()))
        run(stores["instances"].create(an_instance(label="Second")))
        run(stores["instances"].soft_delete("w1", first.id, expect_revision=0))
        assert run(stores["instances"].count_for_template("w1", "marketing_v1")) == 1
        assert run(stores["instances"].count_for_template("w1", "support_v1")) == 0

    def test_instances_can_be_filtered_by_status(self, stores) -> None:
        live = run(stores["instances"].create(an_instance()))
        run(stores["instances"].create(an_instance(label="Draft one")))
        run(stores["instances"].set_status("w1", live.id, InstanceStatus.LIVE, expect_revision=0))
        found = run(stores["instances"].list("w1", status=InstanceStatus.LIVE))
        assert [i.label for i in found] == ["Main website"]

    def test_un_pausing_restores_what_it_was(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        live = run(
            stores["instances"].set_status("w1", created.id, InstanceStatus.LIVE, expect_revision=0)
        )
        paused = live.paused(PausedReason.ACCESS_REVOKED)
        stored = run(
            stores["instances"].set_status(
                "w1", created.id, InstanceStatus.PAUSED, expect_revision=live.draft_revision
            )
        )
        assert stored.status is InstanceStatus.PAUSED
        assert paused.resumed().status is InstanceStatus.LIVE

    def test_leaving_paused_clears_why(self, stores) -> None:
        # Otherwise a later un-pause restores a state the agent is not in.
        created = run(stores["instances"].create(an_instance()))
        run(stores["instances"].set_status("w1", created.id, InstanceStatus.PAUSED, expect_revision=0))
        back = run(
            stores["instances"].set_status("w1", created.id, InstanceStatus.LIVE, expect_revision=1)
        )
        assert back.paused_reason is None
        assert back.paused_from is None

    def test_deployment_is_saved_and_applies_without_a_publish(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        saved = run(
            stores["instances"].save_deployment(
                "w1",
                created.id,
                Deployment(allowed_origins=("https://acme.test",), launcher_text="Chat"),
                expect_revision=0,
            )
        )
        assert saved.deployment.allowed_origins == ("https://acme.test",)
        assert saved.deployment.launcher_text == "Chat"
        assert saved.published_version is None

    def test_pinning_another_template_version_is_a_deliberate_move(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        moved = run(
            stores["instances"].pin_template_version("w1", created.id, 3, expect_revision=0)
        )
        assert moved.draft_template_version == 3

    def test_publishing_snapshots_the_overrides(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        saved = run(
            stores["instances"].save_draft(
                "w1",
                created.id,
                overrides=Overrides(settings={"tone": "formal"}),
                expect_revision=0,
            )
        )
        instance = run(
            stores["instances"].publish(
                AgentInstanceVersion(
                    id="",
                    workspace_id="w1",
                    instance_id=created.id,
                    version=1,
                    template_key="marketing_v1",
                    template_version=1,
                    overrides=saved.draft_overrides,
                    change_summary="formal tone",
                ),
                expect_revision=saved.draft_revision,
            )
        )
        assert instance.published_version == 1
        published = run(stores["instances"].published("w1", created.id))
        assert published is not None
        assert published.overrides.settings == {"tone": "formal"}
        assert published.template_version == 1

    def test_an_unpublished_agent_has_no_published_snapshot(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        assert run(stores["instances"].published("w1", created.id)) is None

    def test_several_published_snapshots_come_back_at_once(self, stores) -> None:
        # What a list screen needs: which agents have unpublished changes,
        # without a read per row. Ids with nothing published are absent
        # rather than present and None.
        published = run(stores["instances"].create(an_instance(label="Published")))
        draft = run(stores["instances"].create(an_instance(label="Draft")))
        run(
            stores["instances"].publish(
                AgentInstanceVersion(
                    id="",
                    workspace_id="w1",
                    instance_id=published.id,
                    version=1,
                    template_key="marketing_v1",
                    template_version=2,
                    overrides=Overrides(settings={"tone": "warm"}),
                ),
                expect_revision=0,
            )
        )

        found = run(
            stores["instances"].published_many("w1", [published.id, draft.id])
        )
        assert set(found) == {published.id}
        assert found[published.id].template_version == 2
        assert found[published.id].overrides.settings == {"tone": "warm"}

    def test_asking_for_none_asks_the_database_nothing(self, stores) -> None:
        assert run(stores["instances"].published_many("w1", [])) == {}

    def test_another_workspace_gets_no_snapshots(self, stores) -> None:
        mine = run(stores["instances"].create(an_instance()))
        run(
            stores["instances"].publish(
                AgentInstanceVersion(
                    id="",
                    workspace_id="w1",
                    instance_id=mine.id,
                    version=1,
                    template_key="marketing_v1",
                    template_version=1,
                ),
                expect_revision=0,
            )
        )
        # The id is known -- ids travel in URLs -- so the workspace has to be
        # what decides, here as in every other method.
        assert run(stores["instances"].published_many("w2", [mine.id])) == {}

    def test_the_same_version_cannot_be_published_twice(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        version = AgentInstanceVersion(
            id="",
            workspace_id="w1",
            instance_id=created.id,
            version=1,
            template_key="marketing_v1",
            template_version=1,
        )
        run(stores["instances"].publish(version, expect_revision=0))
        with pytest.raises(AlreadyExists):
            run(stores["instances"].publish(version, expect_revision=1))

    def test_versions_come_back_newest_first(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        for number in (1, 2, 3):
            run(
                stores["instances"].publish(
                    AgentInstanceVersion(
                        id="",
                        workspace_id="w1",
                        instance_id=created.id,
                        version=number,
                        template_key="marketing_v1",
                        template_version=1,
                    ),
                    expect_revision=number - 1,
                )
            )
        found = run(stores["instances"].versions("w1", created.id))
        assert [v.version for v in found] == [3, 2, 1]

    def test_another_workspace_cannot_read_a_version(self, stores) -> None:
        created = run(stores["instances"].create(an_instance()))
        run(
            stores["instances"].publish(
                AgentInstanceVersion(
                    id="",
                    workspace_id="w1",
                    instance_id=created.id,
                    version=1,
                    template_key="marketing_v1",
                    template_version=1,
                ),
                expect_revision=0,
            )
        )
        assert run(stores["instances"].version("w2", created.id, 1)) is None
        assert run(stores["instances"].versions("w2", created.id)) == []

    def test_a_rollback_is_a_new_version_not_a_deletion(self, stores) -> None:
        # The history stays append-only, so the rollback itself is auditable.
        created = run(stores["instances"].create(an_instance()))
        for number in (1, 2):
            run(
                stores["instances"].publish(
                    AgentInstanceVersion(
                        id="",
                        workspace_id="w1",
                        instance_id=created.id,
                        version=number,
                        template_key="marketing_v1",
                        template_version=1,
                        overrides=Overrides(settings={"tone": "formal" if number == 2 else "friendly"}),
                    ),
                    expect_revision=number - 1,
                )
            )
        run(
            stores["instances"].publish(
                AgentInstanceVersion(
                    id="",
                    workspace_id="w1",
                    instance_id=created.id,
                    version=3,
                    template_key="marketing_v1",
                    template_version=1,
                    overrides=Overrides(settings={"tone": "friendly"}),
                    rolled_back_from=1,
                ),
                expect_revision=2,
            )
        )
        found = run(stores["instances"].versions("w1", created.id))
        assert [v.version for v in found] == [3, 2, 1]
        assert found[0].rolled_back_from == 1


class TestConversations:
    def a_conversation(self, **changed: Any) -> Conversation:
        base: dict[str, Any] = {
            "id": "",
            "workspace_id": "w1",
            "instance_id": "i1",
            "channel": Channel.WEB_CHAT,
        }
        return Conversation(**{**base, **changed})

    def test_a_started_conversation_can_be_read_back(self, stores) -> None:
        started = run(stores["conversations"].start(self.a_conversation()))
        assert started.id
        assert started.started_at is not None
        found = run(stores["conversations"].get("w1", started.id))
        assert found is not None and found.turns == ()

    def test_another_workspace_cannot_read_it(self, stores) -> None:
        started = run(stores["conversations"].start(self.a_conversation()))
        assert run(stores["conversations"].get("w2", started.id)) is None

    def test_turns_are_appended_in_order(self, stores) -> None:
        started = run(stores["conversations"].start(self.a_conversation()))
        run(
            stores["conversations"].append(
                "w1",
                started.id,
                Turn(speaker=Speaker.VISITOR, text="how much?"),
                Turn(speaker=Speaker.AGENT, text="29 per seat.", ending="answered"),
            )
        )
        after = run(
            stores["conversations"].append(
                "w1", started.id, Turn(speaker=Speaker.VISITOR, text="and the Pro plan?")
            )
        )
        assert [turn.text for turn in after.turns] == [
            "how much?",
            "29 per seat.",
            "and the Pro plan?",
        ]

    def test_a_turn_is_stamped_with_when_it_was_stored(self, stores) -> None:
        started = run(stores["conversations"].start(self.a_conversation()))
        after = run(
            stores["conversations"].append(
                "w1", started.id, Turn(speaker=Speaker.VISITOR, text="hello")
            )
        )
        assert after.turns[0].at is not None
        assert after.turns[0].at.tzinfo is not None

    def test_a_time_the_caller_set_is_kept(self, stores) -> None:
        when = datetime(2026, 11, 10, 14, 30, tzinfo=timezone.utc)
        started = run(stores["conversations"].start(self.a_conversation()))
        after = run(
            stores["conversations"].append(
                "w1", started.id, Turn(speaker=Speaker.VISITOR, text="hello", at=when)
            )
        )
        assert after.turns[0].at == when

    def test_what_an_ability_did_is_kept_with_the_turn(self, stores) -> None:
        # The most useful thing to have when an agent said something odd, and
        # the easiest to forget to store.
        started = run(stores["conversations"].start(self.a_conversation()))
        after = run(
            stores["conversations"].append(
                "w1",
                started.id,
                Turn(
                    speaker=Speaker.AGENT,
                    text="29 per seat.",
                    calls=({"key": "search_knowledge", "ok": True},),
                    sources=({"name": "pricing.pdf"},),
                    ending="answered",
                    model="claude-opus-5-5",
                    input_tokens=120,
                    output_tokens=34,
                ),
            )
        )
        turn = after.turns[0]
        assert turn.calls[0]["key"] == "search_knowledge"
        assert turn.sources[0]["name"] == "pricing.pdf"
        assert turn.model == "claude-opus-5-5"
        assert after.tokens == (120, 34)

    def test_appending_nothing_changes_nothing(self, stores) -> None:
        started = run(stores["conversations"].start(self.a_conversation()))
        assert run(stores["conversations"].append("w1", started.id)).turns == ()

    def test_appending_to_a_conversation_that_is_not_there_is_refused(
        self, stores
    ) -> None:
        with pytest.raises(MissingRecord):
            run(
                stores["conversations"].append(
                    "w1", "000000000000000000000000", Turn(speaker=Speaker.VISITOR, text="hi")
                )
            )

    def test_another_workspace_cannot_append(self, stores) -> None:
        started = run(stores["conversations"].start(self.a_conversation()))
        with pytest.raises(MissingRecord):
            run(
                stores["conversations"].append(
                    "w2", started.id, Turn(speaker=Speaker.VISITOR, text="hi")
                )
            )

    def test_recent_conversations_are_newest_first(self, stores) -> None:
        first = run(stores["conversations"].start(self.a_conversation()))
        second = run(stores["conversations"].start(self.a_conversation()))
        run(
            stores["conversations"].append(
                "w1", first.id, Turn(speaker=Speaker.VISITOR, text="later")
            )
        )
        found = run(stores["conversations"].recent("w1"))
        assert [c.id for c in found][0] == first.id
        assert second.id in [c.id for c in found]

    def test_recent_can_be_filtered(self, stores) -> None:
        run(stores["conversations"].start(self.a_conversation()))
        run(stores["conversations"].start(self.a_conversation(instance_id="i2")))
        run(stores["conversations"].start(self.a_conversation(channel=Channel.EMAIL)))
        assert len(run(stores["conversations"].recent("w1", instance_id="i2"))) == 1
        assert len(run(stores["conversations"].recent("w1", channel=Channel.EMAIL))) == 1
        assert len(run(stores["conversations"].recent("w1"))) == 3

    def test_recent_is_per_workspace(self, stores) -> None:
        run(stores["conversations"].start(self.a_conversation()))
        run(stores["conversations"].start(self.a_conversation(workspace_id="w2")))
        assert len(run(stores["conversations"].recent("w1"))) == 1

    def test_the_limit_is_respected(self, stores) -> None:
        for _ in range(5):
            run(stores["conversations"].start(self.a_conversation()))
        assert len(run(stores["conversations"].recent("w1", limit=2))) == 2


@pytest.mark.skipif(HAVE_MONGO, reason="Mongo is available, so it was tested")
def test_the_mongo_half_of_this_suite_was_skipped() -> None:
    """A visible marker that half of these tests did not run.

    Without it, a suite that silently skipped every database test reads as a
    green run, and "it passed on my machine" means nothing.
    """
    pytest.skip(f"No Mongo at {MONGO_URL}: the Mongo half of the contract is unverified")
