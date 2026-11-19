"""
Tests for the ability runner.

Two promises: nothing runs that should not, and nothing raises. The second is
the harder one — the caller is a turn loop whose only move is to tell the model
what went wrong, so an exception reaching it ends a conversation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

import pytest

from celeris_bridge.domain.agent.abilities import (
    Ability,
    AbilityContext,
    AbilityRegistry,
    AbilityResult,
)
from celeris_bridge.domain.agent.definition_schema import validate_definition
from celeris_bridge.domain.agent.engine.runner import (
    AbilityErrorCode,
    Turn,
    TurnContext,
    enabled_tools,
    run_ability,
)
from celeris_bridge.domain.agent.overrides import resolve_config

CONTEXT = TurnContext(workspace_id="w1", agent_instance_id="a1", conversation_id="c1")


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


@dataclass
class Spy:
    """Records what the handler was given, so the context can be inspected."""

    calls: list[tuple[Mapping[str, Any], AbilityContext]]

    def __init__(self) -> None:
        self.calls = []


def reader(spy: Spy | None = None, **changed: Any) -> Ability:
    spy = spy or Spy()

    async def handler(arguments: Mapping[str, Any], context: AbilityContext) -> AbilityResult:
        spy.calls.append((arguments, context))
        return AbilityResult(output={"echo": dict(arguments)}, meta={"ran": True})

    base: dict[str, Any] = {
        "key": "look_it_up",
        "version": "1.0.0",
        "label": "Look it up",
        "description": "Looks something up.",
        "ai_description": "Look something up.",
        "input": {"query": {"type": "string", "description": "What to look up.", "required": True}},
        "effect": "read",
        "handler": handler,
    }
    return Ability(**{**base, **changed})


def writer(**changed: Any) -> Ability:
    runs: list[int] = []

    async def handler(arguments: Mapping[str, Any], context: AbilityContext) -> AbilityResult:
        runs.append(1)
        return AbilityResult(output={"booked": True, "times": len(runs)})

    base: dict[str, Any] = {
        "key": "book_it",
        "version": "1.0.0",
        "label": "Book it",
        "description": "Books something.",
        "ai_description": "Book something.",
        "input": {"slot": {"type": "string", "description": "Which slot.", "required": True}},
        "effect": "write",
        "idempotency_key": lambda arguments, context: f"{context.conversation_id}:{arguments['slot']}",
        "handler": handler,
    }
    return Ability(**{**base, **changed})


def agent(
    *abilities: Ability,
    enabled: bool = True,
    settings: Mapping[str, Any] | None = None,
) -> tuple[Any, AbilityRegistry]:
    """A published definition switching on these abilities, and the registry."""
    registry = AbilityRegistry(abilities)
    raw: dict[str, Any] = {
        "identity": {"name": "Test", "channels": ["web_chat"]},
        "settings": {
            "tone": {
                "label": "Tone",
                "type": "string",
                "default": "friendly",
                "editable": True,
            },
            **(settings or {}),
        },
        "abilities": [
            {"key": ability.key, "enabledByDefault": enabled} for ability in abilities
        ],
    }
    report = validate_definition(raw, abilities=registry)
    assert report.definition is not None, [p.message for p in report.problems]
    return resolve_config(report.definition, None, abilities=registry), registry


class TestWhatTheModelIsOffered:
    def test_an_enabled_ability_becomes_a_tool(self) -> None:
        config, registry = agent(reader())
        tools = enabled_tools(config, registry)
        assert [tool.name for tool in tools] == ["look_it_up"]
        assert tools[0].description == "Look something up."
        assert tools[0].schema["required"] == ["query"]

    def test_a_switched_off_ability_is_not_offered(self) -> None:
        config, registry = agent(reader(), enabled=False)
        assert enabled_tools(config, registry) == ()

    def test_an_ability_this_build_no_longer_has_is_not_offered(self) -> None:
        config, _ = agent(reader())
        assert enabled_tools(config, AbilityRegistry()) == ()

    def test_the_schema_is_built_from_this_agents_settings(self) -> None:
        # An ability whose arguments depend on configuration must describe the
        # agent being run, not the ability in the abstract.
        ability = reader(
            input={},
            input_for=lambda settings: {
                settings["tone"]: {"type": "string", "description": "A tone-named argument."}
            },
        )
        config, registry = agent(ability)
        assert list(enabled_tools(config, registry)[0].schema["properties"]) == ["friendly"]

    def test_an_ability_that_ends_the_turn_says_so(self) -> None:
        config, registry = agent(reader(ends_turn=True))
        assert enabled_tools(config, registry)[0].ends_turn is True


class TestRefusingToRun:
    def test_an_ability_the_agent_does_not_have(self) -> None:
        config, registry = agent(reader())
        outcome = run(
            run_ability(
                key="something_else",
                arguments={},
                config=config,
                registry=registry,
                context=CONTEXT,
            )
        )
        assert outcome.ok is False
        assert outcome.error is not None
        assert outcome.error.code is AbilityErrorCode.NOT_AVAILABLE

    def test_an_ability_the_build_has_but_the_agent_does_not(self) -> None:
        config, _ = agent(reader())
        outcome = run(
            run_ability(
                key="look_it_up",
                arguments={"query": "x"},
                config=config,
                registry=AbilityRegistry([writer()]),
                context=CONTEXT,
            )
        )
        assert outcome.error is not None
        assert outcome.error.code is AbilityErrorCode.NOT_AVAILABLE

    def test_an_ability_the_workspace_switched_off(self) -> None:
        config, registry = agent(reader(), enabled=False)
        outcome = run(
            run_ability(
                key="look_it_up",
                arguments={"query": "x"},
                config=config,
                registry=registry,
                context=CONTEXT,
            )
        )
        assert outcome.error is not None
        assert outcome.error.code is AbilityErrorCode.DISABLED
        # Named as the person sees it, because the model repeats this.
        assert "Look it up" in outcome.error.message

    def test_a_call_with_no_workspace_on_it(self) -> None:
        # A bug in the caller, not something a model can cause. It still must
        # not raise.
        config, registry = agent(reader())
        outcome = run(
            run_ability(
                key="look_it_up",
                arguments={"query": "x"},
                config=config,
                registry=registry,
                context=TurnContext(workspace_id=""),
            )
        )
        assert outcome.error is not None
        assert outcome.error.code is AbilityErrorCode.NO_TENANT


class TestArguments:
    def test_good_arguments_reach_the_handler_normalised(self) -> None:
        spy = Spy()
        config, registry = agent(reader(spy))
        outcome = run(
            run_ability(
                key="look_it_up",
                arguments={"query": "  pricing  "},
                config=config,
                registry=registry,
                context=CONTEXT,
            )
        )
        assert outcome.ok is True
        assert spy.calls[0][0] == {"query": "pricing"}

    def test_bad_arguments_come_back_as_problems_the_model_can_fix(self) -> None:
        config, registry = agent(reader())
        outcome = run(
            run_ability(
                key="look_it_up",
                arguments={"quary": "pricing"},
                config=config,
                registry=registry,
                context=CONTEXT,
            )
        )
        assert outcome.error is not None
        assert outcome.error.code is AbilityErrorCode.INPUT_INVALID
        arguments = {detail["argument"] for detail in outcome.error.details}
        assert arguments == {"quary", "query"}

    def test_bad_arguments_still_use_up_an_attempt(self) -> None:
        # Otherwise a model sending the same malformed call loops for as long
        # as the request lasts.
        config, registry = agent(reader())
        turn = Turn()
        for _ in range(4):
            run(
                run_ability(
                    key="look_it_up",
                    arguments={},
                    config=config,
                    registry=registry,
                    context=CONTEXT,
                    turn=turn,
                )
            )
        codes = [call.error for call in turn.calls]
        assert codes[:3] == [AbilityErrorCode.INPUT_INVALID] * 3
        assert codes[3] is AbilityErrorCode.LIMIT


class TestTheHandlersContext:
    def test_it_gets_this_abilitys_own_settings(self) -> None:
        spy = Spy()
        ability = reader(
            spy,
            settings={
                "depth": {"label": "Depth", "type": "number", "integer": True, "default": 3}
            },
        )
        config, registry = agent(ability)
        run(
            run_ability(
                key="look_it_up",
                arguments={"query": "x"},
                config=config,
                registry=registry,
                context=CONTEXT,
            )
        )
        assert spy.calls[0][1].settings == {"depth": 3}

    def test_it_gets_the_agents_settings_too(self) -> None:
        spy = Spy()
        config, registry = agent(reader(spy))
        run(
            run_ability(
                key="look_it_up",
                arguments={"query": "x"},
                config=config,
                registry=registry,
                context=CONTEXT,
            )
        )
        assert spy.calls[0][1].agent_settings["tone"] == "friendly"

    def test_the_tenant_comes_from_the_caller(self) -> None:
        spy = Spy()
        config, registry = agent(reader(spy))
        run(
            run_ability(
                key="look_it_up",
                arguments={"query": "x"},
                config=config,
                registry=registry,
                context=CONTEXT,
            )
        )
        context = spy.calls[0][1]
        assert context.workspace_id == "w1"
        assert context.conversation_id == "c1"

    def test_the_services_are_handed_straight_through(self) -> None:
        spy = Spy()
        services = object()
        config, registry = agent(reader(spy))
        run(
            run_ability(
                key="look_it_up",
                arguments={"query": "x"},
                config=config,
                registry=registry,
                context=TurnContext(workspace_id="w1", services=services),
            )
        )
        assert spy.calls[0][1].services is services


class TestLimits:
    def test_an_ability_runs_only_as_often_as_it_may(self) -> None:
        config, registry = agent(reader(limits={"maxCallsPerTurn": 2}))
        turn = Turn()
        outcomes = [
            run(
                run_ability(
                    key="look_it_up",
                    arguments={"query": "x"},
                    config=config,
                    registry=registry,
                    context=CONTEXT,
                    turn=turn,
                )
            )
            for _ in range(3)
        ]
        assert [outcome.ok for outcome in outcomes] == [True, True, False]
        assert outcomes[2].error is not None
        assert outcomes[2].error.code is AbilityErrorCode.LIMIT
        # And the model is told what to do instead of trying a fourth time.
        assert "Answer with what you have" in outcomes[2].error.message

    def test_each_ability_has_its_own_allowance(self) -> None:
        config, registry = agent(reader(limits={"maxCallsPerTurn": 1}), writer())
        turn = Turn()
        run(run_ability(key="look_it_up", arguments={"query": "x"}, config=config, registry=registry, context=CONTEXT, turn=turn))
        outcome = run(
            run_ability(key="book_it", arguments={"slot": "9am"}, config=config, registry=registry, context=CONTEXT, turn=turn)
        )
        assert outcome.ok is True

    def test_a_fresh_message_starts_a_fresh_allowance(self) -> None:
        config, registry = agent(reader(limits={"maxCallsPerTurn": 1}))
        for _ in range(3):
            outcome = run(
                run_ability(
                    key="look_it_up",
                    arguments={"query": "x"},
                    config=config,
                    registry=registry,
                    context=CONTEXT,
                    turn=Turn(),
                )
            )
            assert outcome.ok is True

    def test_a_handler_that_hangs_is_cut_off(self) -> None:
        async def forever(arguments: Mapping[str, Any], context: AbilityContext) -> AbilityResult:
            await asyncio.sleep(5)
            return AbilityResult()

        config, registry = agent(reader(handler=forever, limits={"timeoutMs": 100}))
        outcome = run(
            run_ability(
                key="look_it_up",
                arguments={"query": "x"},
                config=config,
                registry=registry,
                context=CONTEXT,
            )
        )
        assert outcome.error is not None
        assert outcome.error.code is AbilityErrorCode.TIMEOUT
        assert "could not check that just now" in outcome.error.message


class TestNothingEscapes:
    def test_a_handler_that_raises_becomes_a_failure(self) -> None:
        async def boom(arguments: Mapping[str, Any], context: AbilityContext) -> AbilityResult:
            raise RuntimeError("mongo is on fire: connection string postgres://u:p@host")

        config, registry = agent(reader(handler=boom))
        outcome = run(
            run_ability(
                key="look_it_up",
                arguments={"query": "x"},
                config=config,
                registry=registry,
                context=CONTEXT,
            )
        )
        assert outcome.ok is False
        assert outcome.error is not None
        assert outcome.error.code is AbilityErrorCode.FAILED

    def test_nothing_about_the_exception_reaches_the_model(self) -> None:
        # The model repeats what it is told, and a visitor must not read a
        # stack trace or a connection string.
        async def boom(arguments: Mapping[str, Any], context: AbilityContext) -> AbilityResult:
            raise RuntimeError("connection string postgres://user:secret@host")

        config, registry = agent(reader(handler=boom))
        outcome = run(
            run_ability(key="look_it_up", arguments={"query": "x"}, config=config, registry=registry, context=CONTEXT)
        )
        assert outcome.error is not None
        assert "secret" not in outcome.error.message
        assert "postgres" not in outcome.error.message
        assert outcome.error.details == ()

    def test_a_handler_returning_the_wrong_thing_is_a_failure_not_a_crash(self) -> None:
        async def wrong(arguments: Mapping[str, Any], context: AbilityContext) -> Any:
            return {"output": "I am not an AbilityResult"}

        config, registry = agent(reader(handler=wrong))
        outcome = run(
            run_ability(key="look_it_up", arguments={"query": "x"}, config=config, registry=registry, context=CONTEXT)
        )
        assert outcome.error is not None
        assert outcome.error.code is AbilityErrorCode.FAILED


class TestWritingTwiceInOneMessage:
    def test_the_same_write_asked_for_twice_happens_once(self) -> None:
        config, registry = agent(writer())
        turn = Turn()
        first = run(
            run_ability(key="book_it", arguments={"slot": "9am"}, config=config, registry=registry, context=CONTEXT, turn=turn)
        )
        second = run(
            run_ability(key="book_it", arguments={"slot": "9am"}, config=config, registry=registry, context=CONTEXT, turn=turn)
        )
        # Both say it worked — the model must not be told a booking failed when
        # it did not — and the handler ran once.
        assert first.ok is True and second.ok is True
        assert first.output["times"] == 1
        assert second.output["times"] == 1
        assert second.replayed is True
        assert first.replayed is False

    def test_a_different_write_still_happens(self) -> None:
        config, registry = agent(writer())
        turn = Turn()
        run(run_ability(key="book_it", arguments={"slot": "9am"}, config=config, registry=registry, context=CONTEXT, turn=turn))
        second = run(
            run_ability(key="book_it", arguments={"slot": "10am"}, config=config, registry=registry, context=CONTEXT, turn=turn)
        )
        assert second.output["times"] == 2
        assert second.replayed is False

    def test_a_replay_still_costs_an_attempt(self) -> None:
        config, registry = agent(writer(limits={"maxCallsPerTurn": 2}))
        turn = Turn()
        for _ in range(2):
            run(run_ability(key="book_it", arguments={"slot": "9am"}, config=config, registry=registry, context=CONTEXT, turn=turn))
        third = run(
            run_ability(key="book_it", arguments={"slot": "9am"}, config=config, registry=registry, context=CONTEXT, turn=turn)
        )
        assert third.error is not None
        assert third.error.code is AbilityErrorCode.LIMIT

    def test_reads_are_not_deduplicated(self) -> None:
        # Searching the same thing twice is wasteful, not dangerous, and a
        # second identical search can legitimately follow a first.
        spy = Spy()
        config, registry = agent(reader(spy))
        turn = Turn()
        for _ in range(2):
            run(run_ability(key="look_it_up", arguments={"query": "x"}, config=config, registry=registry, context=CONTEXT, turn=turn))
        assert len(spy.calls) == 2


class TestTheRecordOfTheTurn:
    def test_every_call_is_recorded_with_its_outcome(self) -> None:
        config, registry = agent(reader(), writer())
        turn = Turn()
        run(run_ability(key="look_it_up", arguments={"query": "x"}, config=config, registry=registry, context=CONTEXT, turn=turn))
        run(run_ability(key="book_it", arguments={}, config=config, registry=registry, context=CONTEXT, turn=turn))
        assert [(c.key, c.ok, c.error) for c in turn.calls] == [
            ("look_it_up", True, None),
            ("book_it", False, AbilityErrorCode.INPUT_INVALID),
        ]

    def test_a_refusal_before_anything_ran_is_recorded_too(self) -> None:
        config, registry = agent(reader())
        turn = Turn()
        run(run_ability(key="nope", arguments={}, config=config, registry=registry, context=CONTEXT, turn=turn))
        assert turn.calls[0].error is AbilityErrorCode.NOT_AVAILABLE

    def test_the_result_carries_what_the_handler_returned(self) -> None:
        config, registry = agent(reader())
        outcome = run(
            run_ability(key="look_it_up", arguments={"query": "x"}, config=config, registry=registry, context=CONTEXT)
        )
        assert outcome.output == {"echo": {"query": "x"}}
        assert outcome.meta == {"ran": True}
        assert outcome.milliseconds >= 0
