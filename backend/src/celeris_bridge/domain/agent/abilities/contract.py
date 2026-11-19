"""
What an ability is.

An ability is one thing an agent can actually *do* — search the workspace's
knowledge, save a visitor's details, offer a time. A definition can only switch
an ability on (``AbilityRef`` in definition_schema.py); the behaviour itself is
code, declared here and registered in registry.py. That asymmetry is deliberate:
a document in the database must never be able to introduce an action.

Three audiences read one declaration:

  * **the model** gets ``ai_description``, ``instructions`` and a JSON Schema
    built from ``input``
  * **the platform admin** gets ``label`` and ``settings`` as form controls
  * **the runner** gets ``effect``, ``limits`` and ``handler``

The schema the model is given and the validator its arguments are checked
against are both built from ``input``, by the same two functions the settings
form uses. A model therefore cannot be told a shape that would then be
rejected — which is the usual cause of a tool-call retry loop that never ends.

This module is pure: no I/O, no database, no framework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.alias_generators import to_camel

from celeris_bridge.domain.agent.field_spec import (
    FieldSpec,
    FieldType,
    InvalidSettingValue,
    ItemFieldSpec,
    is_valid_setting_key,
    to_json_schema,
    validate_value,
)

__all__ = [
    "Ability",
    "AbilityContext",
    "AbilityEffect",
    "AbilityHandler",
    "AbilityInputInvalid",
    "AbilityLimits",
    "AbilityResult",
    "InputField",
    "InputProblem",
    "InvalidAbility",
    "input_json_schema",
    "input_specs",
    "validate_input",
]

ABILITY_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _where(location: Sequence[Any]) -> str:
    """A pydantic error location as something readable in a stack trace."""
    out = ""
    for part in location:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out.lstrip(".") or "(ability)"


class AbilityEffect(StrEnum):
    """Whether running it changes anything."""

    #: Looks something up. Safe to retry, safe to run twice.
    READ = "read"
    #: Changes something outside the conversation, so retries must be made safe.
    WRITE = "write"


class InvalidAbility(Exception):
    """An ability declaration is not usable. Raised at import time, by design.

    A broken ability is a programming mistake, not a runtime condition: better a
    process that refuses to start than an agent that offers a tool which fails
    on a real visitor.

    Deliberately not a ``ValueError``: pydantic turns those into a
    ``ValidationError`` when they come out of a validator, which would mean two
    different exceptions for two kinds of mistake in the same declaration.
    """

    def __init__(self, key: str, problems: Sequence[str]) -> None:
        self.key = key
        self.problems = list(problems)
        listed = "".join(f"\n  - {problem}" for problem in self.problems)
        super().__init__(f'Ability "{key}" is not usable:{listed}')


@dataclass(frozen=True)
class InputProblem:
    """One thing wrong with a model's arguments, in terms the model can act on."""

    path: str
    message: str


class AbilityInputInvalid(ValueError):
    """
    The model's arguments do not fit the ability's input.

    Carries every problem rather than the first, because the whole list goes
    back to the model as the tool result. One problem at a time turns a single
    bad call into several round trips.
    """

    def __init__(self, key: str, problems: Sequence[InputProblem]) -> None:
        self.key = key
        self.problems = list(problems)
        super().__init__(
            f'Invalid arguments for "{key}": '
            + "; ".join(
                f"{problem.path}: {problem.message}" if problem.path else problem.message
                for problem in self.problems
            )
        )


class InputField(ItemFieldSpec):
    """
    One argument the model may pass.

    A setting is configuration with a default; an input is an argument with
    neither a default nor an owner — so this is :class:`ItemFieldSpec` with
    ``required`` added, lists allowed, and ``description`` made compulsory.
    The description is the only thing telling the model what to put here, so an
    ability that omits it is an ability the model will misuse.
    """

    type: FieldType
    #: Compulsory, unlike on a setting: it is the only thing telling the model
    #: what belongs here.
    description: str = Field(min_length=1, max_length=500)
    #: Optional, unlike on a setting: nothing renders an argument as a form
    #: control, so a label has no reader. Useful in a display card or a log.
    label: str = Field(default="", max_length=120)
    #: Absent or null is rejected when true.
    required: bool = False
    #: ``list`` inputs only: the shape of one row.
    item: dict[str, ItemFieldSpec] | None = None

    @model_validator(mode="after")
    def _check_list_shape(self) -> InputField:
        if self.type == "list":
            if not self.item:
                raise ValueError('"list" inputs need an item shape')
            for name in self.item:
                if not is_valid_setting_key(name):
                    raise ValueError(f'"{name}" is not a usable item field name')
        return self


class AbilityLimits(BaseModel):
    """What the runner enforces around a handler."""

    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, alias_generator=to_camel, frozen=True
    )

    timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    #: A model that loops on one tool costs money and says nothing. Cap it.
    max_calls_per_turn: int = Field(default=3, ge=1, le=20)


@dataclass(frozen=True)
class AbilityContext:
    """
    Everything a handler is allowed to know.

    Built by the runner, never by the model. ``workspace_id`` is taken from the
    authenticated principal, so a handler cannot be steered into another
    tenant's data by anything the model writes.
    """

    workspace_id: str
    agent_instance_id: str | None = None
    conversation_id: str | None = None
    #: This ability's resolved settings — definition defaults plus workspace
    #: overrides, already merged (see overrides.resolve_config).
    settings: Mapping[str, Any] = field(default_factory=dict)
    #: The agent's own settings, for handlers that need the surrounding config.
    agent_settings: Mapping[str, Any] = field(default_factory=dict)
    #: The ports a handler may use. Deliberately untyped until ports/ exists;
    #: it becomes a typed bundle then, and nothing else here has to change.
    services: Any = None


@dataclass(frozen=True)
class AbilityResult:
    """What a handler gives back."""

    #: Goes to the model as the tool result. Keep it small: it is re-sent with
    #: every later turn of the conversation.
    output: Mapping[str, Any] = field(default_factory=dict)
    #: Citations the visitor is shown, so an answer can be traced to a source.
    sources: tuple[Mapping[str, Any], ...] = ()
    #: A card for the UI (a slot picker, a confirmation) rather than for the model.
    display: Mapping[str, Any] | None = None
    #: Telemetry. Never shown to the visitor and never sent to the model.
    meta: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class AbilityHandler(Protocol):
    """The work itself. Async because every real one waits on something."""

    async def __call__(
        self, arguments: Mapping[str, Any], context: AbilityContext
    ) -> AbilityResult: ...


class Ability(BaseModel):
    """One ability, declared in full."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
        frozen=True,
        arbitrary_types_allowed=True,
    )

    key: str = Field(pattern=ABILITY_KEY_RE.pattern)
    #: Bumped when the input shape changes, so a stored call can be read back.
    version: str = Field(pattern=VERSION_RE.pattern)

    #: For people: the platform admin and the workspace's settings screen.
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)

    #: For the model: what it does. Sent as the tool description.
    ai_description: str = Field(min_length=1, max_length=1000)
    #: For the model: when to reach for it. Added to the system prompt.
    instructions: str = Field(default="", max_length=2000)

    #: Arguments the model passes. Empty means the tool takes none.
    input: dict[str, InputField] = Field(default_factory=dict)
    #: Configuration the admin and the workspace set, exactly like a setting.
    settings: dict[str, FieldSpec] = Field(default_factory=dict)

    effect: AbilityEffect
    #: The ability produces the reply itself, so the turn stops after it.
    ends_turn: bool = False
    limits: AbilityLimits = Field(default_factory=AbilityLimits)

    handler: AbilityHandler
    #: ``write`` only: (arguments, context) -> a key that makes a retry a no-op.
    idempotency_key: Callable[[Mapping[str, Any], AbilityContext], str] | None = None
    #: Inputs that depend on the agent's configuration — which visitor details
    #: to collect, for instance. Takes the agent's settings, returns input specs.
    input_for: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None

    @model_validator(mode="wrap")
    @classmethod
    def _one_kind_of_error(cls, data: Any, handler: Any) -> Ability:
        """Every way a declaration can be wrong arrives as :class:`InvalidAbility`.

        Without this, a misspelled key raises ``ValidationError`` and a missing
        idempotency key raises ``InvalidAbility`` — two exceptions to catch for
        one class of mistake, and the caller cannot know which to expect.
        """
        try:
            return handler(data)
        except ValidationError as exc:
            key = data.get("key", "?") if isinstance(data, Mapping) else "?"
            raise InvalidAbility(
                str(key),
                [
                    f"{_where(issue['loc'])} "
                    # pydantic prefixes a custom validator's message with
                    # "Value error, ", which says nothing here.
                    + issue["msg"].removeprefix("Value error, ")
                    for issue in exc.errors()
                ],
            ) from exc

    @model_validator(mode="after")
    def _check_declaration(self) -> Ability:
        problems: list[str] = []

        for name in (*self.input, *self.settings):
            if not is_valid_setting_key(name):
                problems.append(
                    f'"{name}" is not a usable name for an input or setting'
                )

        if self.input and self.input_for is not None:
            # The reference quietly ignores the static input when a dynamic one
            # is present, which leaves a declaration that reads as if those
            # arguments exist. Say so instead.
            problems.append(
                'declares both "input" and "inputFor"; the static input would never '
                "be used, so keep whichever is true"
            )

        if self.effect is AbilityEffect.WRITE and self.idempotency_key is None:
            problems.append(
                "changes something, so it needs an idempotencyKey — a retried "
                "tool call must not act twice"
            )
        if self.effect is AbilityEffect.READ and self.idempotency_key is not None:
            problems.append(
                "only reads, so an idempotencyKey has nothing to protect; remove it"
            )
        if self.ends_turn and self.effect is AbilityEffect.WRITE:
            problems.append(
                "changes something and cannot also end the turn: the agent has to "
                "tell the visitor what happened"
            )

        if problems:
            raise InvalidAbility(self.key, problems)
        return self


def input_specs(
    ability: Ability, agent_settings: Mapping[str, Any] | None = None
) -> dict[str, InputField]:
    """
    This ability's input for one agent: fixed, or built from its settings.

    A dynamic input is rebuilt per call rather than cached, because the settings
    it is built from change the moment an admin saves the form — a cached schema
    would describe the agent as it was configured yesterday.
    """
    if ability.input_for is None:
        return dict(ability.input)

    built = ability.input_for(dict(agent_settings or {}))
    if not isinstance(built, Mapping):
        raise InvalidAbility(ability.key, ["inputFor did not return a map of inputs"])

    # A new dict every time: the ability may be returning a module-level
    # constant, and parsing must not write back into it.
    specs: dict[str, InputField] = {}
    problems: list[str] = []
    for name, raw in built.items():
        if not isinstance(name, str) or not is_valid_setting_key(name):
            problems.append(f'inputFor built "{name}", which is not a usable name')
            continue
        if isinstance(raw, InputField):
            specs[name] = raw
            continue
        try:
            specs[name] = InputField.model_validate(raw)
        except ValidationError as exc:
            problems += [
                f"inputFor built an invalid input \"{name}\": "
                f"{issue['loc'][0] if issue['loc'] else name} {issue['msg']}"
                for issue in exc.errors()
            ]
    if problems:
        raise InvalidAbility(ability.key, problems)
    return specs


def input_json_schema(
    ability: Ability, agent_settings: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The tool schema handed to the model.

    ``additionalProperties: False`` matches :func:`validate_input`, which
    rejects unknown arguments. Where the two disagree a model happily invents a
    field, is told nothing about it, and invents it again.
    """
    specs = input_specs(ability, agent_settings)
    return {
        "type": "object",
        "properties": {name: to_json_schema(spec) for name, spec in specs.items()},
        "required": [name for name, spec in specs.items() if spec.required],
        "additionalProperties": False,
    }


def validate_input(
    ability: Ability,
    arguments: Any,
    agent_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Check what the model sent and return it normalised.

    Raises :class:`AbilityInputInvalid` listing every problem. The arguments
    come from a language model, so this is treated as untrusted input in exactly
    the way a form post is — through the same validator, so the two can never
    drift apart.
    """
    specs = input_specs(ability, agent_settings)

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, Mapping):
        raise AbilityInputInvalid(
            ability.key, [InputProblem(path="", message="arguments must be an object")]
        )

    problems: list[InputProblem] = []
    clean: dict[str, Any] = {}

    for name in sorted(set(arguments) - set(specs)):
        problems.append(
            InputProblem(
                path=str(name),
                message=(
                    "is not an argument of this tool"
                    + (f"; expected: {', '.join(specs)}" if specs else "")
                ),
            )
        )

    for name, spec in specs.items():
        if name not in arguments or arguments[name] is None:
            # A model sends null for "I have nothing for this" constantly, and
            # an argument has no "null means not chosen" state the way a
            # setting does — so null is absence, and absence is only a problem
            # when the argument is required.
            if spec.required:
                problems.append(InputProblem(path=name, message="is required"))
            continue
        try:
            clean[name] = validate_value(spec, arguments[name], path=name)
        except InvalidSettingValue as exc:
            problems.append(InputProblem(path=exc.path or name, message=exc.message))

    if problems:
        raise AbilityInputInvalid(ability.key, problems)
    return clean
