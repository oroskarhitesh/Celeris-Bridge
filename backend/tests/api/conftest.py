"""
One app, real everything except the model.

The adapters are the in-memory ones, the services are the real services, the
authenticator is the real token authenticator with three real tokens, and the
definition is the shipped ``marketing_v1`` published through the API itself.
Only the model is scripted, because a test that spends money on every run is a
test people stop running.

Two tokens belong to different workspaces and one is the platform's. That is
the whole tenancy story in a fixture: every test can ask what the other
workspace sees, which is the question a hand-written single-tenant fixture
cannot ask at all.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

import pytest
from fastapi.testclient import TestClient

from celeris_bridge.api.app import create_app
from celeris_bridge.composition.settings import Settings
from celeris_bridge.definitions import load_definition
from celeris_bridge.ports.chat import (
    ChatMessage,
    ChatReply,
    TextBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)
from celeris_bridge.services.conversations import ConversationService

TEMPLATE = "marketing_v1"
WORKSPACE = "ws_acme"
OTHER_WORKSPACE = "ws_rival"

TOKENS = {
    "ws": "tok_acme_1234",
    "other": "tok_rival_5678",
    "platform": "tok_platform_9999",
    # A support engineer: inside one workspace *and* holding the platform
    # role. Not a contradiction -- it is what an internal tool needs, and it
    # is the only principal that sees an admin-only setting on a form.
    "support": "tok_support_4242",
}


def auth(who: str) -> dict[str, str]:
    """Headers for one of the three tokens. Spelled out at every call site on
    purpose: a test that does not say who is asking is a test about nothing."""
    return {"Authorization": f"Bearer {TOKENS[who]}"}


def test_settings(**overrides: Any) -> Settings:
    """Settings that cannot be changed by the machine the tests run on.

    ``_env_file=None`` and every field that matters passed explicitly, because
    a developer with ``CB_ADAPTERS=mongo`` in a ``.env`` should not have a
    different test suite from everybody else.
    """
    return Settings(
        _env_file=None,
        adapters="memory",
        embeddings="development",
        document_root="",
        company="Acme",
        api_tokens=(
            f"{TOKENS['ws']}:{WORKSPACE},"
            f"{TOKENS['other']}:{OTHER_WORKSPACE},"
            f"{TOKENS['platform']}::platform,"
            f"{TOKENS['support']}:{WORKSPACE}:platform"
        ),
        admin_origins="",
        **overrides,
    )


class ScriptedModel:
    """The model, replaced by a list."""

    def __init__(self, *replies: ChatReply) -> None:
        self.replies = list(replies)
        self.asked: list[dict[str, Any]] = []

    async def chat(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        model: str = "",
        max_tokens: int = 1024,
        effort: str = "",
    ) -> ChatReply:
        self.asked.append(
            {
                "system": system,
                "messages": list(messages),
                "tools": list(tools),
                "model": model,
                "effort": effort,
            }
        )
        if self.replies:
            return self.replies.pop(0)
        return ChatReply(content=(TextBlock(text="(nothing scripted)"),))


def says(text: str) -> ChatReply:
    return ChatReply(
        content=(TextBlock(text=text),),
        model="claude-opus-5-5",
        usage=Usage(input_tokens=200, output_tokens=40),
    )


def calls(key: str, **arguments: Any) -> ChatReply:
    return ChatReply(
        content=(ToolUseBlock(id="call_1", name=key, arguments=arguments),),
        model="claude-opus-5-5",
        usage=Usage(input_tokens=200, output_tokens=20),
    )


@pytest.fixture
def chat() -> ScriptedModel:
    return ScriptedModel()


@pytest.fixture
def client(chat: ScriptedModel):
    """The API, running, with ``marketing_v1`` published as version 1.

    The template is seeded through the API rather than the store, so the
    fixture is itself a test of the platform routes -- and so no test can pass
    against a template that could not have been published.
    """
    app = create_app(test_settings())
    with TestClient(app) as running:
        built = app.state.application
        # The one substitution. ``Application`` is frozen, so this is a new
        # one with the same stores: nothing else is replaced, and the
        # conversation service is the real one.
        app.state.application = replace(
            built,
            conversations=ConversationService(
                conversations=built.stores.conversations,
                chat=chat,
                visitors=built.services.visitors,
            ),
        )

        created = running.post(
            "/v1/templates",
            json={"key": TEMPLATE, "draft": load_definition(TEMPLATE)},
            headers=auth("platform"),
        )
        assert created.status_code == 201, created.text
        published = running.post(
            f"/v1/templates/{TEMPLATE}/publish",
            json={
                "expectRevision": created.json()["draftRevision"],
                "changeSummary": "First version",
            },
            headers=auth("platform"),
        )
        assert published.status_code == 201, published.text
        assert published.json()["version"]["version"] == 1
        yield running


def make_agent(client: TestClient, *, label: str = "Main site", who: str = "ws", **body):
    """An agent, as a draft. Returns the response body."""
    created = client.post(
        "/v1/agents",
        json={"templateKey": TEMPLATE, "label": label, **body},
        headers=auth(who),
    )
    assert created.status_code == 201, created.text
    return created.json()


def publish_agent(client: TestClient, agent: dict, *, who: str = "ws") -> dict:
    """Publish a draft and return the agent as it is afterwards.

    No second request to find out what the revision is now: a publish returns
    the fresh record, which is the only reason a client can chain writes.
    """
    published = client.post(
        f"/v1/agents/{agent['id']}/publish",
        json={"expectRevision": agent["draftRevision"]},
        headers=auth(who),
    )
    assert published.status_code == 201, published.text
    return published.json()["agent"]


@pytest.fixture
def live_agent(client: TestClient) -> dict:
    """An agent a visitor can talk to."""
    return publish_agent(client, make_agent(client))
