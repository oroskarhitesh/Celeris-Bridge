"""
Transcripts, for the people who own the agent.

The admin side of what the widget produced, and the difference in what it may
see is the whole reason it is a separate router. A visitor's reply carries a
citation's name and link; the transcript carries the passage text, the scores,
which abilities ran, how long each took and what the turn cost. All of that is
needed to answer "why did it say that", and none of it belongs in a public
response.

A conversation with no instance version is a preview: it was had against a
draft from the admin screen rather than by a visitor. It is flagged rather
than hidden, because a filtered-out conversation is one somebody will go
looking for.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from fastapi import APIRouter, Depends, Query

from celeris_bridge.api.dependencies import App, Workspace, require_workspace
from celeris_bridge.api.models import View
from celeris_bridge.domain.conversation import Channel, Conversation, Turn

router = APIRouter(
    prefix="/v1/conversations",
    tags=["conversations"],
    dependencies=[Depends(require_workspace)],
)


class TurnView(View):
    speaker: str
    text: str
    at: datetime | None
    #: Why the turn ended. ``unavailable`` here is an outage that reached a
    #: customer, which is the thing worth being able to count.
    ending: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    #: Which abilities ran, as the runner recorded them -- the single most
    #: useful thing to have when an agent said something odd.
    calls: list[Mapping[str, Any]] = []
    sources: list[Mapping[str, Any]] = []


class ConversationSummaryView(View):
    id: str
    instance_id: str
    channel: str
    turns: int
    visitor_turns: int
    started_at: datetime | None
    updated_at: datetime | None
    page_url: str
    page_title: str
    template_version: int | None
    instance_version: int | None
    input_tokens: int
    output_tokens: int
    #: Had against a draft, from the admin preview, rather than by a visitor.
    preview: bool


class TranscriptView(ConversationSummaryView):
    turns_detail: list[TurnView]


def _turn(turn: Turn) -> TurnView:
    return TurnView(
        speaker=str(turn.speaker),
        text=turn.text,
        at=turn.at,
        ending=turn.ending,
        model=turn.model,
        input_tokens=turn.input_tokens,
        output_tokens=turn.output_tokens,
        calls=[dict(one) for one in turn.calls],
        sources=[dict(one) for one in turn.sources],
    )


def _summary(conversation: Conversation) -> ConversationSummaryView:
    tokens_in, tokens_out = conversation.tokens
    return ConversationSummaryView(
        id=conversation.id,
        instance_id=conversation.instance_id,
        channel=str(conversation.channel),
        turns=len(conversation.turns),
        visitor_turns=conversation.visitor_turns,
        started_at=conversation.started_at,
        updated_at=conversation.updated_at,
        page_url=conversation.page_url,
        page_title=conversation.page_title,
        template_version=conversation.template_version,
        instance_version=conversation.instance_version,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        preview=conversation.instance_version is None,
    )


@router.get("")
async def recent(
    application: App,
    workspace_id: Workspace,
    instance_id: str | None = Query(default=None, alias="instanceId"),
    channel: Channel | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ConversationSummaryView]:
    return [
        _summary(one)
        for one in await application.conversations.recent(
            workspace_id, instance_id=instance_id, channel=channel, limit=limit
        )
    ]


@router.get("/{conversation_id}")
async def transcript(
    application: App, workspace_id: Workspace, conversation_id: str
) -> TranscriptView:
    conversation = await application.conversations.transcript(
        workspace_id, conversation_id
    )
    return TranscriptView(
        **_summary(conversation).model_dump(),
        turns_detail=[_turn(one) for one in conversation.turns],
    )
