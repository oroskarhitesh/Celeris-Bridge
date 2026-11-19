"""
Serving a visitor's message.

The live path, end to end: resolve the agent, build the prompt, hand the last
few turns to the model, run the turn, store what happened, answer. Everything
it does is a step in that sentence and the file is deliberately short, because
this is the code that runs on every message anyone ever sends.

Four decisions are worth naming.

**The visitor's turn is stored before the model is called.** A message that
produced a timeout is still a message the visitor sent, and losing it means a
transcript that reads as though they never asked. The agent's turn is appended
after, including when the ending is ``unavailable`` -- an outage that reached a
customer is a thing to be able to count.

**Only the tail of the conversation goes to the model.** A long chat costs on
every turn and its oldest exchanges rarely matter. Where to cut comes from the
definition, not from here.

**``maxTurns`` is enforced on the way in, not by the prompt.** A setting that
asks the model to stop after eight turns is a suggestion; a check before the
call is a limit. Past it the agent hands off instead of answering.

**A conversation records the versions it started under.** A transcript read six
months later should be read against the configuration that produced it, and
the agent will have been published a dozen times by then.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence

from celeris_bridge.domain.agent.engine.instructions import build_prompt
from celeris_bridge.domain.agent.engine.runner import TurnContext
from celeris_bridge.domain.agent.engine.turn import Ending, TurnResult, run_turn
from celeris_bridge.domain.conversation import Channel, Conversation, Speaker, Turn
from celeris_bridge.ports.chat import ChatMessage, ChatModel
from celeris_bridge.ports.store import ConversationStore
from celeris_bridge.ports.visitor import ConversationRef, VisitorRecord
from celeris_bridge.services.resolution import ResolvedAgent

__all__ = [
    "ConversationService",
    "ConversationTooLong",
    "OriginNotAllowed",
    "Reply",
]

logger = logging.getLogger(__name__)

#: How many past turns the model is shown. Six exchanges is enough to follow a
#: thread and short enough that a long conversation does not grow the bill on
#: every message.
DEFAULT_HISTORY_TURNS = 12


class ConversationTooLong(RuntimeError):
    """The conversation hit the definition's ``maxTurns``.

    Not an error so much as the end of the conversation: the caller shows the
    hand-off message rather than a failure. Enforced here because the prompt
    cannot enforce anything.
    """

    def __init__(self, limit: int, message: str) -> None:
        self.limit = limit
        self.visitor_message = message
        super().__init__(f"This conversation reached its limit of {limit} turns")


class OriginNotAllowed(PermissionError):
    """The widget asked from a domain this agent is not deployed on.

    Checked against the *stored* deployment and never against a header the
    page controls: the whole point of the list is that a page cannot add
    itself to it.
    """

    def __init__(self, origin: str) -> None:
        self.origin = origin
        super().__init__(f"{origin or 'This page'} is not allowed to use this agent")


@dataclass(frozen=True)
class Reply:
    """What the widget is sent back."""

    conversation_id: str
    text: str
    ending: str
    sources: tuple[Mapping[str, object], ...] = ()
    quick_replies: tuple[str, ...] = ()
    #: Whether the agent will take another message. False once ``maxTurns``
    #: is reached, so the widget stops offering an input box rather than
    #: letting someone type into a conversation that will refuse them.
    open: bool = True


class ConversationService:
    """One visitor message in, one reply out."""

    def __init__(
        self,
        *,
        conversations: ConversationStore,
        chat: ChatModel,
        visitors,
        history_turns: int = DEFAULT_HISTORY_TURNS,
    ) -> None:
        self._conversations = conversations
        self._chat = chat
        self._visitors = visitors
        self._history_turns = history_turns

    async def start(
        self,
        *,
        agent: ResolvedAgent,
        channel: Channel = Channel.WEB_CHAT,
        page_url: str = "",
        page_title: str = "",
        origin: str = "",
    ) -> Conversation:
        """Open a conversation against a resolved agent."""
        self.check_origin(agent, origin)
        return await self._conversations.start(
            Conversation(
                id="",
                workspace_id=agent.workspace_id,
                instance_id=agent.instance.id,
                channel=channel,
                template_version=agent.template_version,
                instance_version=agent.instance_version,
                page_url=page_url,
                page_title=page_title,
                started_at=_now(),
            )
        )

    async def say(
        self,
        *,
        agent: ResolvedAgent,
        conversation_id: str,
        message: str,
        services,
        company: str,
        origin: str = "",
    ) -> Reply:
        """
        Answer one message.

        ``services`` is the ability-facing bundle -- the index, the embedder,
        the calendar, the visitor records. It is passed in rather than held
        because one process serves every workspace and nothing here should be
        able to hold a connection scoped to one of them.

        ``company`` is what the agent calls the company it works for. An
        argument and not a constant, because a constant would have every
        workspace's agent introduce itself as the example company; it will
        come from the workspace record once there is one.
        """
        self.check_origin(agent, origin)
        conversation = await self._conversations.get(agent.workspace_id, conversation_id)
        if conversation is None:
            raise LookupError(
                f"No conversation {conversation_id!r} in workspace {agent.workspace_id!r}"
            )

        text = (message or "").strip()
        if not text:
            raise ValueError("An empty message is not a message")

        limit = int(agent.config.settings.get("maxTurns") or 0)
        if limit and conversation.visitor_turns >= limit:
            # Stored anyway: they said it, and a transcript that drops the
            # last thing a visitor typed is the one someone will ask about.
            await self._conversations.append(
                agent.workspace_id,
                conversation_id,
                Turn(speaker=Speaker.VISITOR, text=text, at=_now(), ending="too_long"),
            )
            raise ConversationTooLong(limit, text)

        # Before the model, so a timeout does not lose the question.
        await self._conversations.append(
            agent.workspace_id,
            conversation_id,
            Turn(speaker=Speaker.VISITOR, text=text, at=_now()),
        )

        visitor = await self._visitors.load(
            ConversationRef(agent.workspace_id, conversation_id)
        ) or VisitorRecord()

        prompt = build_prompt(
            definition=agent.definition,
            settings=agent.config.settings,
            locked_rules=agent.config.locked_rules,
            company=company,
            abilities=agent.briefs,
            visitor=visitor,
        )

        result = await run_turn(
            config=agent.config,
            registry=agent.registry,
            chat=self._chat,
            system=prompt.text,
            company=company,
            message=text,
            context=TurnContext(
                workspace_id=agent.workspace_id,
                agent_instance_id=agent.instance.id,
                conversation_id=conversation_id,
                services=services,
            ),
            history=self._history(conversation),
        )

        await self._conversations.append(
            agent.workspace_id,
            conversation_id,
            Turn(
                speaker=Speaker.AGENT,
                text=result.reply,
                at=_now(),
                calls=tuple(_call_record(call) for call in result.calls),
                sources=result.sources,
                ending=str(result.ending),
                model=result.model,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            ),
        )

        if result.ending is Ending.UNAVAILABLE:
            # Logged here and not only inside the turn loop, because this is
            # the layer that knows which workspace and which conversation a
            # customer-visible outage happened in.
            logger.warning(
                "Agent %s/%s could not answer in conversation %s",
                agent.workspace_id,
                agent.instance.id,
                conversation_id,
            )

        return Reply(
            conversation_id=conversation_id,
            text=result.reply,
            ending=str(result.ending),
            sources=result.sources,
            quick_replies=result.quick_replies,
            # Computed from the turn just stored, so the widget knows the
            # conversation is finished *with* the last reply rather than on
            # the next message being refused.
            open=not limit or (conversation.visitor_turns + 1) < limit,
        )

    async def transcript(self, workspace_id: str, conversation_id: str) -> Conversation:
        conversation = await self._conversations.get(workspace_id, conversation_id)
        if conversation is None:
            raise LookupError(f"No conversation {conversation_id!r}")
        return conversation

    async def recent(
        self,
        workspace_id: str,
        *,
        instance_id: str | None = None,
        channel: Channel | None = None,
        limit: int = 50,
    ) -> Sequence[Conversation]:
        return await self._conversations.recent(
            workspace_id, instance_id=instance_id, channel=channel, limit=limit
        )

    # -- internals -------------------------------------------------------

    def _history(self, conversation: Conversation) -> list[ChatMessage]:
        """The last few turns, as the chat port wants them.

        A narrowing, and the reason the stored turn and the chat message are
        two types: a stored turn carries the ability calls, the citations and
        what the turn cost, and none of that goes back to the model.
        """
        return [
            ChatMessage(
                role="user" if turn.speaker is Speaker.VISITOR else "assistant",
                content=turn.text,
            )
            for turn in conversation.tail(self._history_turns)
            if turn.text
        ]

    def check_origin(self, agent: ResolvedAgent, origin: str) -> None:
        """Whether this page may use this agent.

        Public because the widget asks the same question before it draws a
        launcher: a check that only runs on the first message lets an agent
        render on a site it was taken off.
        """
        allowed = agent.instance.deployment.allowed_origins
        if not allowed:
            # Nothing configured means not yet restricted. Deliberate: a
            # workspace that has not set up its domains should be able to try
            # the agent, and the list is a deployment control rather than
            # authentication.
            return
        if origin not in allowed:
            raise OriginNotAllowed(origin)


def _call_record(call) -> Mapping[str, object]:
    """One ability call, as the transcript stores it.

    Flattened to plain data here rather than stored as the runner's object:
    the transcript is read months later by a person, and a record that is a
    pickled class is a record that stops loading when the class moves.
    """
    return {
        "key": call.key,
        "ok": call.ok,
        "error": str(call.error or ""),
        "milliseconds": call.milliseconds,
        "replayed": call.replayed,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)
