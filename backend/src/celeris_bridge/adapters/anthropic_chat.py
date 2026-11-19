"""
The ``ChatModel`` port, over Anthropic's Messages API.

The only file in the project that knows what a provider's request looks like.
Everything above it speaks in the port's own content blocks, which is what makes
the turn loop testable with a scripted model and a second provider a second
file rather than a rewrite.

Three things here are not obvious and are not guesses:

* **No temperature.** Current models reject the parameter. Depth of thought is
  set with ``output_config.effort`` instead, and that is what the port carries.
* **Thinking is always on** and is not configured: the ``thinking`` parameter is
  omitted entirely, because sending ``{"type": "disabled"}`` is an error on the
  current models. Reasoning blocks come back and must go back unchanged, which
  is what ``OpaqueBlock`` carries.
* **``strict`` on every tool**, which the abilities' schemas already satisfy —
  they close ``additionalProperties`` and list ``required``. It makes the
  provider guarantee arguments that fit the schema, so ``validate_input``
  becomes a second line of defence rather than the first.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

import anthropic

from celeris_bridge.ports.chat import (
    ChatMessage,
    ChatReply,
    ContentBlock,
    OpaqueBlock,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)

__all__ = ["PROVIDER", "AnthropicChatModel"]

#: Stamped on every opaque block, so another provider's adapter cannot be
#: handed one of these and quietly send it on.
PROVIDER = "anthropic"

#: Used when a caller names no model. The current Opus.
DEFAULT_MODEL = "claude-opus-5-5"

_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})

logger = logging.getLogger(__name__)


class AnthropicChatModel:
    """One client, every agent. The model is chosen per call."""

    def __init__(
        self,
        client: anthropic.AsyncAnthropic | None = None,
        *,
        default_model: str = DEFAULT_MODEL,
    ) -> None:
        # No api_key argument: the SDK resolves ANTHROPIC_API_KEY, an auth
        # token, or a signed-in profile on its own, and a key passed through
        # this project's own configuration is a key that can end up in a log.
        self._client = client if client is not None else anthropic.AsyncAnthropic()
        self._default_model = default_model

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
        request: dict[str, Any] = {
            "model": model or self._default_model,
            "max_tokens": max_tokens,
            "messages": [_as_message(message) for message in messages],
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = [_as_tool(tool) for tool in tools]
        if effort:
            if effort not in _EFFORTS:
                raise ValueError(f'"{effort}" is not an effort level')
            request["output_config"] = {"effort": effort}

        reply = await self._client.messages.create(**request)
        return _as_reply(reply)


def _as_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": dict(tool.schema),
        # The schemas come from field_spec, which already closes
        # additionalProperties and lists required — the two things strict mode
        # needs. Without it the model is free to invent an argument, and the
        # correction costs a round trip.
        "strict": True,
    }


def _as_message(message: ChatMessage) -> dict[str, Any]:
    if isinstance(message.content, str):
        return {"role": message.role, "content": message.content}
    return {
        "role": message.role,
        "content": [_as_block(block) for block in message.content],
    }


def _as_block(block: ContentBlock) -> Any:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": dict(block.arguments),
        }
    if isinstance(block, ToolResultBlock):
        result: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
        }
        if block.is_error:
            result["is_error"] = True
        return result
    if isinstance(block, OpaqueBlock):
        if block.provider != PROVIDER:
            # Someone has routed one provider's reasoning into another's
            # request. Loud, because the alternative is a 400 with a confusing
            # message, or worse, a silently weakened turn.
            raise ValueError(
                f'This block came from "{block.provider}" and cannot be sent to '
                f'"{PROVIDER}"'
            )
        return block.payload
    raise TypeError(f"Cannot send a {type(block).__name__} to the model")


def _as_reply(reply: Any) -> ChatReply:
    blocks: list[ContentBlock] = []
    for block in reply.content:
        if block.type == "text":
            blocks.append(TextBlock(text=block.text))
        elif block.type == "tool_use":
            # The arguments are whatever the model produced; they go through the
            # ability's own validator before any handler sees them.
            blocks.append(
                ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, Mapping) else {},
                )
            )
        else:
            # Reasoning, and anything a later API version adds. Carried so the
            # next request of this turn can send it back untouched.
            blocks.append(
                OpaqueBlock(provider=PROVIDER, payload=_plain(block))
            )

    usage = getattr(reply, "usage", None)
    return ChatReply(
        content=tuple(blocks),
        model=getattr(reply, "model", "") or "",
        usage=Usage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        ),
        stop_reason=getattr(reply, "stop_reason", "") or "",
    )


def _plain(block: Any) -> Any:
    """The provider's block as plain JSON, ready to be sent back verbatim.

    ``model_dump`` rather than the object itself, because the request builder
    takes dicts and because a parsed object is harder to store if a
    conversation is ever written to a database mid-turn.
    """
    dump = getattr(block, "model_dump", None)
    return dump(exclude_none=True) if callable(dump) else block
