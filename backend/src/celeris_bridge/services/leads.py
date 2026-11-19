"""
The leads list: who the agent has spoken to and what came of it.

A read model, built from two records that already exist -- the conversation
and the visitor record kept against it. Nothing here writes anything, and
there is no third record for a "lead": a lead *is* a conversation that
produced some details, and a separate table would be a copy that drifts from
the transcript it claims to summarise.

The verdict is **not** recomputed here. ``save_visitor_details`` decides it,
against the agent's settings, at the moment the visitor said something -- and
that is the answer a list has to show. Recomputing it now would mean a
qualification rule changed last week silently rewriting history, and a lead
that was called qualified on Tuesday turning into something else on Friday
with nothing to explain it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from celeris_bridge.domain.conversation import Channel, Conversation, Speaker
from celeris_bridge.ports.store import ConversationStore
from celeris_bridge.ports.visitor import ConversationRef, VisitorRecord, VisitorRecords

__all__ = ["Lead", "LeadsService"]


@dataclass(frozen=True)
class Lead:
    """One conversation, as a leads list shows it."""

    conversation_id: str
    instance_id: str
    channel: str
    #: What the visitor gave, under the keys the workspace asked for. Not
    #: flattened into name/email columns: which details are collected is a
    #: setting, so a fixed shape here would be wrong for anyone who changed it.
    details: Mapping[str, str]
    visitor_type: str
    status: str
    #: Required details still missing, so a list can show what to follow up on.
    missing: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    turns: int = 0
    started_at: datetime | None = None
    last_at: datetime | None = None
    page_url: str = ""
    #: The first thing the visitor said. The single most useful column: it is
    #: why they got in touch, in their own words.
    opening_message: str = ""

    @property
    def has_details(self) -> bool:
        return bool(self.details)

    @property
    def complete(self) -> bool:
        """Whether everything the workspace asks for was collected."""
        return self.has_details and not self.missing


class LeadsService:
    """Conversations and what was learned in them."""

    def __init__(
        self,
        *,
        conversations: ConversationStore,
        visitors: VisitorRecords,
    ) -> None:
        self._conversations = conversations
        self._visitors = visitors

    async def list(
        self,
        workspace_id: str,
        *,
        instance_id: str | None = None,
        channel: Channel | None = None,
        status: str | None = None,
        with_details_only: bool = False,
        limit: int = 50,
    ) -> Sequence[Lead]:
        """
        Recent conversations as leads, newest first.

        ``with_details_only`` is the view most people want: a workspace with a
        public widget has far more conversations than leads, and a list that
        shows every passer-by who said "hi" buries the ones worth calling.
        Filtering is done here rather than in the store because the details
        live in the other record.
        """
        conversations = await self._conversations.recent(
            workspace_id, instance_id=instance_id, channel=channel, limit=limit
        )
        leads: list[Lead] = []
        for conversation in conversations:
            record = await self._visitors.load(
                ConversationRef(workspace_id, conversation.id)
            )
            lead = _lead(conversation, record)
            if with_details_only and not lead.has_details:
                continue
            if status is not None and lead.status != status:
                continue
            leads.append(lead)
        return tuple(leads)

    async def get(self, workspace_id: str, conversation_id: str) -> Lead:
        conversation = await self._conversations.get(workspace_id, conversation_id)
        if conversation is None:
            raise LookupError(f"No conversation {conversation_id!r} in this workspace")
        record = await self._visitors.load(
            ConversationRef(workspace_id, conversation_id)
        )
        return _lead(conversation, record)


def _lead(conversation: Conversation, record: VisitorRecord | None) -> Lead:
    """One conversation and its visitor record, joined.

    ``record`` is ``None`` for a conversation nobody shared anything in, which
    is most of them. Treated as an empty record rather than skipped, because
    "forty conversations, three leads" is the number a workspace needs to see.
    """
    said = [turn for turn in conversation.turns if turn.speaker is Speaker.VISITOR]
    record = record or VisitorRecord()
    return Lead(
        conversation_id=conversation.id,
        instance_id=conversation.instance_id,
        channel=str(conversation.channel),
        details=dict(record.details),
        visitor_type=record.visitor_type,
        status=record.status,
        missing=record.missing,
        reasons=record.reasons,
        turns=len(said),
        started_at=conversation.started_at,
        last_at=conversation.updated_at or conversation.started_at,
        page_url=conversation.page_url,
        opening_message=said[0].text if said else "",
    )
