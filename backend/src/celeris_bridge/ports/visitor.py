"""
Port for what is known about the person on the other end of a conversation.

Only what the agent needs: read what has been gathered so far, write it back
once more has been learned. Whether a leads list, a CRM export or a dashboard
is built on top of it is somebody else's concern — this port stores the record
and says nothing about who reads it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

__all__ = ["ConversationRef", "VisitorRecord", "VisitorRecords"]


@dataclass(frozen=True)
class ConversationRef:
    """Which conversation, in which workspace.

    Both come from the runner, which has them from the authenticated principal
    and the session it is serving. A model that names a conversation id in a
    tool call is naming an argument that does not exist.
    """

    workspace_id: str
    conversation_id: str


@dataclass(frozen=True)
class VisitorRecord:
    """Everything gathered about one visitor.

    ``details`` are facts the visitor gave. The rest is derived — recomputed
    from the agent's settings on every save — and stored anyway, so that a
    leads list can filter on ``status`` without re-running the rules over every
    conversation it displays.
    """

    details: Mapping[str, str] = field(default_factory=dict)
    visitor_type: str = "unknown"
    status: str = "exploring"
    #: Keys of the required details still missing.
    missing: tuple[str, ...] = ()
    #: Why this visitor is not a fit, if they are not. Plain sentences.
    reasons: tuple[str, ...] = ()


@runtime_checkable
class VisitorRecords(Protocol):
    """Stores what is known about visitors, one record per conversation."""

    async def load(self, ref: ConversationRef) -> VisitorRecord | None:
        """What is known so far, or ``None`` for a conversation never written to.

        ``None`` and an empty record are deliberately different: the first means
        this visitor has shared nothing, the second that something was saved and
        it came to nothing.
        """
        ...

    async def save(self, ref: ConversationRef, record: VisitorRecord) -> None:
        """Replace the record for this conversation.

        A whole-record write rather than a patch, because the derived fields are
        only meaningful alongside the details they were computed from. The
        runner serialises an ability's calls within a turn, so the read-modify-
        write in the handler is not racing itself.
        """
        ...
