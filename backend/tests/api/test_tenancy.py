"""
Whose data is it.

The single most expensive bug this layer can have, so it gets the first test
file. Every test here asks the same question from a different angle: can one
workspace's token reach another workspace's anything.

The shape of the answer matters as much as the answer. A request for somebody
else's agent gets exactly what a request for a made-up id gets -- same status,
same code, same words. A 403 for one and a 404 for the other is an
enumeration oracle: it tells whoever is trying that the id exists.
"""

from __future__ import annotations

from tests.api.conftest import (
    OTHER_WORKSPACE,
    TEMPLATE,
    WORKSPACE,
    auth,
    make_agent,
    says,
)


def names_a_workspace(field: str) -> bool:
    """Whether a field name is a workspace *identifier*.

    Narrower than "contains the word", which would also catch
    ``maxPerWorkspace`` -- a count, and exactly the kind of field a workspace
    should be told. What must not exist is somewhere to put an id.
    """
    plain = field.lower().replace("_", "").replace("-", "")
    return plain == "workspace" or plain.endswith("workspaceid")


class TestWhoIsAsking:
    def test_no_credential_is_refused(self, client) -> None:
        answer = client.get("/v1/agents")
        assert answer.status_code == 401
        assert answer.json()["error"]["code"] == "UNAUTHENTICATED"

    def test_an_unknown_token_is_refused(self, client) -> None:
        answer = client.get(
            "/v1/agents", headers={"Authorization": "Bearer tok_made_up"}
        )
        assert answer.status_code == 401

    def test_another_scheme_is_not_a_token(self, client) -> None:
        # Basic auth with the right token in it is still not a bearer token.
        answer = client.get(
            "/v1/agents", headers={"Authorization": "Basic tok_acme_1234"}
        )
        assert answer.status_code == 401

    def test_a_platform_token_has_no_workspace(self, client) -> None:
        # It can publish an agent type and it cannot read a customer's agents.
        # Those are different jobs and a token does not get the second by
        # having the first.
        answer = client.get("/v1/agents", headers=auth("platform"))
        assert answer.status_code == 403
        assert answer.json()["error"]["code"] == "FORBIDDEN"

    def test_a_workspace_token_cannot_change_an_agent_type(self, client) -> None:
        # A template version is resolved by every workspace pinned to it, so
        # publishing one is the platform's key and not a customer's.
        answer = client.post(
            f"/v1/templates/{TEMPLATE}/publish",
            json={"expectRevision": 0},
            headers=auth("ws"),
        )
        assert answer.status_code == 403

    def test_the_catalogue_needs_a_workspace_and_not_the_platform(self, client) -> None:
        assert client.get("/v1/catalogue", headers=auth("ws")).status_code == 200
        assert client.get("/v1/catalogue", headers=auth("platform")).status_code == 403


class TestTheWorkspaceComesFromTheToken:
    def test_no_admin_route_will_accept_one_from_the_request(self, client) -> None:
        """The general form of the rule, read off the API's own description.

        A test that sends one hand-picked field and watches it be ignored
        proves only that that field is ignored. This asserts that there is
        nowhere to put a workspace at all: no admin path, query or header
        parameter names one, and no schema in the API declares one. A route
        added next year is covered by having been added.

        The widget paths are excluded deliberately, and are the only
        exclusion: a visitor has no credential, so there the ids in the URL
        *are* the public address of a chat widget.
        """
        spec = client.app.openapi()

        for path, operations in spec["paths"].items():
            if path.startswith("/v1/widget"):
                continue
            for method, operation in operations.items():
                for parameter in operation.get("parameters", []):
                    # Substring, for a parameter: no admin path, query or
                    # header has any business mentioning a workspace, whatever
                    # it is called -- ``X-Workspace``, ``tenant_workspace``,
                    # anything. The narrower rule below is for schemas, where
                    # a *count* per workspace is a legitimate field.
                    assert "workspace" not in parameter["name"].lower(), (
                        f"{method.upper()} {path} takes a workspace from the request"
                    )

        for name, schema in spec["components"]["schemas"].items():
            offending = [
                field
                for field in schema.get("properties", {})
                if names_a_workspace(field)
            ]
            assert not offending, f"{name} declares {offending}"


    def test_smuggling_one_in_anyway_changes_nothing(self, client) -> None:
        # The behavioural half, for the places a declaration would not show:
        # a header or a query string nobody declared is simply not read, so
        # the agent is created in the token's workspace and the other
        # workspace still sees nothing.
        smuggled = {
            "X-Workspace": OTHER_WORKSPACE,
            "X-Workspace-Id": OTHER_WORKSPACE,
            "X-Tenant": OTHER_WORKSPACE,
            **auth("ws"),
        }
        created = client.post(
            f"/v1/agents?workspaceId={OTHER_WORKSPACE}&workspace_id={OTHER_WORKSPACE}",
            json={"templateKey": TEMPLATE, "label": "Smuggled"},
            headers=smuggled,
        )
        assert created.status_code == 201, created.text
        assert client.get("/v1/agents", headers=auth("other")).json() == []
        assert [one["label"] for one in client.get(
            "/v1/agents", headers=auth("ws")
        ).json()] == ["Smuggled"]

    def test_a_body_cannot_carry_a_workspace(self, client) -> None:
        # Not ignored -- refused. The guarantee is that there is no way to
        # send one, which is stronger than a promise not to read it.
        answer = client.post(
            "/v1/agents",
            json={
                "templateKey": TEMPLATE,
                "label": "Smuggled",
                "workspaceId": OTHER_WORKSPACE,
            },
            headers=auth("ws"),
        )
        assert answer.status_code == 422
        problems = answer.json()["error"]["details"]["problems"]
        assert any("workspaceId" in one["path"] for one in problems)

    def test_an_agent_is_created_in_the_tokens_workspace(self, client) -> None:
        agent = make_agent(client, who="ws")
        # Visible to its own workspace and to nobody else, which is the only
        # observable form the claim has.
        assert client.get(
            f"/v1/agents/{agent['id']}", headers=auth("ws")
        ).status_code == 200
        assert client.get(
            f"/v1/agents/{agent['id']}", headers=auth("other")
        ).status_code == 404

    def test_listing_shows_only_your_own(self, client) -> None:
        make_agent(client, label="Acme one", who="ws")
        make_agent(client, label="Acme two", who="ws")
        make_agent(client, label="Rival one", who="other")

        mine = client.get("/v1/agents", headers=auth("ws")).json()
        theirs = client.get("/v1/agents", headers=auth("other")).json()
        assert [one["label"] for one in mine] == ["Acme one", "Acme two"]
        assert [one["label"] for one in theirs] == ["Rival one"]


class TestOneWorkspaceCannotReachAnother:
    def test_the_answer_is_the_same_as_for_a_made_up_id(self, client) -> None:
        agent = make_agent(client, who="ws")
        theirs = client.get(f"/v1/agents/{agent['id']}", headers=auth("other"))
        nothing = client.get("/v1/agents/instance-does-not-exist", headers=auth("other"))
        assert theirs.status_code == nothing.status_code == 404
        assert (
            theirs.json()["error"]["code"] == nothing.json()["error"]["code"]
        )
        # The message quotes what was asked for and says nothing else: it
        # reads the same whether the id exists in another workspace or
        # nowhere at all.
        assert agent["id"] in theirs.json()["error"]["message"]
        assert "instance-does-not-exist" in nothing.json()["error"]["message"]

    def test_editing_somebody_elses_agent_is_a_404(self, client) -> None:
        agent = make_agent(client, who="ws")
        answer = client.patch(
            f"/v1/agents/{agent['id']}",
            json={"settings": {"greeting": "Mine now"}, "expectRevision": 0},
            headers=auth("other"),
        )
        assert answer.status_code == 404
        # And nothing was written.
        form = client.get(f"/v1/agents/{agent['id']}/settings", headers=auth("ws"))
        greeting = next(
            one for one in form.json()["settings"] if one["key"] == "greeting"
        )
        assert greeting["overridden"] is False

    def test_publishing_somebody_elses_agent_is_a_404(self, client) -> None:
        agent = make_agent(client, who="ws")
        answer = client.post(
            f"/v1/agents/{agent['id']}/publish",
            json={"expectRevision": 0},
            headers=auth("other"),
        )
        assert answer.status_code == 404

    def test_deleting_somebody_elses_agent_is_a_404(self, client) -> None:
        agent = make_agent(client, who="ws")
        answer = client.delete(
            f"/v1/agents/{agent['id']}?expectRevision=0", headers=auth("other")
        )
        assert answer.status_code == 404
        assert client.get(
            f"/v1/agents/{agent['id']}", headers=auth("ws")
        ).status_code == 200


class TestKnowledgeIsScoped:
    def test_a_document_belongs_to_one_workspace(self, client) -> None:
        uploaded = client.post(
            "/v1/knowledge/sources",
            files={"file": ("pricing.txt", b"Starter is 29 per seat.", "text/plain")},
            headers=auth("ws"),
        )
        assert uploaded.status_code == 201, uploaded.text
        source_id = uploaded.json()["sourceId"]

        assert client.get("/v1/knowledge/sources", headers=auth("other")).json() == []
        assert client.get(
            f"/v1/knowledge/sources/{source_id}", headers=auth("other")
        ).status_code == 404
        assert client.get(
            f"/v1/knowledge/sources/{source_id}/original", headers=auth("other")
        ).status_code == 404

    def test_deleting_somebody_elses_document_is_a_404(self, client) -> None:
        uploaded = client.post(
            "/v1/knowledge/sources",
            files={"file": ("notes.txt", b"Internal notes.", "text/plain")},
            headers=auth("ws"),
        )
        source_id = uploaded.json()["sourceId"]
        assert client.delete(
            f"/v1/knowledge/sources/{source_id}", headers=auth("other")
        ).status_code == 404
        # Still there, still indexed.
        assert client.get(
            f"/v1/knowledge/sources/{source_id}", headers=auth("ws")
        ).status_code == 200


class TestConversationsAreScoped:
    def test_another_workspace_cannot_read_a_transcript(
        self, client, live_agent, chat
    ) -> None:
        chat.replies.append(says("Hello, pricing starts at 29 per seat."))
        opened = client.post(
            f"/v1/widget/{WORKSPACE}/{live_agent['id']}/conversations", json={}
        )
        conversation_id = opened.json()["conversationId"]
        client.post(
            f"/v1/widget/{WORKSPACE}/{live_agent['id']}/conversations/{conversation_id}/messages",
            json={"message": "What does it cost?"},
        )

        assert client.get(
            f"/v1/conversations/{conversation_id}", headers=auth("ws")
        ).status_code == 200
        assert client.get(
            f"/v1/conversations/{conversation_id}", headers=auth("other")
        ).status_code == 404
        assert client.get("/v1/conversations", headers=auth("other")).json() == []
        assert client.get("/v1/leads", headers=auth("other")).json() == []

    def test_a_conversation_cannot_be_moved_to_another_agents_url(
        self, client, live_agent, chat
    ) -> None:
        # The agent and the conversation are both scoped, so a conversation id
        # from one workspace is not usable under another's path even with that
        # workspace's own live agent id.
        opened = client.post(
            f"/v1/widget/{WORKSPACE}/{live_agent['id']}/conversations", json={}
        )
        conversation_id = opened.json()["conversationId"]
        answer = client.post(
            f"/v1/widget/{OTHER_WORKSPACE}/{live_agent['id']}/conversations/{conversation_id}/messages",
            json={"message": "Hello?"},
        )
        assert answer.status_code == 404
        assert answer.json()["error"]["code"] == "AGENT_NOT_FOUND"
