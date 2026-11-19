"""
Running an ability on an agent's behalf.

The only way a handler executes. Everything a model asks for comes through
here, and here is where it is established that the ability exists, that this
agent has it, that it is switched on, that it has not already been used too
often this turn, and that the arguments fit. Then the handler runs with a
timeout and a context the model had no part in building.

**It never raises.** A failure is a value, because the caller is a turn loop
whose next move is always the same: tell the model what went wrong and let it
try something else. An exception escaping to there would end a conversation
over a calendar being briefly unreachable.

This module is pure: the only things it touches are the ports the ability
itself asks for, through the services on the context.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence

from celeris_bridge.domain.agent.abilities.contract import (
    Ability,
    AbilityContext,
    AbilityEffect,
    AbilityInputInvalid,
    AbilityResult,
    input_json_schema,
    validate_input,
)
from celeris_bridge.domain.agent.abilities.registry import AbilityRegistry
from celeris_bridge.domain.agent.overrides import ResolvedConfig

logger = logging.getLogger(__name__)

__all__ = [
    "AbilityCall",
    "AbilityError",
    "AbilityErrorCode",
    "AbilityOutcome",
    "AbilityTool",
    "Turn",
    "TurnContext",
    "enabled_tools",
    "run_ability",
]


class AbilityErrorCode(StrEnum):
    """Why a call did not produce a result. Stable: logs and tests match on it."""

    #: No workspace on the context. A bug, never a model's doing.
    NO_TENANT = "ABILITY_NO_TENANT"
    #: This agent does not have that ability, or this build does not.
    NOT_AVAILABLE = "ABILITY_NOT_AVAILABLE"
    #: The agent has it, the workspace switched it off.
    DISABLED = "ABILITY_DISABLED"
    #: Already used its allowance for this message.
    LIMIT = "ABILITY_LIMIT"
    #: The arguments do not fit. Carries the problems so the model can fix them.
    INPUT_INVALID = "ABILITY_INPUT_INVALID"
    TIMEOUT = "ABILITY_TIMEOUT"
    #: The handler raised. Deliberately vague to the model.
    FAILED = "ABILITY_FAILED"


@dataclass(frozen=True)
class AbilityError:
    code: AbilityErrorCode
    #: Read by the model, so it says what to do instead.
    message: str
    #: Per-argument problems, for INPUT_INVALID. The model corrects from these.
    details: tuple[Mapping[str, str], ...] = ()


@dataclass(frozen=True)
class AbilityOutcome:
    """What one call came to."""

    key: str
    ok: bool
    output: Mapping[str, Any] = field(default_factory=dict)
    sources: tuple[Mapping[str, Any], ...] = ()
    display: Mapping[str, Any] | None = None
    meta: Mapping[str, Any] = field(default_factory=dict)
    error: AbilityError | None = None
    ends_turn: bool = False
    milliseconds: int = 0
    #: True when an identical write was already made this turn and replayed.
    replayed: bool = False


@dataclass(frozen=True)
class AbilityCall:
    """One line of a turn's record of what it did."""

    key: str
    ok: bool
    milliseconds: int
    error: AbilityErrorCode | None = None
    replayed: bool = False


@dataclass(frozen=True)
class AbilityTool:
    """An enabled ability as the model needs to see it."""

    name: str
    description: str
    schema: Mapping[str, Any]
    instructions: str = ""
    ends_turn: bool = False


@dataclass(frozen=True)
class TurnContext:
    """What the runner knows, before any particular ability is involved.

    Built by whoever is serving the conversation, from the authenticated
    principal and the session. The ability's own settings are added by the
    runner — an ability cannot be handed another ability's configuration.
    """

    workspace_id: str
    agent_instance_id: str | None = None
    conversation_id: str | None = None
    services: Any = None


class Turn:
    """State shared by every ability call for one visitor message.

    Holds the per-ability counts that enforce ``max_calls_per_turn``, the record
    of what was called, and the results of writes already made — so that a
    model asking for the same booking twice in one message books once.
    """

    __slots__ = ("calls", "_counts", "_writes")

    def __init__(self) -> None:
        self.calls: list[AbilityCall] = []
        self._counts: dict[str, int] = {}
        self._writes: dict[str, AbilityOutcome] = {}

    def used(self, key: str) -> int:
        return self._counts.get(key, 0)

    def count(self, key: str) -> None:
        self._counts[key] = self._counts.get(key, 0) + 1

    def written(self, fingerprint: str) -> AbilityOutcome | None:
        return self._writes.get(fingerprint)

    def remember(self, fingerprint: str, outcome: AbilityOutcome) -> None:
        self._writes[fingerprint] = outcome

    def record(self, outcome: AbilityOutcome) -> AbilityOutcome:
        self.calls.append(
            AbilityCall(
                key=outcome.key,
                ok=outcome.ok,
                milliseconds=outcome.milliseconds,
                error=outcome.error.code if outcome.error else None,
                replayed=outcome.replayed,
            )
        )
        return outcome


def enabled_tools(
    config: ResolvedConfig, registry: AbilityRegistry
) -> tuple[AbilityTool, ...]:
    """The abilities this agent may use, as tools.

    Each schema is built from the agent's own settings, so an ability whose
    arguments depend on configuration describes *this* agent.
    """
    tools: list[AbilityTool] = []
    for resolved in config.enabled_abilities:
        ability = registry.get(resolved.key)
        if ability is None:
            # Reported as ResolvedConfig.missing_abilities; not offered.
            continue
        tools.append(
            AbilityTool(
                name=ability.key,
                description=ability.ai_description,
                schema=input_json_schema(ability, config.settings),
                instructions=ability.instructions,
                ends_turn=ability.ends_turn,
            )
        )
    return tuple(tools)


def _failed(
    key: str,
    code: AbilityErrorCode,
    message: str,
    *,
    details: Sequence[Mapping[str, str]] = (),
    milliseconds: int = 0,
) -> AbilityOutcome:
    return AbilityOutcome(
        key=key,
        ok=False,
        error=AbilityError(code=code, message=message, details=tuple(details)),
        milliseconds=milliseconds,
    )


async def run_ability(
    *,
    key: str,
    arguments: Any,
    config: ResolvedConfig,
    registry: AbilityRegistry,
    context: TurnContext,
    turn: Turn | None = None,
) -> AbilityOutcome:
    """Run one ability. Never raises."""
    turn = turn if turn is not None else Turn()
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    if not context.workspace_id:
        return turn.record(
            _failed(key, AbilityErrorCode.NO_TENANT, "No workspace for this call")
        )

    ability = registry.get(key)
    resolved = config.ability(key)
    if ability is None or resolved is None:
        return turn.record(
            _failed(
                key,
                AbilityErrorCode.NOT_AVAILABLE,
                f'"{key}" is not one of your tools',
            )
        )
    if not resolved.enabled:
        return turn.record(
            _failed(
                key,
                AbilityErrorCode.DISABLED,
                f'"{ability.label}" is switched off for this agent',
            )
        )

    allowance = ability.limits.max_calls_per_turn
    if turn.used(key) >= allowance:
        return turn.record(
            _failed(
                key,
                AbilityErrorCode.LIMIT,
                f'"{ability.label}" has already been used {allowance} '
                f"time{'s' if allowance != 1 else ''} for this message. "
                "Answer with what you have.",
            )
        )
    # Counted before the arguments are checked, so a model that keeps sending
    # bad ones runs out of attempts instead of looping for the length of a
    # request.
    turn.count(key)

    try:
        clean = validate_input(ability, arguments, config.settings)
    except AbilityInputInvalid as invalid:
        return turn.record(
            _failed(
                key,
                AbilityErrorCode.INPUT_INVALID,
                f'Those arguments do not fit "{ability.label}". Correct them and try again.',
                details=[
                    {"argument": problem.path, "problem": problem.message}
                    for problem in invalid.problems
                ],
                milliseconds=elapsed(),
            )
        )

    fingerprint = ""
    if ability.effect is AbilityEffect.WRITE and ability.idempotency_key is not None:
        probe = AbilityContext(
            workspace_id=context.workspace_id,
            agent_instance_id=context.agent_instance_id,
            conversation_id=context.conversation_id,
        )
        fingerprint = f"{key}:{ability.idempotency_key(clean, probe)}"
        earlier = turn.written(fingerprint)
        if earlier is not None:
            # The same write, asked for twice in one message. Replaying the
            # first result is the only answer that is both honest and safe: the
            # model is told it worked, and nothing happened twice.
            return turn.record(
                AbilityOutcome(
                    key=earlier.key,
                    ok=earlier.ok,
                    output=earlier.output,
                    sources=earlier.sources,
                    display=earlier.display,
                    meta=earlier.meta,
                    error=earlier.error,
                    ends_turn=earlier.ends_turn,
                    milliseconds=elapsed(),
                    replayed=True,
                )
            )

    ability_context = AbilityContext(
        workspace_id=context.workspace_id,
        agent_instance_id=context.agent_instance_id,
        conversation_id=context.conversation_id,
        settings=dict(resolved.settings),
        agent_settings=config.settings,
        services=context.services,
    )

    try:
        result = await asyncio.wait_for(
            ability.handler(clean, ability_context),
            timeout=ability.limits.timeout_ms / 1000,
        )
    except asyncio.TimeoutError:
        return turn.record(
            _failed(
                key,
                AbilityErrorCode.TIMEOUT,
                f'"{ability.label}" took too long. Tell the visitor you could not '
                "check that just now.",
                milliseconds=elapsed(),
            )
        )
    except Exception:
        # Nothing about the exception reaches the model: it would be repeated
        # to a visitor, and a stack trace is not something a visitor should
        # read. It is logged here, where the exception actually is — the
        # comment used to say the caller would, and no caller did.
        logger.exception(
            "Ability %s failed for workspace %s", key, context.workspace_id
        )
        return turn.record(
            _failed(
                key,
                AbilityErrorCode.FAILED,
                f'"{ability.label}" is unavailable at the moment.',
                milliseconds=elapsed(),
            )
        )

    if not isinstance(result, AbilityResult):
        return turn.record(
            _failed(
                key,
                AbilityErrorCode.FAILED,
                f'"{ability.label}" is unavailable at the moment.',
                milliseconds=elapsed(),
            )
        )

    outcome = AbilityOutcome(
        key=key,
        ok=True,
        output=result.output,
        sources=result.sources,
        display=result.display,
        meta=result.meta,
        ends_turn=ability.ends_turn,
        milliseconds=elapsed(),
    )
    if fingerprint:
        turn.remember(fingerprint, outcome)
    return turn.record(outcome)
