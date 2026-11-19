"""
Trying a change before visitors get it.

The preview is the only caller of ``resolve_draft``, and the whole reason that
function exists: an admin editing settings needs to see what they will do. A
preview that answered from the published snapshot would be a preview of
nothing, so the test that matters is that the two differ.
"""

from __future__ import annotations

from tests.api.conftest import WORKSPACE, auth, make_agent, publish_agent, says


class TestPreviewingTheDraft:
    def test_a_draft_can_be_talked_to_before_it_is_published(
        self, client, chat
    ) -> None:
        # Status is not checked: previewing something not yet live is what
        # this is for.
        agent = make_agent(client)
        assert agent["status"] == "draft"

        chat.replies.append(says("Hello, I am the draft."))
        opened = client.post(
            f"/v1/agents/{agent['id']}/preview/conversations",
            json={},
            headers=auth("ws"),
        )
        assert opened.status_code == 201, opened.text
        conversation_id = opened.json()["conversationId"]
        # No instance version, because a draft has no published version --
        # which is also how a preview is told apart from real traffic later.
        assert opened.json()["instanceVersion"] is None
        assert opened.json()["templateVersion"] == 1

        answer = client.post(
            f"/v1/agents/{agent['id']}/preview/conversations/{conversation_id}/messages",
            json={"message": "Who am I talking to?"},
            headers=auth("ws"),
        )
        assert answer.status_code == 200, answer.text
        assert answer.json()["text"] == "Hello, I am the draft."

    def test_the_preview_uses_the_draft_and_the_widget_does_not(
        self, client, chat
    ) -> None:
        agent = publish_agent(client, make_agent(client))
        client.patch(
            f"/v1/agents/{agent['id']}",
            json={
                "settings": {"agentName": "Noor"},
                "expectRevision": agent["draftRevision"],
            },
            headers=auth("ws"),
        )

        # The visitor still gets the published snapshot.
        assert (
            client.get(f"/v1/widget/{WORKSPACE}/{agent['id']}").json()["agentName"]
            == "Ava"
        )

        # The preview gets the edit, and the proof is in the prompt the model
        # was given rather than in anything the API chose to echo back.
        chat.replies.append(says("Hello."))
        opened = client.post(
            f"/v1/agents/{agent['id']}/preview/conversations",
            json={},
            headers=auth("ws"),
        )
        client.post(
            f"/v1/agents/{agent['id']}/preview/conversations/"
            f"{opened.json()['conversationId']}/messages",
            json={"message": "Hello?"},
            headers=auth("ws"),
        )
        assert "Noor" in chat.asked[-1]["system"]
        assert "Ava" not in chat.asked[-1]["system"]

    def test_a_preview_is_marked_in_the_conversations_list(
        self, client, chat
    ) -> None:
        agent = make_agent(client)
        chat.replies.append(says("Hello."))
        opened = client.post(
            f"/v1/agents/{agent['id']}/preview/conversations",
            json={},
            headers=auth("ws"),
        )
        client.post(
            f"/v1/agents/{agent['id']}/preview/conversations/"
            f"{opened.json()['conversationId']}/messages",
            json={"message": "Hello?"},
            headers=auth("ws"),
        )
        listed = client.get("/v1/conversations", headers=auth("ws")).json()
        # Flagged rather than hidden: a conversation filtered out of every
        # list is one somebody will go looking for.
        assert [one["preview"] for one in listed] == [True]

    def test_another_workspace_cannot_preview_this_draft(self, client) -> None:
        agent = make_agent(client)
        answer = client.post(
            f"/v1/agents/{agent['id']}/preview/conversations",
            json={},
            headers=auth("other"),
        )
        assert answer.status_code == 404
