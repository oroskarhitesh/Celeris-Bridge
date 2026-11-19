"""
What came of the conversations.

The leads list is a read model, so what matters here is that a lead appears
because a conversation produced details -- not because anything was posted to
a leads endpoint, of which there is none.
"""

from __future__ import annotations

from tests.api.conftest import WORKSPACE, auth, calls, make_agent, publish_agent, says


def talk(client, agent, *, message: str = "I would like a demo.") -> str:
    """One conversation and one message, with the replies already scripted."""
    opened = client.post(
        f"/v1/widget/{WORKSPACE}/{agent['id']}/conversations",
        json={"pageUrl": "https://acme.test/pricing", "pageTitle": "Pricing"},
    )
    conversation_id = opened.json()["conversationId"]
    answer = client.post(
        f"/v1/widget/{WORKSPACE}/{agent['id']}/conversations/{conversation_id}/messages",
        json={"message": message},
    )
    assert answer.status_code == 200, answer.text
    return conversation_id


class TestTheLeadsList:
    def test_a_conversation_with_no_details_is_not_a_lead(
        self, client, live_agent, chat
    ) -> None:
        # A public widget has far more conversations than leads, and a list
        # that shows every passer-by who said "hi" buries the ones worth
        # calling.
        chat.replies.append(says("Hello."))
        talk(client, live_agent, message="hi")

        assert len(client.get("/v1/conversations", headers=auth("ws")).json()) == 1
        everything = client.get("/v1/leads", headers=auth("ws")).json()
        assert len(everything) == 1
        assert everything[0]["details"] == {}
        filtered = client.get(
            "/v1/leads?withDetailsOnly=true", headers=auth("ws")
        ).json()
        assert filtered == []

    def test_details_the_agent_saved_become_a_lead(
        self, client, live_agent, chat
    ) -> None:
        chat.replies.append(
            calls(
                "save_visitor_details",
                full_name="Dana Shah",
                email="dana@acme.test",
                visitorType="prospect",
            )
        )
        chat.replies.append(says("Thanks Dana, I will send a booking link."))
        conversation_id = talk(
            client, live_agent, message="I am Dana, dana@acme.test, we need a demo."
        )

        leads = client.get("/v1/leads?withDetailsOnly=true", headers=auth("ws")).json()
        assert len(leads) == 1
        lead = leads[0]
        assert lead["conversationId"] == conversation_id
        assert lead["details"]["full_name"] == "Dana Shah"
        assert lead["details"]["email"] == "dana@acme.test"
        assert lead["visitorType"] == "prospect"
        # The first thing they said, in their own words: the most useful
        # column there is.
        assert lead["openingMessage"].startswith("I am Dana")
        assert lead["pageUrl"] == "https://acme.test/pricing"
        # Everything the workspace requires before a booking was answered, so
        # there is nothing to follow up on. ``company_size`` is asked but not
        # required, which is why it is not missing.
        assert lead["missing"] == []
        assert lead["complete"] is True

    def test_a_half_answered_lead_says_what_is_missing(
        self, client, live_agent, chat
    ) -> None:
        chat.replies.append(calls("save_visitor_details", full_name="Dana Shah"))
        chat.replies.append(says("Thanks Dana. What is the best email for you?"))
        talk(client, live_agent, message="I am Dana and we need a demo.")

        lead = client.get("/v1/leads?withDetailsOnly=true", headers=auth("ws")).json()[0]
        assert lead["missing"] == ["email"]
        assert lead["complete"] is False

    def test_one_lead_can_be_read_on_its_own(self, client, live_agent, chat) -> None:
        chat.replies.append(
            calls("save_visitor_details", full_name="Dana Shah", email="d@acme.test")
        )
        chat.replies.append(says("Thank you."))
        conversation_id = talk(client, live_agent)

        lead = client.get(f"/v1/leads/{conversation_id}", headers=auth("ws")).json()
        assert lead["details"]["full_name"] == "Dana Shah"

    def test_a_lead_that_does_not_exist_is_a_404(self, client) -> None:
        answer = client.get("/v1/leads/conversation-nope", headers=auth("ws"))
        assert answer.status_code == 404

    def test_filtering_by_agent(self, client, chat) -> None:
        first = publish_agent(client, make_agent(client, label="One"))
        second = publish_agent(client, make_agent(client, label="Two"))
        chat.replies.append(says("Hello from one."))
        talk(client, first)
        chat.replies.append(says("Hello from two."))
        talk(client, second)

        only_second = client.get(
            f"/v1/leads?instanceId={second['id']}", headers=auth("ws")
        ).json()
        assert [one["instanceId"] for one in only_second] == [second["id"]]


class TestTheConversationsList:
    def test_a_transcript_carries_what_a_turn_cost(
        self, client, live_agent, chat
    ) -> None:
        chat.replies.append(says("Hello."))
        conversation_id = talk(client, live_agent, message="hi")
        transcript = client.get(
            f"/v1/conversations/{conversation_id}", headers=auth("ws")
        ).json()
        assert transcript["inputTokens"] == 200
        assert transcript["outputTokens"] == 40
        assert transcript["templateVersion"] == 1
        assert transcript["instanceVersion"] == 1
        assert transcript["preview"] is False
