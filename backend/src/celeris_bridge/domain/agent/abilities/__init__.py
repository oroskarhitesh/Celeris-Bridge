"""
Abilities: the things an agent can do, as opposed to the things it can say.

``contract.py`` says what an ability is; ``registry.py`` holds the ones this
build has. The handlers themselves will live in ``builtins/``, one file each,
and are the only part of this package that touches a port.
"""

from celeris_bridge.domain.agent.abilities.contract import (
    Ability,
    AbilityContext,
    AbilityEffect,
    AbilityHandler,
    AbilityInputInvalid,
    AbilityLimits,
    AbilityResult,
    InputField,
    InputProblem,
    InvalidAbility,
    input_json_schema,
    input_specs,
    validate_input,
)
from celeris_bridge.domain.agent.abilities.registry import (
    AbilityRegistry,
    DuplicateAbility,
    UnknownAbility,
)

__all__ = [
    "Ability",
    "AbilityContext",
    "AbilityEffect",
    "AbilityHandler",
    "AbilityInputInvalid",
    "AbilityLimits",
    "AbilityRegistry",
    "AbilityResult",
    "DuplicateAbility",
    "InputField",
    "InputProblem",
    "InvalidAbility",
    "UnknownAbility",
    "input_json_schema",
    "input_specs",
    "validate_input",
]
