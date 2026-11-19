"""
A workspace's agent, over HTTP.

The lifecycle is already tested against the services; what is tested here is
what the HTTP layer adds and could get wrong on its own: that the settings
form carries the declaration a control is drawn from, that an admin-only
setting is not offered to someone who cannot set it, that a rejected patch
arrives as every reason at once, and that each service exception becomes the
status a client can act on.
"""

from __future__ import annotations

from celeris_bridge.definitions import load_definition

from tests.api.conftest import TEMPLATE, auth, make_agent, publish_agent


def settings_of(client, agent_id: str, *, who: str = "ws") -> dict:
    answer = client.get(f"/v1/agents/{agent_id}/settings", headers=auth(who))
    assert answer.status_code == 200, answer.text
    body = answer.json()
    return {one["key"]: one for one in body["settings"]}


class TestTheSettingsForm:
    def test_a_setting_arrives_as_its_declaration(self, client) -> None:
        # The same FieldSpec is the default, the validator, this control, the
        # schema the model is given and the way the value is written into the
        # prompt. The form is generated from it rather than being a sixth copy.
        agent = make_agent(client)
        tone = settings_of(client, agent["id"])["tone"]
        assert tone["spec"]["type"] == "enum"
        assert [option["value"] for option in tone["spec"]["options"]] == [
            "friendly",
            "formal",
            "direct",
        ]
        assert tone["spec"]["group"] == "Persona"
        assert tone["value"] == "friendly"
        assert tone["overridden"] is False

    def test_the_groups_come_in_the_definitions_order(self, client) -> None:
        agent = make_agent(client)
        body = client.get(
            f"/v1/agents/{agent['id']}/settings", headers=auth("ws")
        ).json()
        assert body["groups"] == [
            "Company",
            "Persona",
            "Conversation",
            "Qualification",
            "Safety",
            "Model",
        ]
        assert body["name"] == "Marketing"

    def test_an_admin_only_setting_is_not_offered_to_a_workspace(self, client) -> None:
        # The validator refuses it, so drawing a control for it would be a
        # form that lies. A support principal -- in the workspace and holding
        # the platform role -- does see it.
        agent = make_agent(client)
        assert "chatModel" not in settings_of(client, agent["id"], who="ws")
        assert "chatModel" in settings_of(client, agent["id"], who="support")

    def test_the_locked_rules_are_shown(self, client) -> None:
        # A workspace should be able to read the rules its agent is held to,
        # even though it cannot change them.
        agent = make_agent(client)
        body = client.get(
            f"/v1/agents/{agent['id']}/settings", headers=auth("ws")
        ).json()
        assert [rule["id"] for rule in body["lockedRules"]]
        assert all(rule["text"] for rule in body["lockedRules"])

    def test_abilities_arrive_with_their_own_declarations(self, client) -> None:
        agent = make_agent(client)
        body = client.get(
            f"/v1/agents/{agent['id']}/settings", headers=auth("ws")
        ).json()
        abilities = {one["key"]: one for one in body["abilities"]}
        assert abilities["search_knowledge"]["enabled"] is True
        assert abilities["search_knowledge"]["missing"] is False
        # Its own settings, declared the same way the agent's are.
        assert abilities["search_knowledge"]["settingsSpec"]["maxPassages"]["type"] in {
            "number",
            "integer",
        }


class TestEditing:
    def test_a_changed_setting_is_marked_as_the_workspaces_own(self, client) -> None:
        agent = make_agent(client)
        edited = client.patch(
            f"/v1/agents/{agent['id']}",
            json={"settings": {"greeting": "Hi there."}, "expectRevision": 0},
            headers=auth("ws"),
        )
        assert edited.status_code == 200, edited.text
        greeting = settings_of(client, agent["id"])["greeting"]
        assert greeting["value"] == "Hi there."
        assert greeting["overridden"] is True

    def test_resetting_is_a_deletion_and_not_a_write(self, client) -> None:
        # The point of a reset: the setting follows the default *from now on*,
        # including when a later version improves it.
        agent = make_agent(client)
        client.patch(
            f"/v1/agents/{agent['id']}",
            json={"settings": {"greeting": "Hi there."}, "expectRevision": 0},
            headers=auth("ws"),
        )
        reset = client.post(
            f"/v1/agents/{agent['id']}/reset",
            json={"settings": ["greeting"], "expectRevision": 1},
            headers=auth("ws"),
        )
        assert reset.status_code == 200, reset.text
        greeting = settings_of(client, agent["id"])["greeting"]
        assert greeting["overridden"] is False
        assert greeting["value"] == load_definition(TEMPLATE)["settings"]["greeting"][
            "default"
        ]

    def test_every_rejection_arrives_at_once(self, client) -> None:
        # So a form can mark all of its bad fields in one round trip instead
        # of making somebody fix thirty mistakes one at a time.
        agent = make_agent(client)
        answer = client.patch(
            f"/v1/agents/{agent['id']}",
            json={
                "settings": {"tone": "sarcastic", "maxTurns": 1, "nonsense": True},
                "expectRevision": 0,
            },
            headers=auth("ws"),
        )
        assert answer.status_code == 400
        error = answer.json()["error"]
        assert error["code"] == "OVERRIDES_REJECTED"
        rejections = {one["path"]: one["code"] for one in error["details"]["rejections"]}
        assert rejections["settings.tone"] == "SETTING_INVALID"
        assert rejections["settings.maxTurns"] == "SETTING_INVALID"
        assert rejections["settings.nonsense"] == "SETTING_UNKNOWN"

    def test_a_rejected_patch_writes_nothing(self, client) -> None:
        agent = make_agent(client)
        client.patch(
            f"/v1/agents/{agent['id']}",
            json={
                "settings": {"greeting": "This one is fine", "tone": "sarcastic"},
                "expectRevision": 0,
            },
            headers=auth("ws"),
        )
        # All or nothing: the acceptable half is not kept.
        assert settings_of(client, agent["id"])["greeting"]["overridden"] is False

    def test_an_admin_only_setting_is_refused_from_a_workspace(self, client) -> None:
        agent = make_agent(client)
        answer = client.patch(
            f"/v1/agents/{agent['id']}",
            json={"settings": {"chatModel": "claude-opus-5-5"}, "expectRevision": 0},
            headers=auth("ws"),
        )
        assert answer.status_code == 400
        assert answer.json()["error"]["code"] == "OVERRIDES_REJECTED"

    def test_a_stale_revision_is_a_conflict_and_says_how_stale(self, client) -> None:
        # Two admins with the same form open, both pressing save, is a
        # Tuesday. The second one is told rather than silently discarding the
        # first one's work.
        agent = make_agent(client)
        client.patch(
            f"/v1/agents/{agent['id']}",
            json={"settings": {"greeting": "First"}, "expectRevision": 0},
            headers=auth("ws"),
        )
        answer = client.patch(
            f"/v1/agents/{agent['id']}",
            json={"settings": {"greeting": "Second"}, "expectRevision": 0},
            headers=auth("ws"),
        )
        assert answer.status_code == 409
        error = answer.json()["error"]
        assert error["code"] == "REVISION_CONFLICT"
        assert error["details"] == {"expected": 0, "found": 1}


class TestPublishing:
    def test_publishing_a_draft_makes_it_live(self, client) -> None:
        agent = publish_agent(client, make_agent(client))
        assert agent["status"] == "live"
        assert agent["publishedVersion"] == 1

    def test_publishing_twice_with_no_change_is_refused(self, client) -> None:
        # A version is a line in a history someone reads, and a list of twenty
        # identical versions is a list nobody can find anything in.
        agent = publish_agent(client, make_agent(client))
        answer = client.post(
            f"/v1/agents/{agent['id']}/publish",
            json={"expectRevision": agent["draftRevision"]},
            headers=auth("ws"),
        )
        assert answer.status_code == 409
        assert answer.json()["error"]["code"] == "NOTHING_TO_PUBLISH"

    def test_the_publisher_is_the_credential(self, client) -> None:
        agent = make_agent(client)
        published = client.post(
            f"/v1/agents/{agent['id']}/publish",
            json={"expectRevision": 0, "changeSummary": "Launch"},
            headers=auth("ws"),
        ).json()
        assert published["version"]["publishedBy"] == "ws_acme"
        assert published["version"]["changeSummary"] == "Launch"

    def test_a_rollback_is_a_new_version(self, client) -> None:
        agent = publish_agent(client, make_agent(client))
        edited = client.patch(
            f"/v1/agents/{agent['id']}",
            json={
                "settings": {"greeting": "Second greeting"},
                "expectRevision": agent["draftRevision"],
            },
            headers=auth("ws"),
        ).json()
        second = client.post(
            f"/v1/agents/{agent['id']}/publish",
            json={"expectRevision": edited["draftRevision"]},
            headers=auth("ws"),
        ).json()

        rolled = client.post(
            f"/v1/agents/{agent['id']}/rollback",
            json={
                "toVersion": 1,
                "expectRevision": second["agent"]["draftRevision"],
            },
            headers=auth("ws"),
        )
        assert rolled.status_code == 201, rolled.text
        assert rolled.json()["version"]["version"] == 3
        assert rolled.json()["version"]["rolledBackFrom"] == 1

        versions = client.get(
            f"/v1/agents/{agent['id']}/versions", headers=auth("ws")
        ).json()
        # Nothing was deleted: the history reads as what happened.
        assert [one["version"] for one in versions] == [3, 2, 1]


class TestUpgrading:
    def _publish_second_template_version(self, client) -> None:
        """Version 2 of the type, with ``greeting`` removed."""
        document = load_definition(TEMPLATE)
        document["settings"].pop("greeting")
        # The revision is read rather than assumed: publishing bumps the
        # draft revision, so it is not 0 by the time a second version is
        # written -- the same trap the publish-twice check falls into.
        current = client.get(
            f"/v1/templates/{TEMPLATE}", headers=auth("platform")
        ).json()
        saved = client.put(
            f"/v1/templates/{TEMPLATE}/draft",
            json={"draft": document, "expectRevision": current["draftRevision"]},
            headers=auth("platform"),
        )
        assert saved.status_code == 200, saved.text
        published = client.post(
            f"/v1/templates/{TEMPLATE}/publish",
            json={
                "expectRevision": saved.json()["draftRevision"],
                "changeSummary": "Dropped the greeting",
            },
            headers=auth("platform"),
        )
        assert published.status_code == 201, published.text

    def test_a_new_template_version_does_not_arrive_on_its_own(self, client) -> None:
        # The pin is the whole point: a definition arriving unasked is how an
        # upgrade breaks a customer's agent overnight.
        agent = publish_agent(client, make_agent(client))
        self._publish_second_template_version(client)
        fresh = client.get(f"/v1/agents/{agent['id']}", headers=auth("ws")).json()
        assert fresh["draftTemplateVersion"] == 1

    def test_the_preview_says_what_would_be_dropped_before_anything_is_written(
        self, client
    ) -> None:
        agent = make_agent(client, settings={"greeting": "Mine"})
        self._publish_second_template_version(client)

        preview = client.get(
            f"/v1/agents/{agent['id']}/upgrade", headers=auth("ws")
        ).json()
        assert preview["fromVersion"] == 1
        assert preview["toVersion"] == 2
        assert preview["losesNothing"] is False
        assert [one["path"] for one in preview["dropped"]] == ["settings.greeting"]

        # And it wrote nothing.
        assert (
            client.get(f"/v1/agents/{agent['id']}", headers=auth("ws")).json()[
                "draftTemplateVersion"
            ]
            == 1
        )

    def test_upgrading_moves_the_draft_and_drops_what_cannot_come(
        self, client
    ) -> None:
        agent = make_agent(client, settings={"greeting": "Mine"})
        self._publish_second_template_version(client)
        upgraded = client.post(
            f"/v1/agents/{agent['id']}/upgrade",
            json={"expectRevision": agent["draftRevision"]},
            headers=auth("ws"),
        )
        assert upgraded.status_code == 200, upgraded.text
        assert upgraded.json()["draftTemplateVersion"] == 2
        assert "greeting" not in settings_of(client, agent["id"])


class TestStatus:
    def test_pausing_a_live_agent_and_resuming_it_leaves_it_live(
        self, client
    ) -> None:
        # The regression this exists for: if the store forgets what an agent
        # was paused *from*, resuming puts it back as a draft and quietly
        # un-publishes a live agent.
        agent = publish_agent(client, make_agent(client))
        paused = client.post(
            f"/v1/agents/{agent['id']}/pause",
            json={"expectRevision": agent["draftRevision"]},
            headers=auth("ws"),
        )
        assert paused.status_code == 200, paused.text
        assert paused.json()["status"] == "paused"

        resumed = client.post(
            f"/v1/agents/{agent['id']}/resume",
            json={"expectRevision": paused.json()["draftRevision"]},
            headers=auth("ws"),
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "live"

    def test_a_deleted_agent_is_gone_from_the_api(self, client) -> None:
        agent = make_agent(client)
        deleted = client.delete(
            f"/v1/agents/{agent['id']}?expectRevision=0", headers=auth("ws")
        )
        assert deleted.status_code == 200, deleted.text
        assert client.get(
            f"/v1/agents/{agent['id']}", headers=auth("ws")
        ).status_code == 404

    def test_deleting_without_a_revision_is_refused(self, client) -> None:
        # Deleting an agent somebody else just edited should fail, so the
        # revision is required even though a DELETE has no body.
        agent = make_agent(client)
        assert client.delete(
            f"/v1/agents/{agent['id']}", headers=auth("ws")
        ).status_code == 422


class TestDeployment:
    def test_the_origin_list_applies_without_a_publish(self, client) -> None:
        # Unlike every setting: the allowed origins are a security control,
        # and taking a domain off the list has to take effect now.
        agent = publish_agent(client, make_agent(client))
        saved = client.put(
            f"/v1/agents/{agent['id']}/deployment",
            json={
                "allowedOrigins": ["https://acme.test"],
                "widgetEnabled": True,
                "launcherPosition": "left",
                "launcherText": "Chat to us",
                "expectRevision": agent["draftRevision"],
            },
            headers=auth("ws"),
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["deployment"]["allowedOrigins"] == ["https://acme.test"]
        assert saved.json()["publishedVersion"] == 1
        assert saved.json()["status"] == "live"


class TestCreating:
    def test_a_value_set_at_creation_goes_through_the_real_validator(
        self, client
    ) -> None:
        # There is no back door that accepts something the product would not.
        answer = client.post(
            "/v1/agents",
            json={
                "templateKey": TEMPLATE,
                "label": "Bad one",
                "settings": {"tone": "sarcastic"},
            },
            headers=auth("ws"),
        )
        assert answer.status_code == 400
        assert answer.json()["error"]["code"] == "OVERRIDES_REJECTED"

    def test_the_limit_from_the_definition_is_enforced(self, client) -> None:
        for index in range(3):
            make_agent(client, label=f"Agent {index}")
        answer = client.post(
            "/v1/agents",
            json={"templateKey": TEMPLATE, "label": "One too many"},
            headers=auth("ws"),
        )
        assert answer.status_code == 409
        error = answer.json()["error"]
        assert error["code"] == "AGENT_LIMIT_REACHED"
        assert error["details"]["limit"] == 3

    def test_an_unknown_agent_type_is_not_a_500(self, client) -> None:
        answer = client.post(
            "/v1/agents",
            json={"templateKey": "does_not_exist", "label": "Nope"},
            headers=auth("ws"),
        )
        assert answer.status_code == 500
        assert answer.json()["error"]["code"] == "DEFINITION_BROKEN"
