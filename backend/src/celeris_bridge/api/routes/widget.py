"""
The public path: a visitor talking to a live agent.

This is the only router with no credential, and the only one where the
workspace comes from the URL. That is not a hole in the tenancy rule, it is
the other side of it: a visitor has no account to authenticate, so the agent's
ids *are* the public address of a chat widget -- the same kind of identifier
as the script tag that loads it. What stops them being useful for anything
else is that this router exposes exactly three things and each of them
refuses unless the agent is live:

* the agent's public face, so the launcher can render before anyone clicks,
* opening a conversation,
* saying one thing in it.

Nothing here can read a transcript, list conversations, see another agent, or
learn that a workspace has more than this one. An id in a URL is a capability,
so the capability has to be small.

Two narrowings are deliberate and easy to lose by accident.

**A citation sent to a visitor is a name and a link.** The retrieval layer
hands the turn the whole passage -- its text, its score, its source id -- and
all of that is in the stored transcript where an admin can read it. The
visitor gets what a citation is for.

**``Origin`` is a deployment control, not authentication.** It is checked on
every request here against the agent's own list, which keeps a customer's
agent off sites it does not belong on. A browser cannot forge it; anything
that is not a browser can. See :mod:`celeris_bridge.api.cors`.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Header
from pydantic import Field

from celeris_bridge.api.dependencies import App
from celeris_bridge.api.models import Payload, View
from celeris_bridge.domain.conversation import Channel
from celeris_bridge.services.conversations import Reply
from celeris_bridge.services.resolution import AgentNotLive, ResolvedAgent, resolve_live

router = APIRouter(prefix="/v1/widget", tags=["widget"])


class StartConversation(Payload):
    #: Where the visitor was. Useful on the leads list -- "they were on the
    #: pricing page" is most of a qualification on its own.
    page_url: str = Field(default="", max_length=2000)
    page_title: str = Field(default="", max_length=300)


class SayMessage(Payload):
    #: One visitor message. The length cap is the request's business: a
    #: 50,000-character paste is not a chat message, and refusing it here
    #: costs nothing where refusing it after the model call costs a call.
    message: str = Field(min_length=1, max_length=4000)


class CitationView(View):
    """A citation, as a visitor sees it: where it came from."""

    name: str = ""
    url: str | None = None


class LauncherView(View):
    position: str = "right"
    text: str = ""


class AgentFaceView(View):
    """What the widget needs before a conversation exists."""

    agent_name: str = ""
    greeting: str = ""
    launcher: LauncherView
    #: The versions serving this visitor. Sent so a bug report from a
    #: customer's site can be read against the configuration that produced it.
    template_version: int
    instance_version: int | None


class ConversationView(View):
    conversation_id: str


class ReplyView(View):
    conversation_id: str
    text: str
    ending: str
    #: False once ``maxTurns`` is reached, so the widget stops offering an
    #: input box rather than letting someone type into a conversation that
    #: will refuse them.
    open: bool = True
    sources: list[CitationView] = []
    quick_replies: list[str] = []


def reply_view(reply: Reply) -> ReplyView:
    """A service reply, narrowed to what a visitor may see."""
    return ReplyView(
        conversation_id=reply.conversation_id,
        text=reply.text,
        ending=reply.ending,
        open=reply.open,
        sources=[_citation(one) for one in reply.sources],
        quick_replies=list(reply.quick_replies),
    )


def _citation(source: Mapping[str, Any]) -> CitationView:
    return CitationView(
        name=str(source.get("name") or ""),
        url=source.get("url") or None,
    )


async def _live(application, workspace_id: str, instance_id: str) -> ResolvedAgent:
    """The agent a visitor is talking to, if it is talking to visitors.

    ``widgetEnabled`` is checked here rather than in each endpoint, so all
    three answer the same way. It is a deployment control like the origin
    list: unversioned and immediate, because switching the chat off is
    something a customer does *now* and not at their next publish. Without
    this check the setting would be a switch that stores a value and changes
    nothing -- and an agent kept live for email or voice while its web chat
    is off is an ordinary configuration, not an edge case.

    Reported as :class:`AgentNotLive` rather than as a new error code: from a
    visitor's side it is the same fact in the same shape -- this agent is not
    answering here, and the reason says which kind of not-answering it is.
    """
    agent = await resolve_live(
        workspace_id=workspace_id,
        instance_id=instance_id,
        templates=application.stores.templates,
        instances=application.stores.instances,
        registry=application.registry,
        cache=application.cache,
    )
    if not agent.instance.deployment.widget_enabled:
        raise AgentNotLive(
            status=str(agent.instance.status),
            reason="the widget is switched off for this agent",
        )
    return agent


@router.get("/{workspace_id}/{instance_id}")
async def agent_face(
    application: App,
    workspace_id: str,
    instance_id: str,
    origin: str = Header(default=""),
) -> AgentFaceView:
    """The agent's public face.

    Origin-checked like the rest, so a widget embedded where it should not be
    fails before it draws a launcher rather than on the visitor's first
    message.
    """
    agent = await _live(application, workspace_id, instance_id)
    application.conversations.check_origin(agent, origin)
    settings = agent.config.settings
    return AgentFaceView(
        agent_name=str(settings.get("agentName") or ""),
        greeting=str(settings.get("greeting") or ""),
        launcher=LauncherView(
            position=agent.instance.deployment.launcher_position,
            text=agent.instance.deployment.launcher_text,
        ),
        template_version=agent.template_version,
        instance_version=agent.instance_version,
    )


@router.post("/{workspace_id}/{instance_id}/conversations", status_code=201)
async def start_conversation(
    application: App,
    workspace_id: str,
    instance_id: str,
    body: StartConversation,
    origin: str = Header(default=""),
) -> ConversationView:
    """Open a conversation. No model call, so it costs nothing."""
    agent = await _live(application, workspace_id, instance_id)
    conversation = await application.conversations.start(
        agent=agent,
        channel=Channel.WEB_CHAT,
        page_url=body.page_url,
        page_title=body.page_title,
        origin=origin,
    )
    return ConversationView(conversation_id=conversation.id)


@router.post("/{workspace_id}/{instance_id}/conversations/{conversation_id}/messages")
async def say(
    application: App,
    workspace_id: str,
    instance_id: str,
    conversation_id: str,
    body: SayMessage,
    origin: str = Header(default=""),
) -> ReplyView:
    """Answer one message.

    The origin is checked here too and not only when the conversation opened:
    a list that is only enforced at the start is a list that stops applying
    the moment a conversation id is known, and conversation ids are shared by
    the page they were created on.
    """
    agent = await _live(application, workspace_id, instance_id)
    return reply_view(
        await application.conversations.say(
            agent=agent,
            conversation_id=conversation_id,
            message=body.message,
            services=application.services,
            company=application.company,
            origin=origin,
        )
    )
