"""
The public path: a visitor, with no credential.

The tests here are mostly about what this route will *not* do. It is the only
unauthenticated surface in the product, so what it exposes is the whole of
what an agent id in a URL is worth -- and a citation that arrives with the
passage text in it is a leak that no test above this layer would catch,
because every layer above is right to carry it.
"""

from __future__ import annotations

from tests.api.conftest import (
    WORKSPACE,
    auth,
    make_agent,
    publish_agent,
    says,
)


def open_conversation(client, agent, *, origin: str = "") -> str:
    headers = {"Origin": origin} if origin else {}
    opened = client.post(
        f"/v1/widget/{WORKSPACE}/{agent['id']}/conversations",
        json={"pageUrl": "https://acme.test/pricing", "pageTitle": "Pricing"},
        headers=headers,
    )
    assert opened.status_code == 201, opened.text
    return opened.json()["conversationId"]


def say(client, agent, conversation_id: str, message: str, *, origin: str = ""):
    headers = {"Origin": origin} if origin else {}
    return client.post(
        f"/v1/widget/{WORKSPACE}/{agent['id']}/conversations/{conversation_id}/messages",
        json={"message": message},
        headers=headers,
    )


class TestOnlyALiveAgentAnswers:
    def test_a_draft_agent_does_not_answer_and_says_why(self, client) -> None:
        # Three cases need different words in front of a visitor: never
        # published, paused by its owner, paused by us. The status travels so
        # the widget can choose, and the API chooses nothing.
        agent = make_agent(client)
        answer = client.get(f"/v1/widget/{WORKSPACE}/{agent['id']}")
        assert answer.status_code == 409
        error = answer.json()["error"]
        assert error["code"] == "AGENT_NOT_LIVE"
        assert error["details"]["status"] == "draft"

    def test_a_paused_agent_does_not_answer(self, client) -> None:
        agent = publish_agent(client, make_agent(client))
        client.post(
            f"/v1/agents/{agent['id']}/pause",
            json={"expectRevision": agent["draftRevision"]},
            headers=auth("ws"),
        )
        answer = client.post(
            f"/v1/widget/{WORKSPACE}/{agent['id']}/conversations", json={}
        )
        assert answer.status_code == 409
        assert answer.json()["error"]["details"]["status"] == "paused"

    def test_an_agent_with_its_widget_switched_off_does_not_answer(
        self, client
    ) -> None:
        # ``widgetEnabled`` is a deployment control, so it applies now rather
        # than at the next publish -- and it applies to all three public
        # endpoints, including the face, so a widget that was turned off
        # stops drawing a launcher instead of failing on the first message.
        agent = publish_agent(client, make_agent(client))
        client.put(
            f"/v1/agents/{agent['id']}/deployment",
            json={
                "allowedOrigins": [],
                "widgetEnabled": False,
                "launcherPosition": "right",
                "launcherText": "",
                "expectRevision": agent["draftRevision"],
            },
            headers=auth("ws"),
        )

        face = client.get(f"/v1/widget/{WORKSPACE}/{agent['id']}")
        assert face.status_code == 409
        assert face.json()["error"]["code"] == "AGENT_NOT_LIVE"
        assert "switched off" in face.json()["error"]["details"]["reason"]

        opened = client.post(
            f"/v1/widget/{WORKSPACE}/{agent['id']}/conversations", json={}
        )
        assert opened.status_code == 409

    def test_an_unknown_agent_is_a_404(self, client) -> None:
        answer = client.get(f"/v1/widget/{WORKSPACE}/instance-nope")
        assert answer.status_code == 404
        assert answer.json()["error"]["code"] == "AGENT_NOT_FOUND"


class TestTheAgentsPublicFace:
    def test_it_carries_what_the_launcher_needs_and_nothing_else(
        self, client, live_agent
    ) -> None:
        face = client.get(f"/v1/widget/{WORKSPACE}/{live_agent['id']}")
        assert face.status_code == 200
        body = face.json()
        assert body["agentName"] == "Ava"
        assert body["greeting"].startswith("Hi")
        assert body["launcher"] == {"position": "right", "text": ""}
        assert body["templateVersion"] == 1
        assert body["instanceVersion"] == 1
        # No settings, no overrides, no label, no deployment list: an id in a
        # URL is a capability, so the capability stays small.
        assert set(body) == {
            "agentName",
            "greeting",
            "launcher",
            "templateVersion",
            "instanceVersion",
        }

    def test_a_published_change_reaches_the_widget(self, client) -> None:
        agent = publish_agent(client, make_agent(client))
        edited = client.patch(
            f"/v1/agents/{agent['id']}",
            json={
                "settings": {"agentName": "Noor"},
                "expectRevision": agent["draftRevision"],
            },
            headers=auth("ws"),
        ).json()

        # Not yet: live traffic reads the published snapshot, never the draft.
        assert (
            client.get(f"/v1/widget/{WORKSPACE}/{agent['id']}").json()["agentName"]
            == "Ava"
        )

        client.post(
            f"/v1/agents/{agent['id']}/publish",
            json={"expectRevision": edited["draftRevision"]},
            headers=auth("ws"),
        )
        assert (
            client.get(f"/v1/widget/{WORKSPACE}/{agent['id']}").json()["agentName"]
            == "Noor"
        )


class TestSaying:
    def test_one_message_in_one_reply_out(self, client, live_agent, chat) -> None:
        chat.replies.append(says("We are sold per seat."))
        conversation_id = open_conversation(client, live_agent)
        answer = say(client, live_agent, conversation_id, "How is it priced?")
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body["text"] == "We are sold per seat."
        assert body["open"] is True
        assert body["conversationId"] == conversation_id

    def test_an_empty_message_never_reaches_the_model(
        self, client, live_agent, chat
    ) -> None:
        conversation_id = open_conversation(client, live_agent)
        answer = say(client, live_agent, conversation_id, "   ")
        assert answer.status_code == 422
        assert answer.json()["error"]["code"] == "VALIDATION_FAILED"
        assert chat.asked == []

    def test_a_visitor_sees_a_citation_and_not_the_passage(
        self, client, live_agent, chat
    ) -> None:
        # The retrieval layer hands the turn the whole passage -- its text, its
        # score, its source id -- and all of it is in the transcript an admin
        # can read. A visitor gets what a citation is for.
        uploaded = client.post(
            "/v1/knowledge/sources",
            files={
                "file": (
                    "pricing.txt",
                    b"Pricing. The Starter plan is 29 per seat per month and "
                    b"includes web chat. The Growth plan is 49 per seat.",
                    "text/plain",
                )
            },
            headers=auth("ws"),
        )
        assert uploaded.json()["passages"] > 0

        # No scripted tool call: the turn searches the knowledge before the
        # first model call, because an agent that answers from memory and only
        # searches when it feels uncertain is wrong exactly when it matters.
        chat.replies.append(
            says("The Starter plan is 29 per seat per month and includes web chat.")
        )
        conversation_id = open_conversation(client, live_agent)
        answer = say(
            client,
            live_agent,
            conversation_id,
            "How much is the Starter plan per seat per month?",
        )
        assert answer.status_code == 200, answer.text

        sources = answer.json()["sources"]
        assert sources, "the reply quoted the passage, so it should cite it"
        for source in sources:
            assert set(source) == {"name", "url"}
        assert sources[0]["name"] == "pricing.txt"

        # The same turn, for the workspace that owns it: everything.
        transcript = client.get(
            f"/v1/conversations/{conversation_id}", headers=auth("ws")
        ).json()
        agent_turn = [
            turn for turn in transcript["turnsDetail"] if turn["speaker"] == "agent"
        ][-1]
        assert "text" in agent_turn["sources"][0]
        assert "score" in agent_turn["sources"][0]
        assert [call["key"] for call in agent_turn["calls"]] == ["search_knowledge"]

    def test_the_turn_limit_is_enforced_before_the_model(
        self, client, live_agent, chat
    ) -> None:
        # A setting that asks the model to stop after four turns is a
        # suggestion; a check before the call is a limit.
        edited = client.patch(
            f"/v1/agents/{live_agent['id']}",
            json={
                "settings": {"maxTurns": 4},
                "expectRevision": live_agent["draftRevision"],
            },
            headers=auth("ws"),
        ).json()
        client.post(
            f"/v1/agents/{live_agent['id']}/publish",
            json={"expectRevision": edited["draftRevision"]},
            headers=auth("ws"),
        )

        conversation_id = open_conversation(client, live_agent)
        for turn in range(4):
            chat.replies.append(says(f"Reply {turn}"))
            answer = say(client, live_agent, conversation_id, f"Question {turn}")
            assert answer.status_code == 200, answer.text
        # The widget was told the conversation was closing with the last reply,
        # rather than finding out on the next message.
        assert answer.json()["open"] is False

        asked_before = len(chat.asked)
        refused = say(client, live_agent, conversation_id, "One more question")
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "CONVERSATION_TOO_LONG"
        assert refused.json()["error"]["details"]["limit"] == 4
        assert len(chat.asked) == asked_before, "no model call for a refused turn"

        # And the refused message is still in the transcript: they said it.
        transcript = client.get(
            f"/v1/conversations/{conversation_id}", headers=auth("ws")
        ).json()
        assert transcript["visitorTurns"] == 5
        assert transcript["turnsDetail"][-1]["text"] == "One more question"
        assert transcript["turnsDetail"][-1]["ending"] == "too_long"

    def test_an_unknown_conversation_is_a_404(self, client, live_agent) -> None:
        answer = say(client, live_agent, "conversation-nope", "Hello?")
        assert answer.status_code == 404


class TestWhereTheWidgetMayBeUsed:
    def _restrict(self, client, agent) -> None:
        saved = client.put(
            f"/v1/agents/{agent['id']}/deployment",
            json={
                "allowedOrigins": ["https://acme.test"],
                "expectRevision": agent["draftRevision"],
            },
            headers=auth("ws"),
        )
        assert saved.status_code == 200, saved.text

    def test_an_unconfigured_list_allows_anything(self, client, live_agent) -> None:
        # A workspace that has not set up its domains should be able to try
        # the agent: the list is a deployment control, not authentication.
        assert (
            client.get(
                f"/v1/widget/{WORKSPACE}/{live_agent['id']}",
                headers={"Origin": "https://anywhere.test"},
            ).status_code
            == 200
        )

    def test_a_site_not_on_the_list_is_refused(self, client, live_agent) -> None:
        self._restrict(client, live_agent)
        answer = client.get(
            f"/v1/widget/{WORKSPACE}/{live_agent['id']}",
            headers={"Origin": "https://somewhere-else.test"},
        )
        assert answer.status_code == 403
        assert answer.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"

    def test_the_allowed_site_still_works(self, client, live_agent, chat) -> None:
        self._restrict(client, live_agent)
        chat.replies.append(says("Of course."))
        conversation_id = open_conversation(
            client, live_agent, origin="https://acme.test"
        )
        answer = say(
            client,
            live_agent,
            conversation_id,
            "Can you help?",
            origin="https://acme.test",
        )
        assert answer.status_code == 200, answer.text

    def test_the_list_is_checked_on_every_message(self, client, live_agent) -> None:
        # Not only when the conversation opens. A check that stops applying
        # once a conversation id is known is no check: the id is shared by the
        # page it was created on.
        conversation_id = open_conversation(client, live_agent)
        self._restrict(client, live_agent)
        refused = say(
            client,
            live_agent,
            conversation_id,
            "Still there?",
            origin="https://somewhere-else.test",
        )
        assert refused.status_code == 403

    def test_taking_a_domain_off_the_list_needs_no_publish(
        self, client, live_agent
    ) -> None:
        # The one operational setting: an agent on a site it should never have
        # been on is an incident, and an incident must not wait for a publish.
        self._restrict(client, live_agent)
        assert (
            client.get(
                f"/v1/widget/{WORKSPACE}/{live_agent['id']}",
                headers={"Origin": "https://anywhere.test"},
            ).status_code
            == 403
        )


class TestCrossOrigin:
    def test_a_widget_request_answers_any_origin_without_credentials(
        self, client, live_agent
    ) -> None:
        answer = client.get(
            f"/v1/widget/{WORKSPACE}/{live_agent['id']}",
            headers={"Origin": "https://a-customers-site.test"},
        )
        assert answer.headers["access-control-allow-origin"] == (
            "https://a-customers-site.test"
        )
        assert answer.headers["vary"] == "Origin"
        # The reason echoing the origin is safe: no cookie and no bearer token
        # is ever attached, so a hostile page learns only what it could have
        # learned from its own server.
        assert "access-control-allow-credentials" not in answer.headers

    def test_a_preflight_is_answered_by_the_policy(self, client) -> None:
        answer = client.options(
            f"/v1/widget/{WORKSPACE}/instance-1/conversations",
            headers={
                "Origin": "https://a-customers-site.test",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert answer.status_code == 204
        assert answer.headers["access-control-allow-origin"] == (
            "https://a-customers-site.test"
        )
        assert "POST" in answer.headers["access-control-allow-methods"]

    def test_the_admin_api_does_not_answer_a_stranger(self, client) -> None:
        # The wildcard must never reach the routes that can delete a
        # workspace's knowledge.
        answer = client.get(
            "/v1/agents",
            headers={"Origin": "https://a-customers-site.test", **auth("ws")},
        )
        assert answer.status_code == 200
        assert "access-control-allow-origin" not in answer.headers
