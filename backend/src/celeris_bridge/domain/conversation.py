"""
A conversation, as it is stored.

Separate from the chat port's ``ChatMessage``, on purpose. That type is what a
provider is sent; this is what happened. They look alike today and diverge the
moment anything is stored that a model should not be re-sent — which turn was
blocked, what an ability was asked, how long a reply took.

Turning one into the other is the application service's job, and it is a
narrowing: a stored turn carries more than the model ever sees.

This module is pure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Sequence

__all__ = ["Channel", "Conversation", "Speaker", "Turn"]


class Channel(StrEnum):
    """Where the conversation is happening. Mirrors a definition's channels."""

    WEB_CHAT = "web_chat"
    EMAIL = "email"
    VOICE = "voice"
    WHATSAPP = "whatsapp"


class Speaker(StrEnum):
    VISITOR = "visitor"
    AGENT = "agent"


@dataclass(frozen=True)
class Turn:
    """One thing that was said, and what it cost to say it."""

    speaker: Speaker
    text: str
    at: datetime | None = None
    #: Which abilities ran, as the runner recorded them. Kept for the
    #: transcript a person reads when an agent said something odd — the single
    #: most useful thing to have and the easiest to forget to store.
    calls: tuple[Mapping[str, Any], ...] = ()
    #: Citations shown under the reply.
    sources: tuple[Mapping[str, Any], ...] = ()
    #: Why the turn ended, from ``Ending``. ``unavailable`` here is an outage
    #: that reached a customer, and that is worth being able to count.
    ending: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Conversation:
    """One visitor's exchange with one agent."""

    id: str
    workspace_id: str
    instance_id: str
    channel: Channel = Channel.WEB_CHAT
    turns: tuple[Turn, ...] = ()
    #: The template and instance versions that were live when it started, so a
    #: transcript can be read against the configuration that produced it rather
    #: than against today's.
    template_version: int | None = None
    instance_version: int | None = None
    #: Where the visitor was when they opened the chat.
    page_url: str = ""
    page_title: str = ""
    started_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def visitor_turns(self) -> int:
        return sum(1 for turn in self.turns if turn.speaker is Speaker.VISITOR)

    @property
    def tokens(self) -> tuple[int, int]:
        """Total in and out across the conversation."""
        return (
            sum(turn.input_tokens for turn in self.turns),
            sum(turn.output_tokens for turn in self.turns),
        )

    def tail(self, turns: int) -> Sequence[Turn]:
        """The last few turns, for the history a model is given.

        A long conversation does not go to the model whole: it costs on every
        turn and the oldest exchanges rarely matter. Where to cut is the
        caller's decision, which is why this takes a number.
        """
        return self.turns[-turns:] if turns > 0 else ()
