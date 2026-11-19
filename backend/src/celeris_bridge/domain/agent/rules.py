"""
Cross-setting rules: checks a definition applies to a resolved config.

A single setting validates itself through its spec (field_spec.py). A rule says
something about *two or more* settings together, or about the inside of a list
setting — "the email qualification field must exist and be required to book".

Rule *types* live here, in code. A definition only picks a type and fills in its
parameters, which is what keeps definitions pure data: a new agent type cannot
introduce a new kind of check without a code change, and so cannot smuggle
arbitrary logic into the database.

Each type answers two questions:

  * ``reference_problems`` — does this rule even make sense against these
    settings? Checked once, when a definition is saved.
  * ``passes`` — do these resolved values satisfy it? Checked on every publish,
    against the definition's defaults and against each workspace's config.

Adding a type means adding a class here and listing it in ``RULE_TYPES``.
Resist adding one until a definition actually needs it.

This module is pure: no I/O, no database, no framework.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from celeris_bridge.domain.agent.field_spec import (
    FieldSpec,
    InvalidSettingValue,
    ItemFieldSpec,
    is_valid_setting_key,
    validate_value,
)

__all__ = [
    "RULE_TYPES",
    "ListRequiresItem",
    "Rule",
    "RuleBase",
    "RuleViolation",
    "evaluate_rules",
]

#: What a rule may compare against. Rules test values, they do not compute them.
Scalar = str | int | float | bool | None


@dataclass(frozen=True)
class RuleViolation:
    """A rule that resolved values do not satisfy."""

    setting: str
    message: str


class RuleBase(BaseModel, ABC):
    """Shared shape of every rule type."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
    )

    #: The setting the rule is about. Problems are reported against it, so the
    #: admin UI can show the message next to the field the admin must fix.
    setting: str = Field(min_length=1)
    #: Shown to whoever has to fix it, so it says what to do, not what failed.
    message: str = Field(min_length=1, max_length=300)

    @abstractmethod
    def reference_problems(self, settings: Mapping[str, FieldSpec]) -> list[str]:
        """Why this rule cannot be applied to these settings, as messages."""

    @abstractmethod
    def passes(self, values: Mapping[str, Any]) -> bool:
        """Whether resolved setting values satisfy this rule."""


class ListRequiresItem(RuleBase):
    """
    A list setting must contain a row matching ``match``, and that row must also
    carry the values in ``require``.

    Used to pin down a row the engine depends on without freezing the whole
    list: a workspace may reorder, relabel and extend its qualification fields,
    but cannot remove the one the booking flow reads.
    """

    type: Literal["listRequiresItem"]
    #: Which row this is about, e.g. ``{"key": "email"}``.
    match: dict[str, Scalar] = Field(min_length=1)
    #: What that row must say, e.g. ``{"requiredToBook": True}``.
    require: dict[str, Scalar] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _fields_are_identifiers(self) -> ListRequiresItem:
        bad = sorted(
            name
            for name in (*self.match, *self.require)
            if not is_valid_setting_key(name)
        )
        if bad:
            raise ValueError(f"not usable item field names: {', '.join(bad)}")
        return self

    def reference_problems(self, settings: Mapping[str, FieldSpec]) -> list[str]:
        spec = settings.get(self.setting)
        if spec is None:
            return [f'unknown setting "{self.setting}"']
        if spec.type != "list":
            return [f'"{self.setting}" is not a list setting']

        item_specs = spec.item or {}
        problems = [
            f'"{name}" is not a field of "{self.setting}" items'
            for name in (*self.match, *self.require)
            if name not in item_specs
        ]
        if problems:
            return problems

        # A rule that can never match is worse than no rule: it reports a
        # failure the admin cannot fix by editing the list.
        return [
            f'"{name}" of "{self.setting}" can never equal {value!r}'
            for name, value in (*self.match.items(), *self.require.items())
            if not _value_is_reachable(item_specs[name], value)
        ]

    def passes(self, values: Mapping[str, Any]) -> bool:
        rows = values.get(self.setting)
        if not isinstance(rows, list):
            return False
        return any(
            isinstance(row, dict)
            and all(row.get(k) == v for k, v in self.match.items())
            and all(row.get(k) == v for k, v in self.require.items())
            for row in rows
        )


def _value_is_reachable(spec: ItemFieldSpec, value: Scalar) -> bool:
    """Whether ``value`` is one a field of this spec could ever hold."""
    try:
        validate_value(spec, value)
    except InvalidSettingValue:
        return False
    return True


#: Every registered rule type. Listed so a definition's rules can be parsed and
#: so the admin UI can offer the types that exist.
RULE_TYPES: tuple[type[RuleBase], ...] = (ListRequiresItem,)

# One type today. When a second arrives this becomes a discriminated union:
#   Rule = Annotated[ListRequiresItem | NextType, Field(discriminator="type")]
Rule = ListRequiresItem


def evaluate_rules(
    rules: Sequence[RuleBase], values: Mapping[str, Any]
) -> list[RuleViolation]:
    """Which of ``rules`` the resolved ``values`` break."""
    return [
        RuleViolation(setting=rule.setting, message=rule.message)
        for rule in rules
        if not rule.passes(values)
    ]
