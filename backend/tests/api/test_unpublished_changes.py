"""
``hasUnpublishedChanges``, through the API.

One field, its own file, because an admin screen hangs a warning on it: *what
you are looking at is not what visitors are getting*. A warning that is
sometimes wrong is a warning people learn to click past, so the sequence that
matters is the boring one -- publish, edit, undo the edit -- and the field has
to be right at every step of it rather than only at the ends.

The rule itself lives in ``AgentInstance.differs_from`` and is tested there;
this is about what the HTTP layer reports, including that one agent's answer
is the same whether it is read on its own or in a list.
"""

from __future__ import annotations

from tests.api.conftest import auth, make_agent, publish_agent


def read(client, agent: dict) -> dict:
    answer = client.get(f"/v1/agents/{agent['id']}", headers=auth("ws"))
    assert answer.status_code == 200, answer.text
    return answer.json()


def edit(client, agent: dict, **settings) -> dict:
    answer = client.patch(
        f"/v1/agents/{agent['id']}",
        json={"settings": settings, "expectRevision": agent["draftRevision"]},
        headers=auth("ws"),
    )
    assert answer.status_code == 200, answer.text
    return answer.json()


class TestWhatTheApiReports:
    def test_a_draft_has_unpublished_changes_because_nobody_is_served(
        self, client
    ) -> None:
        agent = make_agent(client)
        assert agent["publishedVersion"] is None
        assert agent["hasUnpublishedChanges"] is True

    def test_publishing_clears_it_immediately(self, client) -> None:
        # The case the old rule got wrong. Publishing bumps the draft
        # revision, so anything counting revisions says "edited" about the
        # publish itself -- on the very response that announced it.
        published = publish_agent(client, make_agent(client))
        assert published["publishedVersion"] == 1
        assert published["draftRevision"] > 0
        assert published["hasUnpublishedChanges"] is False
        # And on the next read, not only in the publish response.
        assert read(client, published)["hasUnpublishedChanges"] is False

    def test_an_edit_sets_it(self, client) -> None:
        agent = publish_agent(client, make_agent(client))
        edited = edit(client, agent, agentName="Noor")
        assert edited["hasUnpublishedChanges"] is True
        assert read(client, agent)["hasUnpublishedChanges"] is True

    def test_undoing_an_edit_clears_it_again(self, client) -> None:
        # Edit, then put it back. Two more revisions have gone by and the
        # agent is byte-for-byte what visitors have, so the honest answer is
        # that there is nothing to publish -- and the API agrees with itself:
        # publishing here is refused as NOTHING_TO_PUBLISH.
        agent = publish_agent(client, make_agent(client, settings={"agentName": "Ava"}))
        edited = edit(client, agent, agentName="Noor")
        restored = edit(client, edited, agentName="Ava")

        assert restored["hasUnpublishedChanges"] is False

        refused = client.post(
            f"/v1/agents/{agent['id']}/publish",
            json={"expectRevision": restored["draftRevision"]},
            headers=auth("ws"),
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "NOTHING_TO_PUBLISH"

    def test_publishing_the_edit_clears_it(self, client) -> None:
        agent = publish_agent(client, make_agent(client))
        edited = edit(client, agent, agentName="Noor")
        again = publish_agent(client, edited)

        assert again["publishedVersion"] == 2
        assert again["hasUnpublishedChanges"] is False

    def test_a_rollback_leaves_nothing_unpublished(self, client) -> None:
        # A rollback publishes a new version carrying the old overrides, so
        # the draft and the snapshot agree when it is done.
        agent = publish_agent(client, make_agent(client, settings={"agentName": "Ava"}))
        edited = edit(client, agent, agentName="Noor")
        second = publish_agent(client, edited)

        rolled = client.post(
            f"/v1/agents/{agent['id']}/rollback",
            json={"toVersion": 1, "expectRevision": second["draftRevision"]},
            headers=auth("ws"),
        )
        assert rolled.status_code == 201, rolled.text
        assert rolled.json()["agent"]["hasUnpublishedChanges"] is False

    def test_pausing_does_not_change_the_answer(self, client) -> None:
        # Pausing is operational, not a change to the draft. An agent that is
        # paused with nothing to publish still has nothing to publish.
        agent = publish_agent(client, make_agent(client))
        paused = client.post(
            f"/v1/agents/{agent['id']}/pause",
            json={"expectRevision": agent["draftRevision"]},
            headers=auth("ws"),
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["hasUnpublishedChanges"] is False

    def test_a_deployment_change_does_not_change_the_answer(self, client) -> None:
        # Where the widget may appear is deliberately not versioned, so it
        # cannot be something waiting to be published.
        agent = publish_agent(client, make_agent(client))
        answer = client.put(
            f"/v1/agents/{agent['id']}/deployment",
            json={
                "allowedOrigins": ["https://acme.test"],
                "expectRevision": agent["draftRevision"],
            },
            headers=auth("ws"),
        )
        assert answer.status_code == 200, answer.text
        assert answer.json()["hasUnpublishedChanges"] is False


class TestEveryScreenAgrees:
    def test_the_list_says_what_the_detail_says(self, client) -> None:
        untouched = publish_agent(client, make_agent(client, label="Untouched"))
        edited = publish_agent(client, make_agent(client, label="Edited"))
        edit(client, edited, agentName="Noor")
        draft = make_agent(client, label="Never published")

        listed = client.get("/v1/agents", headers=auth("ws")).json()
        by_id = {one["id"]: one["hasUnpublishedChanges"] for one in listed}

        assert by_id[untouched["id"]] is False
        assert by_id[edited["id"]] is True
        assert by_id[draft["id"]] is True
        for one in listed:
            assert one["hasUnpublishedChanges"] == read(client, one)["hasUnpublishedChanges"]

    def test_the_settings_form_says_it_too(self, client) -> None:
        # The form is where the warning is actually drawn, and it carries its
        # own copy of the agent.
        agent = publish_agent(client, make_agent(client))
        form = client.get(f"/v1/agents/{agent['id']}/settings", headers=auth("ws"))
        assert form.status_code == 200, form.text
        assert form.json()["agent"]["hasUnpublishedChanges"] is False

        edit(client, agent, agentName="Noor")
        form = client.get(f"/v1/agents/{agent['id']}/settings", headers=auth("ws"))
        assert form.json()["agent"]["hasUnpublishedChanges"] is True


class TestTheCostOfAnswering:
    def test_a_list_reads_the_snapshots_once_not_once_per_agent(self, client) -> None:
        # The reason the store has ``published_many``. A read per row is the
        # kind of slowness that only shows up on the account with twenty
        # agents, which is the account worth keeping.
        application = client.app.state.application
        store = application.stores.instances
        counted = {"single": 0, "bulk": 0}

        original_single = store.published
        original_bulk = store.published_many

        async def published(workspace_id, instance_id):
            counted["single"] += 1
            return await original_single(workspace_id, instance_id)

        async def published_many(workspace_id, instance_ids):
            counted["bulk"] += 1
            return await original_bulk(workspace_id, instance_ids)

        for number in range(3):
            publish_agent(client, make_agent(client, label=f"Agent {number}"))

        store.published = published
        store.published_many = published_many
        try:
            assert client.get("/v1/agents", headers=auth("ws")).status_code == 200
        finally:
            store.published = original_single
            store.published_many = original_bulk

        assert counted == {"single": 0, "bulk": 1}
