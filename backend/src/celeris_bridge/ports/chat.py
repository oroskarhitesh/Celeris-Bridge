"""
Port for a model that can hold a conversation and call tools.

Shaped after what a tool-calling API actually exchanges — content blocks, not
strings — because flattening to text is what makes a provider adapter lossy and
a turn loop guesswork. The block types are ours, not any provider's: an adapter
translates, so a second provider is a second adapter and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable

__all__ = [
    "ChatMessage",
    "ChatModel",
    "ChatReply",
    "ContentBlock",
    "OpaqueBlock",
    "TextBlock",
    "ToolResultBlock",
    "ToolSpec",
    "ToolUseBlock",
    "Usage",
]


@dataclass(frozen=True)
class TextBlock:
    """Something the model said."""

    text: str


@dataclass(frozen=True)
class ToolUseBlock:
    """The model asking for an ability to run.

    ``arguments`` is whatever the model produced. It is not to be trusted: the
    runner puts it through the ability's validator before a handler sees it.
    """

    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultBlock:
    """What came of that, on its way back to the model."""

    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class OpaqueBlock:
    """A block this domain does not model, kept exactly as the provider sent it.

    Reasoning blocks are the reason this exists. Current models think on every
    turn, and a thinking block has to go back to the provider unchanged on the
    next request of the same turn: the model loses its own reasoning otherwise,
    and a provider that checks for edited history can reject the request
    outright. Modelling reasoning properly here would mean inventing a shape
    for something the domain never reads, so it is carried instead.

    Never inspected, never logged, never shown to anyone. ``provider`` exists
    so an adapter cannot be handed another provider's block.
    """

    provider: str
    payload: Any


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | OpaqueBlock


@dataclass(frozen=True)
class ChatMessage:
    """One message in the exchange."""

    role: Literal["user", "assistant"]
    content: str | tuple[ContentBlock, ...]


@dataclass(frozen=True)
class ToolSpec:
    """A tool as the model is told about it."""

    name: str
    description: str
    schema: Mapping[str, Any]


@dataclass(frozen=True)
class Usage:
    """What the call cost, for billing and for noticing a prompt getting fat."""

    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True)
class ChatReply:
    """One response from the model."""

    content: tuple[ContentBlock, ...] = ()
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    #: Why the model stopped. ``"max_tokens"`` means the reply was cut off.
    stop_reason: str = ""

    @property
    def text(self) -> str:
        return "".join(
            block.text for block in self.content if isinstance(block, TextBlock)
        )

    @property
    def tool_uses(self) -> tuple[ToolUseBlock, ...]:
        return tuple(
            block for block in self.content if isinstance(block, ToolUseBlock)
        )


@runtime_checkable
class ChatModel(Protocol):
    """Sends an exchange to a model and gets its next message."""

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
        """
        One round trip.

        ``model`` is per call rather than per adapter: which model an agent uses
        is a setting a platform admin changes, and one client serves all of them.
        An empty string means the adapter's own default.

        ``effort`` is how hard to think, not how random to be. There is no
        temperature here on purpose: current models reject the parameter
        outright, and the knob that replaced it is this one. An empty string
        means the model's own default.

        Raising is allowed and expected — a provider outage is not something
        this port can paper over. The turn loop decides what the visitor sees.
        """
        ...
