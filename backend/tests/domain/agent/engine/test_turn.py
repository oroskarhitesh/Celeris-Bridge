"""
Tests for one turn, with a scripted model.

The fake model is given a list of replies and hands them out in order, so a
test reads as "the model does this, then this" and the assertions are about
what the turn loop did in response.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping, Sequence

import pytest

from celeris_bridge.domain.agent.abilities import (
    Ability,
    AbilityContext,
    AbilityRegistry,
    AbilityResult,
)
from celeris_bridge.domain.agent.definition_schema import validate_definition
from celeris_bridge.domain.agent.engine.runner import AbilityErrorCode, TurnContext
from celeris_bridge.domain.agent.engine.turn import (
    Ending,
    run_turn,
    search_query_for,
)
from celeris_bridge.domain.agent.overrides import resolve_config
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

CONTEXT = TurnContext(workspace_id="w1", agent_instance_id="a1", conversation_id="c1")


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class ScriptedModel:
    """Hands out prepared replies, recording what it was asked."""

    def __init__(self, *replies: ChatReply, raises: Exception | None = None) -> None:
        self.replies = list(replies)
        self.raises = raises
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
                "max_tokens": max_tokens,
            }
        )
        if self.raises is not None:
            raise self.raises
        if not self.replies:
            return ChatReply(content=(TextBlock(text="(nothing left to say)"),))
        return self.replies.pop(0)


def says(text: str, *, usage: Usage | None = None) -> ChatReply:
    return ChatReply(
        content=(TextBlock(text=text),),
        model="fake-model-1",
        usage=usage or Usage(input_tokens=10, output_tokens=5),
    )


def calls(name: str, arguments: Mapping[str, Any], *, text: str = "", identifier: str = "u1") -> ChatReply:
    content: tuple[Any, ...] = ()
    if text:
        content += (TextBlock(text=text),)
    content += (ToolUseBlock(id=identifier, name=name, arguments=arguments),)
    return ChatReply(content=content, model="fake-model-1", usage=Usage(input_tokens=10, output_tokens=5))


def searcher(found: bool = True, *, source: str = "pricing.pdf", text: str = "Plans start at 29 per seat.") -> Ability:
    async def handler(arguments: Mapping[str, Any], context: AbilityContext) -> AbilityResult:
        if not found:
            return AbilityResult(output={"found": False, "passages": []})
        return AbilityResult(
            output={"found": True, "passages": [{"source": source, "text": text}]},
            sources=({"sourceId": "s1", "name": source, "score": 0.4, "text": text},),
        )

    return Ability(
        key="search_knowledge",
        version="1.0.0",
        label="Search company knowledge",
        description="Searches.",
        ai_description="Search the company knowledge.",
        instructions="Search before answering.",
        input={"query": {"type": "string", "description": "A query.", "required": True}},
        effect="read",
        handler=handler,
    )


def suggester() -> Ability:
    async def handler(arguments: Mapping[str, Any], context: AbilityContext) -> AbilityResult:
        return AbilityResult(
            output={"ok": True},
            display={"quickReplies": list(arguments.get("replies", []))},
        )

    return Ability(
        key="suggest_replies",
        version="1.0.0",
        label="Suggest replies",
        description="Suggests follow-ups.",
        ai_description="Offer tappable follow-ups.",
        input={
            "replies": {
                "type": "stringList",
                "description": "Two or three short follow-ups.",
                "required": True,
            }
        },
        effect="read",
        ends_turn=True,
        handler=handler,
    )


def agent(*abilities: Ability, settings: Mapping[str, Any] | None = None):
    registry = AbilityRegistry(abilities)
    raw: dict[str, Any] = {
        "identity": {"name": "Test", "channels": ["web_chat"]},
        "settings": {
            "chatModel": {
                "label": "Model",
                "type": "enum",
                "options": ["claude-opus-5-5", "claude-sonnet-5-5"],
                "default": "claude-opus-5-5",
                "adminOnly": True,
            },
            "replyEffort": {
                "label": "Thinking effort",
                "type": "enum",
                "options": ["low", "medium", "high"],
                "default": "low",
                "adminOnly": True,
            },
            **(settings or {}),
        },
        "abilities": [{"key": ability.key, "enabledByDefault": True} for ability in abilities],
    }
    report = validate_definition(raw, abilities=registry)
    assert report.definition is not None, [p.message for p in report.problems]
    return resolve_config(report.definition, None, abilities=registry), registry


def take_turn(model: ScriptedModel, *abilities: Ability, message: str = "how much is it?", **changed: Any):
    config, registry = agent(*abilities)
    return run(
        run_turn(
            config=config,
            registry=registry,
            chat=model,
            system="You are a helpful agent.",
            company="Acme",
            message=message,
            context=CONTEXT,
            **changed,
        )
    )


class TestTheSimplestTurn:
    def test_the_model_answers_and_that_is_the_reply(self) -> None:
        result = take_turn(ScriptedModel(says("Plans start at 29 per seat.")))
        assert result.reply == "Plans start at 29 per seat."
        assert result.ending is Ending.ANSWERED
        assert result.rounds == 1

    def test_the_system_prompt_and_message_are_what_the_model_is_given(self) -> None:
        model = ScriptedModel(says("Hello."))
        take_turn(model, message="hi there")
        asked = model.asked[0]
        assert asked["system"] == "You are a helpful agent."
        assert asked["messages"][-1] == ChatMessage(role="user", content="hi there")

    def test_earlier_messages_come_first(self) -> None:
        model = ScriptedModel(says("It is 29."))
        history = [
            ChatMessage(role="user", content="do you have a free trial?"),
            ChatMessage(role="assistant", content="Yes, 14 days."),
        ]
        take_turn(model, message="and the price?", history=history)
        roles = [m.role for m in model.asked[0]["messages"]]
        assert roles == ["user", "assistant", "user"]

    def test_the_admin_chosen_model_and_effort_are_used(self) -> None:
        # Both are adminOnly settings, so a workspace cannot move its agent
        # onto a more expensive model or make it think harder than the plan
        # pays for. There is no temperature: current models reject it.
        model = ScriptedModel(says("Hello."))
        take_turn(model)
        assert model.asked[0]["model"] == "claude-opus-5-5"
        assert model.asked[0]["effort"] == "low"

    def test_what_the_call_cost_is_added_up(self) -> None:
        model = ScriptedModel(
            calls("search_knowledge", {"query": "price"}),
            says("It is 29.", usage=Usage(input_tokens=100, output_tokens=20)),
        )
        result = take_turn(model, searcher(), search_first=False)
        assert result.usage.input_tokens == 110
        assert result.usage.output_tokens == 25
        assert result.model == "fake-model-1"


class TestSearchingFirst:
    def test_the_knowledge_is_searched_before_the_model_is_asked_anything(self) -> None:
        # An agent that only searches when it feels uncertain is wrong exactly
        # when it matters.
        model = ScriptedModel(says("Plans start at 29 per seat."))
        result = take_turn(model, searcher())
        assert [call.key for call in result.calls] == ["search_knowledge"]
        assert result.rounds == 1

    def test_the_model_is_shown_the_search_as_a_tool_call_and_its_result(self) -> None:
        # Otherwise it searches again for the same thing on its first move.
        model = ScriptedModel(says("Plans start at 29 per seat."))
        take_turn(model, searcher())
        messages = model.asked[0]["messages"]
        use = messages[-2].content[0]
        result = messages[-1].content[0]
        assert isinstance(use, ToolUseBlock)
        assert use.name == "search_knowledge"
        assert isinstance(result, ToolResultBlock)
        assert result.tool_use_id == use.id
        assert "29 per seat" in result.content

    def test_an_agent_without_the_ability_is_not_searched_for(self) -> None:
        model = ScriptedModel(says("I can help with that."))
        result = take_turn(model)
        assert result.calls == ()

    def test_searching_first_can_be_turned_off(self) -> None:
        model = ScriptedModel(says("Hello."))
        result = take_turn(model, searcher(), search_first=False)
        assert result.calls == ()

    def test_a_short_follow_up_is_searched_with_the_previous_question(self) -> None:
        assert search_query_for(
            "and the Pro plan?",
            [ChatMessage(role="user", content="how much is the Starter plan?")],
        ) == "how much is the Starter plan?\nand the Pro plan?"

    def test_a_question_that_stands_alone_is_searched_as_it_is(self) -> None:
        long_question = "how much does the enterprise plan cost per seat per month in euros"
        assert search_query_for(long_question, []) == long_question

    def test_the_first_message_of_a_conversation_has_no_previous_question(self) -> None:
        assert search_query_for("how much?", []) == "how much?"


class TestAbilitiesDuringATurn:
    def test_a_tool_call_is_run_and_its_result_fed_back(self) -> None:
        model = ScriptedModel(
            calls("search_knowledge", {"query": "trial"}),
            says("Yes — 14 days."),
        )
        result = take_turn(model, searcher(), search_first=False)
        assert result.ending is Ending.ANSWERED
        assert result.rounds == 2
        fed_back = model.asked[1]["messages"][-1].content[0]
        assert isinstance(fed_back, ToolResultBlock)
        assert json.loads(fed_back.content)["found"] is True

    def test_a_bad_tool_call_comes_back_as_a_problem_the_model_can_fix(self) -> None:
        model = ScriptedModel(
            calls("search_knowledge", {"quary": "trial"}),
            says("Yes — 14 days."),
        )
        result = take_turn(model, searcher(), search_first=False)
        fed_back = model.asked[1]["messages"][-1].content[0]
        assert isinstance(fed_back, ToolResultBlock)
        assert fed_back.is_error is True
        body = json.loads(fed_back.content)
        assert "problems" in body
        assert result.calls[0].error is AbilityErrorCode.INPUT_INVALID
        # And the turn carried on rather than ending on a correctable mistake.
        assert result.reply == "Yes — 14 days."

    def test_an_ability_the_agent_does_not_have_is_refused_not_run(self) -> None:
        model = ScriptedModel(
            calls("delete_everything", {}),
            says("I cannot do that, but I can help with pricing."),
        )
        result = take_turn(model, searcher(), search_first=False)
        assert result.calls[0].error is AbilityErrorCode.NOT_AVAILABLE
        assert result.reply.startswith("I cannot do that")

    def test_several_tool_calls_in_one_reply_all_run(self) -> None:
        model = ScriptedModel(
            ChatReply(
                content=(
                    ToolUseBlock(id="u1", name="search_knowledge", arguments={"query": "a"}),
                    ToolUseBlock(id="u2", name="search_knowledge", arguments={"query": "b"}),
                ),
                model="fake-model-1",
            ),
            says("Here is what I found."),
        )
        result = take_turn(model, searcher(), search_first=False)
        assert [call.key for call in result.calls] == ["search_knowledge"] * 2
        assert len(model.asked[1]["messages"][-1].content) == 2

    def test_the_rounds_run_out_rather_than_going_on_forever(self) -> None:
        model = ScriptedModel(
            *[calls("search_knowledge", {"query": "again"}, identifier=f"u{n}") for n in range(6)]
        )
        result = take_turn(model, searcher(), search_first=False, max_rounds=3)
        assert result.rounds == 3
        assert result.ending is Ending.EMPTY
        assert "could not find a good answer" in result.reply

    def test_a_model_that_talks_while_calling_keeps_what_it_said(self) -> None:
        model = ScriptedModel(
            calls("search_knowledge", {"query": "trial"}, text="Let me check."),
            says("Yes — 14 days."),
        )
        result = take_turn(model, searcher(), search_first=False)
        assert result.reply == "Let me check. Yes — 14 days."


class TestAbilitiesThatEndTheTurn:
    def test_a_decorating_ability_does_not_cost_another_round(self) -> None:
        # suggest_replies only decorates an answer that is already written.
        # Going round again would have the model write it twice.
        model = ScriptedModel(
            calls(
                "suggest_replies",
                {"replies": ["What does it cost?", "Book a demo"]},
                text="We integrate with Slack and Teams.",
            ),
            says("(this must not be reached)"),
        )
        result = take_turn(model, suggester(), search_first=False)
        assert result.ending is Ending.DECORATED
        assert result.rounds == 1
        assert result.reply == "We integrate with Slack and Teams."
        assert result.quick_replies == ("What does it cost?", "Book a demo")

    def test_it_does_not_end_the_turn_if_nothing_was_said_yet(self) -> None:
        # Ending there would show the visitor suggestions and no answer.
        model = ScriptedModel(
            calls("suggest_replies", {"replies": ["Book a demo"]}),
            says("Happy to help — what would you like to know?"),
        )
        result = take_turn(model, suggester(), search_first=False)
        assert result.rounds == 2
        assert result.reply == "Happy to help — what would you like to know?"


class TestCitations:
    def test_a_source_the_reply_drew_on_is_cited(self) -> None:
        model = ScriptedModel(says("Plans start at 29 per seat."))
        result = take_turn(model, searcher())
        assert [source["name"] for source in result.sources] == ["pricing.pdf"]

    def test_a_source_the_reply_did_not_use_is_not(self) -> None:
        # An agent that searches and then cites whatever it searched is lying
        # by implication.
        model = ScriptedModel(says("I am afraid I cannot help with that."))
        result = take_turn(model, searcher())
        assert result.sources == ()

    def test_nothing_is_cited_when_the_search_found_nothing(self) -> None:
        model = ScriptedModel(says("Plans start at 29 per seat."))
        result = take_turn(model, searcher(found=False))
        assert result.sources == ()

    def test_citations_are_capped(self) -> None:
        text = "Plans start at 29 per seat."

        async def many(arguments: Mapping[str, Any], context: AbilityContext) -> AbilityResult:
            return AbilityResult(
                output={"found": True, "passages": []},
                sources=tuple(
                    {"sourceId": f"s{n}", "name": f"doc{n}.pdf", "score": 0.4, "text": text}
                    for n in range(6)
                ),
            )

        ability = searcher()
        result = take_turn(
            ScriptedModel(says(text)),
            Ability(**{**ability.model_dump(exclude={"handler"}), "handler": many}),
            max_sources=2,
        )
        assert len(result.sources) == 2


class TestWhenThingsGoWrong:
    def test_a_model_that_says_nothing_gets_a_fallback(self) -> None:
        result = take_turn(ScriptedModel(ChatReply(content=())))
        assert result.ending is Ending.EMPTY
        assert "Could you rephrase it" in result.reply
        assert "Acme team" in result.reply

    def test_a_model_that_cannot_be_reached_says_so_differently(self) -> None:
        # "I could not find an answer" would be a lie: it never looked.
        result = take_turn(ScriptedModel(raises=RuntimeError("503 from the provider")))
        assert result.ending is Ending.UNAVAILABLE
        assert "trouble answering just now" in result.reply

    def test_nothing_from_the_exception_reaches_the_visitor(self) -> None:
        result = take_turn(
            ScriptedModel(raises=RuntimeError("api key sk-ant-secret is invalid"))
        )
        assert "sk-ant" not in result.reply

    def test_no_citations_or_suggestions_are_shown_with_a_fallback(self) -> None:
        # They would imply the fallback came from a source.
        model = ScriptedModel(ChatReply(content=()))
        result = take_turn(model, searcher(), suggester())
        assert result.sources == ()
        assert result.quick_replies == ()

    def test_an_ability_that_fails_does_not_end_the_turn(self) -> None:
        async def boom(arguments: Mapping[str, Any], context: AbilityContext) -> AbilityResult:
            raise RuntimeError("the index is down")

        ability = searcher()
        broken = Ability(**{**ability.model_dump(exclude={"handler"}), "handler": boom})
        model = ScriptedModel(says("I cannot check that right now, but I can help otherwise."))
        result = take_turn(model, broken)
        assert result.calls[0].error is AbilityErrorCode.FAILED
        assert result.ending is Ending.ANSWERED
        # The model was told, in a form it can act on.
        result_block = model.asked[0]["messages"][-1].content[0]
        assert result_block.is_error is True
        assert "unavailable at the moment" in result_block.content


class TestThePortIsSatisfiedStructurally:
    def test_the_scripted_model_is_a_chat_model(self) -> None:
        assert isinstance(ScriptedModel(), ChatModel)
