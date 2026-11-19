"""
Tests for the Anthropic adapter, up to the network and no further.

The responses are built from the SDK's **own** types, not from hand-made
stand-ins. A fake shaped the way I remember the API is a test that passes while
the adapter is wrong; these fail if the SDK's shapes are not what the adapter
reads.

What is checked here is translation, which is all this file does: the request
it builds, and the reply it turns back into the port's own blocks.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from anthropic.types import Message, TextBlock as SdkText, ToolUseBlock as SdkToolUse, Usage as SdkUsage
from anthropic.types import ThinkingBlock as SdkThinking

from celeris_bridge.adapters.anthropic_chat import PROVIDER, AnthropicChatModel
from celeris_bridge.ports.chat import (
    ChatMessage,
    ChatModel,
    OpaqueBlock,
    TextBlock,
    ToolResultBlock,
    ToolSpec,
    ToolUseBlock,
)

SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string", "maxLength": 300}},
    "required": ["query"],
    "additionalProperties": False,
}


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def reply(*content: Any, stop_reason: str = "end_turn", model: str = "claude-opus-5-5") -> Message:
    return Message(
        id="msg_1",
        model=model,
        role="assistant",
        type="message",
        stop_reason=stop_reason,
        content=list(content),
        usage=SdkUsage(input_tokens=120, output_tokens=34),
    )


class StubMessages:
    def __init__(self, response: Message) -> None:
        self.response = response
        self.requests: list[dict[str, Any]] = []

    async def create(self, **request: Any) -> Message:
        self.requests.append(request)
        return self.response


class StubClient:
    def __init__(self, response: Message) -> None:
        self.messages = StubMessages(response)


def ask(response: Message | None = None, **changed: Any):
    client = StubClient(response or reply(SdkText(type="text", text="Hello.")))
    adapter = AnthropicChatModel(client)  # type: ignore[arg-type]
    arguments: dict[str, Any] = {
        "system": "You are a helpful agent.",
        "messages": [ChatMessage(role="user", content="how much is it?")],
    }
    arguments.update(changed)
    result = run(adapter.chat(**arguments))
    return result, client.messages.requests[0]


class TestTheRequest:
    def test_it_satisfies_the_port(self) -> None:
        assert isinstance(AnthropicChatModel(StubClient(reply())), ChatModel)  # type: ignore[arg-type]

    def test_the_system_prompt_and_messages_go_through(self) -> None:
        _, request = ask()
        assert request["system"] == "You are a helpful agent."
        assert request["messages"] == [{"role": "user", "content": "how much is it?"}]

    def test_no_temperature_is_ever_sent(self) -> None:
        # Current models reject the parameter outright. This is the assertion
        # that stops it being reintroduced by someone reaching for a familiar
        # knob.
        _, request = ask()
        assert "temperature" not in request
        assert "top_p" not in request
        assert "top_k" not in request

    def test_effort_is_sent_as_output_config(self) -> None:
        _, request = ask(effort="low")
        assert request["output_config"] == {"effort": "low"}

    def test_no_effort_means_the_models_own_default(self) -> None:
        _, request = ask()
        assert "output_config" not in request

    def test_a_made_up_effort_level_is_refused_here_rather_than_by_the_api(self) -> None:
        with pytest.raises(ValueError):
            ask(effort="maximum")

    def test_thinking_is_never_configured(self) -> None:
        # Thinking is always on for the current models, and sending
        # {"type": "disabled"} is an error. Omitting it is the correct request.
        _, request = ask()
        assert "thinking" not in request

    def test_the_caller_chooses_the_model_per_call(self) -> None:
        _, request = ask(model="claude-haiku-5-5")
        assert request["model"] == "claude-haiku-5-5"

    def test_with_no_model_named_the_adapters_default_is_used(self) -> None:
        _, request = ask()
        assert request["model"] == "claude-opus-5-5"

    def test_max_tokens_goes_through(self) -> None:
        _, request = ask(max_tokens=700)
        assert request["max_tokens"] == 700


class TestToolsAsTheModelSeesThem:
    def test_a_tool_becomes_a_tool_definition(self) -> None:
        _, request = ask(
            tools=[ToolSpec(name="search_knowledge", description="Search.", schema=SCHEMA)]
        )
        tool = request["tools"][0]
        assert tool["name"] == "search_knowledge"
        assert tool["description"] == "Search."
        assert tool["input_schema"] == SCHEMA

    def test_every_tool_is_strict(self) -> None:
        # The schemas already close additionalProperties and list required, so
        # strict costs nothing and makes the provider guarantee arguments that
        # fit. Without it, a model invents a field and the correction costs a
        # round trip.
        _, request = ask(
            tools=[ToolSpec(name="search_knowledge", description="Search.", schema=SCHEMA)]
        )
        assert request["tools"][0]["strict"] is True

    def test_the_schemas_meet_what_strict_mode_requires(self) -> None:
        # If this ever fails, strict must come off — or field_spec changed.
        from celeris_bridge.domain.agent.abilities import input_json_schema
        from celeris_bridge.domain.agent.abilities.builtins import builtin_abilities

        for key, ability in builtin_abilities().items():
            schema = input_json_schema(ability, {"qualificationFields": []})
            assert schema["additionalProperties"] is False, key
            assert "required" in schema, key

    def test_no_tools_means_no_tools_key(self) -> None:
        _, request = ask()
        assert "tools" not in request


class TestSendingBlocksBack:
    def test_text_and_tool_use_are_translated(self) -> None:
        _, request = ask(
            messages=[
                ChatMessage(
                    role="assistant",
                    content=(
                        TextBlock(text="Let me check."),
                        ToolUseBlock(id="u1", name="search_knowledge", arguments={"query": "x"}),
                    ),
                )
            ]
        )
        assert request["messages"][0]["content"] == [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "u1", "name": "search_knowledge", "input": {"query": "x"}},
        ]

    def test_a_tool_result_is_translated(self) -> None:
        _, request = ask(
            messages=[
                ChatMessage(
                    role="user",
                    content=(ToolResultBlock(tool_use_id="u1", content='{"found": true}'),),
                )
            ]
        )
        assert request["messages"][0]["content"] == [
            {"type": "tool_result", "tool_use_id": "u1", "content": '{"found": true}'}
        ]

    def test_a_failed_tool_result_says_so(self) -> None:
        _, request = ask(
            messages=[
                ChatMessage(
                    role="user",
                    content=(
                        ToolResultBlock(tool_use_id="u1", content='{"error": "x"}', is_error=True),
                    ),
                )
            ]
        )
        assert request["messages"][0]["content"][0]["is_error"] is True

    def test_an_opaque_block_goes_back_exactly_as_it_came(self) -> None:
        # This is what keeps the model's own reasoning intact across the tool
        # rounds of one turn.
        payload = {"type": "thinking", "thinking": "because", "signature": "sig"}
        _, request = ask(
            messages=[
                ChatMessage(
                    role="assistant",
                    content=(OpaqueBlock(provider=PROVIDER, payload=payload),),
                )
            ]
        )
        assert request["messages"][0]["content"] == [payload]

    def test_another_providers_block_is_refused_loudly(self) -> None:
        # Sending one provider's reasoning to another is a 400 at best. Better
        # to say which two were mixed up.
        with pytest.raises(ValueError) as caught:
            ask(
                messages=[
                    ChatMessage(
                        role="assistant",
                        content=(OpaqueBlock(provider="someone_else", payload={}),),
                    )
                ]
            )
        assert "someone_else" in str(caught.value)


class TestTheReply:
    def test_text_comes_back_as_text(self) -> None:
        result, _ = ask(reply(SdkText(type="text", text="It is 29 per seat.")))
        assert result.text == "It is 29 per seat."
        assert result.content == (TextBlock(text="It is 29 per seat."),)

    def test_a_tool_call_comes_back_with_its_arguments(self) -> None:
        result, _ = ask(
            reply(
                SdkToolUse(type="tool_use", id="u1", name="search_knowledge", input={"query": "price"})
            )
        )
        use = result.tool_uses[0]
        assert (use.id, use.name, use.arguments) == ("u1", "search_knowledge", {"query": "price"})

    def test_reasoning_is_kept_without_being_understood(self) -> None:
        result, _ = ask(
            reply(
                SdkThinking(type="thinking", thinking="let me reason", signature="sig"),
                SdkText(type="text", text="29 per seat."),
            )
        )
        kept = result.content[0]
        assert isinstance(kept, OpaqueBlock)
        assert kept.provider == PROVIDER
        assert kept.payload["type"] == "thinking"
        # And it does not leak into what the turn loop treats as the reply.
        assert result.text == "29 per seat."

    def test_the_order_of_the_blocks_is_preserved(self) -> None:
        # Reasoning first, then text, then the tool call: the provider rejects
        # a reordered assistant turn.
        result, _ = ask(
            reply(
                SdkThinking(type="thinking", thinking="r", signature="s"),
                SdkText(type="text", text="Checking."),
                SdkToolUse(type="tool_use", id="u1", name="search_knowledge", input={"query": "x"}),
            )
        )
        assert [type(block).__name__ for block in result.content] == [
            "OpaqueBlock",
            "TextBlock",
            "ToolUseBlock",
        ]

    def test_what_it_cost_comes_back(self) -> None:
        result, _ = ask()
        assert result.usage.input_tokens == 120
        assert result.usage.output_tokens == 34

    def test_which_model_answered_comes_back(self) -> None:
        result, _ = ask(reply(SdkText(type="text", text="x"), model="claude-haiku-5-5"))
        assert result.model == "claude-haiku-5-5"

    def test_why_it_stopped_comes_back(self) -> None:
        # max_tokens here means the reply was cut off mid-sentence, which is
        # worth being able to see.
        result, _ = ask(reply(SdkText(type="text", text="x"), stop_reason="max_tokens"))
        assert result.stop_reason == "max_tokens"

    def test_an_empty_reply_is_not_an_error_here(self) -> None:
        # The turn loop decides what an empty reply means; the adapter just
        # reports it.
        result, _ = ask(reply())
        assert result.content == ()
        assert result.text == ""
