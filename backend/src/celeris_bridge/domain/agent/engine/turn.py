"""
One visitor message in, one reply out.

Generic: it runs any definition's agent, because everything specific to an
agent type is in the prompt and the abilities.

    1. search the knowledge first, unprompted, so most answers need one model call
    2. model ⇄ abilities, up to a few rounds
    3. attribute the citations, and fall back to something honest if the model
       produced nothing

What the model decides: what to say and which ability to reach for. What this
decides: what is allowed to run, how many times, and what the visitor actually
sees. The model is never the thing enforcing a limit.

This module is pure. The model and every ability arrive through ports.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence

from celeris_bridge.domain.agent.abilities.registry import AbilityRegistry
from celeris_bridge.domain.agent.engine.runner import (
    AbilityCall,
    AbilityOutcome,
    AbilityTool,
    Turn,
    TurnContext,
    enabled_tools,
    run_ability,
)
from celeris_bridge.domain.agent.engine.used_sources import sources_used_by
from celeris_bridge.domain.agent.overrides import ResolvedConfig
from celeris_bridge.ports.chat import (
    ChatMessage,
    ChatModel,
    ChatReply,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_ROUNDS",
    "Ending",
    "TurnResult",
    "run_turn",
    "search_query_for",
]

#: Enough for: search, one correction, one follow-up ability, one answer.
DEFAULT_MAX_ROUNDS = 4
#: More citations than this under one reply is wallpaper.
DEFAULT_MAX_SOURCES = 3
SEARCH = "search_knowledge"
_PRESEARCH_ID = "presearch"
#: Below this many words a message is treated as a follow-up that needs the
#: previous question attached to be searchable on its own.
_FOLLOW_UP_WORDS = 12
_MAX_SEARCH_QUERY = 300


class Ending(StrEnum):
    """Why the turn stopped. Worth recording: each one is a different bug."""

    #: The model answered and asked for nothing more.
    ANSWERED = "answered"
    #: An ability produced the final answer itself.
    DECORATED = "decorated"
    #: It kept calling abilities until the rounds ran out.
    ROUNDS = "rounds"
    #: It said nothing at all.
    EMPTY = "empty"
    #: The model could not be reached.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class TurnResult:
    """Everything one message produced."""

    reply: str
    ending: Ending
    sources: tuple[Mapping[str, Any], ...] = ()
    quick_replies: tuple[str, ...] = ()
    calls: tuple[AbilityCall, ...] = ()
    rounds: int = 0
    usage: Usage = field(default_factory=Usage)
    model: str = ""


def fallback_reply(company: str) -> str:
    return (
        "Sorry — I could not find a good answer to that. Could you rephrase it, or "
        f"would you like the {company} team to take a look?"
    )


def unavailable_reply(company: str) -> str:
    return (
        "Sorry — I am having trouble answering just now. Please try again in a "
        f"moment, or ask the {company} team."
    )


def search_query_for(message: str, history: Sequence[ChatMessage] = ()) -> str:
    """A query that stands on its own.

    "and the Pro plan?" retrieves nothing by itself. Attaching the previous
    question is a cheap fix that works because the model is also told to write
    standalone queries when it searches deliberately — this is only for the
    unprompted first search.
    """
    text = message.strip()
    if len(text.split()) > _FOLLOW_UP_WORDS:
        return text[:_MAX_SEARCH_QUERY]
    previous = next(
        (
            message_.content
            for message_ in reversed(history)
            if message_.role == "user" and isinstance(message_.content, str)
        ),
        None,
    )
    if not previous:
        return text[:_MAX_SEARCH_QUERY]
    return f"{previous.strip()}\n{text}"[:_MAX_SEARCH_QUERY]


def _tool_result(identifier: str, outcome: AbilityOutcome) -> ToolResultBlock:
    """What the model is told about a call.

    A failure carries its per-argument problems, which is what lets a model
    correct a bad call rather than repeat it.
    """
    if outcome.ok:
        return ToolResultBlock(
            tool_use_id=identifier, content=json.dumps(outcome.output, default=str)
        )
    assert outcome.error is not None
    body: dict[str, Any] = {"error": outcome.error.message}
    if outcome.error.details:
        body["problems"] = [dict(detail) for detail in outcome.error.details]
    return ToolResultBlock(
        tool_use_id=identifier, content=json.dumps(body, default=str), is_error=True
    )


class _Gathered:
    """What the abilities produced over a turn, as the reply is assembled."""

    def __init__(self) -> None:
        self.sources: dict[str, Mapping[str, Any]] = {}
        self.quick_replies: tuple[str, ...] = ()
        self.searched_and_found = False

    def add(self, outcome: AbilityOutcome) -> None:
        if not outcome.ok:
            return
        for source in outcome.sources:
            identifier = str(source.get("sourceId") or source.get("name") or "")
            kept = self.sources.get(identifier)
            if kept is None or float(source.get("score") or 0) > float(
                kept.get("score") or 0
            ):
                self.sources[identifier] = source
        if outcome.key == SEARCH and outcome.output.get("found"):
            self.searched_and_found = True
        if outcome.display and outcome.display.get("quickReplies"):
            self.quick_replies = tuple(outcome.display["quickReplies"])


async def run_turn(
    *,
    config: ResolvedConfig,
    registry: AbilityRegistry,
    chat: ChatModel,
    system: str,
    company: str,
    message: str,
    context: TurnContext,
    history: Sequence[ChatMessage] = (),
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_sources: int = DEFAULT_MAX_SOURCES,
    max_tokens: int = 700,
    search_first: bool = True,
) -> TurnResult:
    """Run one turn. Raises only if a port does something a port may not."""
    turn = Turn()
    tools = enabled_tools(config, registry)
    specs = [
        ToolSpec(name=tool.name, description=tool.description, schema=tool.schema)
        for tool in tools
    ]
    ends_turn = {tool.name for tool in tools if tool.ends_turn}
    gathered = _Gathered()

    # Admin-only settings, so a workspace cannot quietly move its agent onto a
    # more expensive model or make it think harder than the plan pays for.
    model = str(config.settings.get("chatModel") or "")
    effort = str(config.settings.get("replyEffort") or "")

    exchange: list[ChatMessage] = [*history, ChatMessage(role="user", content=message)]

    if search_first and any(tool.name == SEARCH for tool in tools):
        # Unprompted, because an agent that answers from memory and only
        # searches when it feels uncertain is wrong exactly when it matters.
        query = search_query_for(message, history)
        outcome = await run_ability(
            key=SEARCH,
            arguments={"query": query},
            config=config,
            registry=registry,
            context=context,
            turn=turn,
        )
        gathered.add(outcome)
        # Written into the exchange as a tool call and its result, because
        # that is the only shape a tool-calling model reads a result in — and
        # it has to see the search already happened, or it searches again for
        # the same thing.
        exchange.append(
            ChatMessage(
                role="assistant",
                content=(
                    ToolUseBlock(
                        id=_PRESEARCH_ID, name=SEARCH, arguments={"query": query}
                    ),
                ),
            )
        )
        exchange.append(
            ChatMessage(role="user", content=(_tool_result(_PRESEARCH_ID, outcome),))
        )

    said: list[str] = []
    ending = Ending.ROUNDS
    usage = Usage()
    model_used = ""
    rounds = 0

    while rounds < max_rounds:
        rounds += 1
        try:
            reply: ChatReply = await chat.chat(
                system=system,
                messages=exchange,
                tools=specs,
                model=model,
                max_tokens=max_tokens,
                effort=effort,
            )
        except Exception:
            # One provider outage must not look like an agent with nothing to
            # say: the visitor gets something human. Logged here rather than
            # left to a caller, because an unreachable model and a missing API
            # key look identical from the outside, and silence makes the second
            # one very hard to find.
            logger.exception(
                "The model could not be reached for workspace %s",
                context.workspace_id,
            )
            ending = Ending.UNAVAILABLE
            said = []
            break

        usage = usage + reply.usage
        model_used = reply.model or model_used
        text = reply.text.strip()
        if text:
            said.append(text)

        uses = reply.tool_uses
        if not uses:
            ending = Ending.ANSWERED
            break

        results: list[ToolResultBlock] = []
        for use in uses:
            outcome = await run_ability(
                key=use.name,
                arguments=use.arguments,
                config=config,
                registry=registry,
                context=context,
                turn=turn,
            )
            gathered.add(outcome)
            results.append(_tool_result(use.id, outcome))

        # An ability like suggest_replies only decorates an answer that is
        # already written. Going round again would have the model write it
        # twice.
        if text and all(use.name in ends_turn for use in uses):
            ending = Ending.DECORATED
            break

        exchange.append(ChatMessage(role="assistant", content=reply.content))
        exchange.append(ChatMessage(role="user", content=tuple(results)))

    reply_text = " ".join(said).strip()
    if not reply_text:
        reply_text = (
            unavailable_reply(company)
            if ending is Ending.UNAVAILABLE
            else fallback_reply(company)
        )
        if ending is not Ending.UNAVAILABLE:
            ending = Ending.EMPTY

    sources: tuple[Mapping[str, Any], ...] = ()
    if gathered.searched_and_found and ending not in (Ending.EMPTY, Ending.UNAVAILABLE):
        sources = sources_used_by(
            reply_text, gathered.sources.values(), limit=max_sources
        )

    return TurnResult(
        reply=reply_text,
        ending=ending,
        sources=sources,
        quick_replies=() if ending in (Ending.EMPTY, Ending.UNAVAILABLE) else gathered.quick_replies,
        calls=tuple(turn.calls),
        rounds=rounds,
        usage=usage,
        model=model_used,
    )
