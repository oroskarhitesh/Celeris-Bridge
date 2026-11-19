"""
The agent definition: one plain-JSON document that describes an agent type.

Marketing, email triage, support — each is a definition, not a class. The engine
is generic and reads a definition; only the *building blocks* live in code:
setting types (field_spec.py), rule types (rules.py), channels and abilities
(below, and the ability registry). A definition can only pick from those, which
is what makes a new agent type a new document rather than a new code path.

:func:`validate_definition` is the single check used in both places that matter:

  * on every draft save, to show problems next to the fields that caused them
  * on publish, which is refused unless there are none

It never raises. A definition that cannot be parsed yields problems and no
definition; one that parses yields both, so a draft can be stored with its
problems listed.

This module is pure: no I/O, no database, no framework.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.alias_generators import to_camel

from celeris_bridge.domain.agent.field_spec import (
    FieldSpec,
    InvalidSettingValue,
    is_valid_setting_key,
    validate_value,
)
from celeris_bridge.domain.agent.rules import Rule, RuleBase, evaluate_rules

__all__ = [
    "BUILTIN_PLACEHOLDERS",
    "CHANNELS",
    "MAX_DEFINITION_BYTES",
    "AbilityContract",
    "AbilityRef",
    "AgentDefinition",
    "DefinitionReport",
    "Identity",
    "Instruction",
    "InstructionCondition",
    "Limits",
    "LockedRule",
    "Problem",
    "Upgrade",
    "defaults_of",
    "empty_definition",
    "placeholders_in",
    "validate_definition",
]

#: Where an agent can run. Adding one is a code change: it needs a runtime that
#: can carry a turn, not just a line in a definition.
CHANNELS = ("web_chat", "email", "voice", "whatsapp")

#: Placeholders an instruction may use besides its definition's setting keys.
BUILTIN_PLACEHOLDERS = frozenset({"company"})

#: Ceiling on one stored definition, so a draft cannot grow without bound.
MAX_DEFINITION_BYTES = 256 * 1024

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
ABILITY_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

#: A well-formed placeholder.
PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z][a-zA-Z0-9_]*)\s*\}\}")
#: Anything that was *meant* to be one. Used to catch `{{company name}}`, which
#: the strict pattern simply does not see — so it would reach the model verbatim.
MALFORMED_PLACEHOLDER_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)

#: "is" tests equality, so it only means something for a single scalar value.
_EQUATABLE_TYPES = frozenset({"string", "enum", "boolean", "number", "url", "color"})
#: "notEmpty" only means something where a value can be empty.
_EMPTIABLE_TYPES = frozenset({"string", "url", "color", "multiselect", "stringList", "list"})

_DEFINITION_ROOT = "(definition)"


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a definition, addressed to whoever can fix it."""

    path: str
    message: str


@dataclass(frozen=True)
class DefinitionReport:
    """
    The result of checking a definition.

    ``definition`` is the parsed form, with omitted sections filled in, and is
    present whenever the structure was sound — even if semantic problems were
    found, so a draft can be saved with its problems shown.
    """

    problems: list[Problem] = field(default_factory=list)
    definition: AgentDefinition | None = None

    @property
    def ok(self) -> bool:
        """True when this definition may be published."""
        return not self.problems and self.definition is not None


@runtime_checkable
class AbilityContract(Protocol):
    """
    What :func:`validate_definition` needs to know about a registered ability.

    The real registry lives outside this module; it only has to expose its
    label and the settings it accepts, so a definition's ability defaults can
    be checked against them and rejections can name the ability a person sees.
    """

    @property
    def label(self) -> str: ...

    @property
    def settings(self) -> Mapping[str, FieldSpec]: ...


class _Section(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
    )


class Identity(_Section):
    """What the agent type is, for the catalogue a workspace picks from."""

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    icon: str = Field(default="bot", max_length=40)
    category: str = Field(default="general", max_length=60)
    channels: list[Literal[CHANNELS]] = Field(min_length=1)  # type: ignore[valid-type]

    @model_validator(mode="after")
    def _channels_are_distinct(self) -> Identity:
        if len(set(self.channels)) != len(self.channels):
            raise ValueError("lists a channel twice")
        return self


class Limits(_Section):
    """Ceilings the platform puts on a workspace using this agent type."""

    max_agents_per_workspace: int = Field(default=3, ge=1, le=100)


class InstructionCondition(_Section):
    """
    When an instruction block is included.

    Exactly one of ``is`` (the setting equals this value), ``notEmpty`` (the
    setting holds something) or ``empty`` (it does not). The point is that the
    prompt shrinks when a workspace has not filled something in, rather than
    carrying empty headings the model then tries to make sense of.

    ``empty`` exists because the other two cannot express the useful half of
    an unset setting: "there is no booking link, so offer a follow-up by email
    instead". Without it that block has to be left out of the definition, and
    the agent simply says nothing where it should say something.
    """

    setting: str = Field(min_length=1)
    # "is" is a Python keyword, so the attribute is named for what it does.
    equals: Any = Field(default=None, alias="is")
    not_empty: Literal[True] | None = None
    empty: Literal[True] | None = None

    @model_validator(mode="after")
    def _exactly_one_test(self) -> InstructionCondition:
        tests = (
            "equals" in self.model_fields_set,
            bool(self.not_empty),
            bool(self.empty),
        )
        if sum(tests) != 1:
            raise ValueError('needs exactly one of "is", "notEmpty" or "empty"')
        return self

    @property
    def tests_equality(self) -> bool:
        return "equals" in self.model_fields_set


class Instruction(_Section):
    """
    One block of the system prompt.

    ``{{settingKey}}`` becomes the setting's value and ``{{company}}`` the
    workspace's name, so the prompt is assembled from data rather than built by
    string concatenation somewhere in the engine.
    """

    id: str = Field(pattern=ID_RE.pattern)
    title: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=8000)
    when: InstructionCondition | None = None


class LockedRule(_Section):
    """
    Prompt text a workspace cannot edit, remove or talk the agent out of.

    Deliberately not templated: a locked rule has to read the same for every
    workspace, which is the whole reason it is locked. See the placeholder check
    in :func:`validate_definition`.
    """

    id: str = Field(pattern=ID_RE.pattern)
    text: str = Field(min_length=1, max_length=1000)


class AbilityRef(_Section):
    """
    A registered ability this agent type may use.

    The definition says whether it starts on and whether a workspace may change
    that. What the ability *does* is code, in the registry — a definition cannot
    introduce behaviour, only switch on behaviour that already exists.
    """

    key: str = Field(pattern=ABILITY_KEY_RE.pattern)
    enabled_by_default: bool = True
    editable: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)


class Upgrade(_Section):
    """How a workspace's overrides survive a move to a newer definition."""

    #: old key -> new key, so a rename carries the override instead of dropping it.
    renamed_settings: dict[str, str] = Field(default_factory=dict)


class AgentDefinition(_Section):
    """One agent type, in full."""

    identity: Identity
    limits: Limits = Field(default_factory=Limits)
    instructions: list[Instruction] = Field(default_factory=list, max_length=200)
    locked_rules: list[LockedRule] = Field(default_factory=list, max_length=100)
    setting_groups: list[str] = Field(default_factory=list, max_length=30)
    #: Flat: key -> spec. Never a group of groups; see _flat_settings_problems.
    settings: dict[str, FieldSpec]
    abilities: list[AbilityRef] = Field(default_factory=list, max_length=50)
    rules: list[Rule] = Field(default_factory=list, max_length=50)
    upgrade: Upgrade = Field(default_factory=Upgrade)


def placeholders_in(text: str) -> list[str]:
    """Well-formed placeholder names in ``text``, in order of appearance."""
    return [match.group(1) for match in PLACEHOLDER_RE.finditer(text)]


def _malformed_placeholders_in(text: str) -> list[str]:
    """``{{...}}`` that is not a usable placeholder, so would reach the model as-is."""
    return [
        match.group(0)
        for match in MALFORMED_PLACEHOLDER_RE.finditer(text)
        if not PLACEHOLDER_RE.fullmatch(match.group(0))
    ]


def defaults_of(definition: AgentDefinition) -> dict[str, Any]:
    """Every setting at its default: the config a workspace starts from."""
    return {key: spec.default for key, spec in definition.settings.items()}


def empty_definition(name: str = "New agent") -> dict[str, Any]:
    """A blank definition that validates, apart from the work an admin must do."""
    return {
        "identity": {
            "name": name,
            "description": "",
            "icon": "bot",
            "category": "general",
            "channels": ["web_chat"],
        },
        "limits": {"maxAgentsPerWorkspace": 3},
        "instructions": [
            {
                "id": "role",
                "title": "Who you are",
                "text": "You are the AI assistant for {{company}}.",
            }
        ],
        "lockedRules": [],
        "settingGroups": [],
        "settings": {},
        "abilities": [],
        "rules": [],
        "upgrade": {"renamedSettings": {}},
    }


def _path_of(location: Sequence[Any]) -> str:
    """A pydantic error location as a path an admin UI can point at a field."""
    out = ""
    for part in location:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out.lstrip(".") or _DEFINITION_ROOT


def _sentence(text: str) -> str:
    """Capitalise the first character only.

    ``str.capitalize`` lowercases the rest, which turns a message about
    ``"qualificationFields"`` into one about ``"qualificationfields"`` — a key
    the admin cannot find by searching.
    """
    return text[:1].upper() + text[1:]


def _duplicates(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    repeated: dict[str, None] = {}
    for value in values:
        if value in seen:
            repeated[value] = None
        seen.add(value)
    return list(repeated)


def _flat_settings_problems(raw_settings: Any) -> list[Problem]:
    """
    Catch the one shape mistake that is easy to make and hard to read.

    ``settings`` is a flat map of key to spec. Writing it as a group of groups::

        "settings": { "quickReplies": { "bookDemo": { "type": "boolean", ... } } }

    parses as a single setting called ``quickReplies`` whose spec is missing a
    label and a type, so the structural error lands two levels away from the
    actual mistake. Say what happened instead.
    """
    if not isinstance(raw_settings, dict):
        return [Problem(path="settings", message="Settings must be a map of setting key to spec")]

    problems: list[Problem] = []
    for key, spec in raw_settings.items():
        if not isinstance(spec, dict):
            problems.append(
                Problem(path=f"settings.{key}", message="A setting must be an object describing it")
            )
            continue
        if "type" in spec:
            continue
        nested = [
            name
            for name, value in spec.items()
            if isinstance(value, dict) and "type" in value
        ]
        if nested:
            problems.append(
                Problem(
                    path=f"settings.{key}",
                    message=(
                        f'Settings are a flat list, but "{key}" contains what look like '
                        f"settings ({', '.join(sorted(nested))}). Declare each one at the "
                        f'top level and use "group" to show them together'
                    ),
                )
            )
    return problems


def validate_definition(
    raw: Any,
    *,
    abilities: Mapping[str, AbilityContract],
) -> DefinitionReport:
    """
    Check a definition. Never raises.

    ``abilities`` is the registry of abilities that exist in this build. It is
    required rather than optional because an unchecked ability reference is a
    definition that publishes and then fails on a real visitor; passing ``{}``
    correctly reports every reference as unknown.
    """
    if not isinstance(raw, dict):
        return DefinitionReport(
            problems=[Problem(path=_DEFINITION_ROOT, message="A definition must be an object")]
        )

    try:
        size = len(json.dumps(raw).encode("utf-8"))
    except (TypeError, ValueError):
        return DefinitionReport(
            problems=[Problem(path=_DEFINITION_ROOT, message="A definition must be plain JSON")]
        )
    if size > MAX_DEFINITION_BYTES:
        return DefinitionReport(
            problems=[
                Problem(
                    path=_DEFINITION_ROOT,
                    message=f"This definition is too large ({size} bytes, limit {MAX_DEFINITION_BYTES})",
                )
            ]
        )

    # Run before parsing, so a mis-shaped settings map is reported as the
    # mistake it is rather than as whatever pydantic makes of it.
    if "settings" in raw:
        shape_problems = _flat_settings_problems(raw["settings"])
        if shape_problems:
            return DefinitionReport(problems=shape_problems)

    try:
        definition = AgentDefinition.model_validate(raw)
    except ValidationError as exc:
        return DefinitionReport(
            problems=[
                Problem(path=_path_of(issue["loc"]), message=issue["msg"])
                for issue in exc.errors()
            ]
        )

    problems: list[Problem] = []
    problems += _check_settings(definition)
    problems += _check_instructions(definition)
    problems += _check_locked_rules(definition)
    problems += _check_abilities(definition, abilities)
    problems += _check_rules(definition)
    problems += _check_upgrade(definition)
    return DefinitionReport(problems=problems, definition=definition)


def _check_settings(definition: AgentDefinition) -> list[Problem]:
    problems: list[Problem] = []
    groups = definition.setting_groups

    for group in _duplicates(groups):
        problems.append(Problem(path="settingGroups", message=f'Duplicate group "{group}"'))

    for key, spec in definition.settings.items():
        path = f"settings.{key}"
        if not is_valid_setting_key(key):
            problems.append(
                Problem(
                    path=path,
                    message="A setting key must start with a letter and use only letters, digits and _",
                )
            )
        # Defaults are validated by FieldSpec itself, so by here they fit.
        if groups and spec.group and spec.group not in groups:
            problems.append(
                Problem(
                    path=f"{path}.group",
                    message=f'Group "{spec.group}" is not in settingGroups',
                )
            )
        if spec.admin_only and spec.editable:
            problems.append(
                Problem(
                    path=f"{path}.adminOnly",
                    message="A setting hidden from workspaces cannot also be workspace-editable",
                )
            )
    return problems


def _check_instructions(definition: AgentDefinition) -> list[Problem]:
    problems: list[Problem] = []
    settings = definition.settings

    for duplicate in _duplicates([block.id for block in definition.instructions]):
        problems.append(
            Problem(path="instructions", message=f'Duplicate instruction id "{duplicate}"')
        )

    for index, block in enumerate(definition.instructions):
        path = f"instructions[{index}]"
        for name in placeholders_in(block.text):
            if name not in BUILTIN_PLACEHOLDERS and name not in settings:
                problems.append(
                    Problem(path=f"{path}.text", message=f"Unknown placeholder {{{{{name}}}}}")
                )
        for broken in _malformed_placeholders_in(block.text):
            problems.append(
                Problem(
                    path=f"{path}.text",
                    message=f'"{broken}" is not a usable placeholder, so it would be sent to the model as written',
                )
            )
        problems += _check_condition(block.when, f"{path}.when", settings)
    return problems


def _check_condition(
    condition: InstructionCondition | None,
    path: str,
    settings: Mapping[str, FieldSpec],
) -> list[Problem]:
    """A condition that can never fire leaves prompt text that never renders."""
    if condition is None:
        return []

    spec = settings.get(condition.setting)
    if spec is None:
        return [
            Problem(path=f"{path}.setting", message=f'Unknown setting "{condition.setting}"')
        ]

    if condition.tests_equality:
        if spec.type not in _EQUATABLE_TYPES:
            return [
                Problem(
                    path=f"{path}.is",
                    message=f'"is" cannot compare a {spec.type} setting; use "notEmpty"',
                )
            ]
        try:
            validate_value(spec, condition.equals)
        except InvalidSettingValue as exc:
            return [
                Problem(
                    path=f"{path}.is",
                    message=f'"{condition.setting}" can never hold this value, so the block would never be used ({exc.message})',
                )
            ]
        return []

    if spec.type not in _EMPTIABLE_TYPES:
        # A boolean or a number always holds something, so one of these two is
        # always true and the other never fires at all.
        test, consequence = (
            ("notEmpty", "always true") if condition.not_empty else ("empty", "never true")
        )
        return [
            Problem(
                path=f"{path}.{test}",
                message=f'A {spec.type} setting is never empty, so "{test}" is {consequence}; use "is"',
            )
        ]
    return []


def _check_locked_rules(definition: AgentDefinition) -> list[Problem]:
    problems: list[Problem] = []

    for duplicate in _duplicates([rule.id for rule in definition.locked_rules]):
        problems.append(
            Problem(path="lockedRules", message=f'Duplicate locked rule id "{duplicate}"')
        )

    for index, rule in enumerate(definition.locked_rules):
        # Locked rules are not templated: the engine appends them verbatim. A
        # placeholder here would reach the model as literal braces, which is
        # both a leak of how the prompt is built and a rule the model may not
        # follow. They are meant to read the same for every workspace anyway.
        written = [f"{{{{{name}}}}}" for name in placeholders_in(rule.text)]
        for name in written + _malformed_placeholders_in(rule.text):
            problems.append(
                Problem(
                    path=f"lockedRules[{index}].text",
                    message=(
                        "A locked rule is used as written and is the same for every "
                        f'workspace, so it cannot contain a placeholder ("{name}"). '
                        "Put workspace-specific wording in an instruction instead"
                    ),
                )
            )
    return problems


def _check_abilities(
    definition: AgentDefinition, abilities: Mapping[str, AbilityContract]
) -> list[Problem]:
    problems: list[Problem] = []

    for duplicate in _duplicates([ref.key for ref in definition.abilities]):
        problems.append(Problem(path="abilities", message=f'Duplicate ability "{duplicate}"'))

    for index, ref in enumerate(definition.abilities):
        path = f"abilities[{index}]"
        ability = abilities.get(ref.key)
        if ability is None:
            problems.append(
                Problem(path=f"{path}.key", message=f'No ability called "{ref.key}"')
            )
            continue
        for key, value in ref.settings.items():
            spec = ability.settings.get(key)
            if spec is None:
                problems.append(
                    Problem(
                        path=f"{path}.settings.{key}",
                        message=f'"{key}" is not a setting of "{ref.key}"',
                    )
                )
                continue
            try:
                validate_value(spec, value)
            except InvalidSettingValue as exc:
                problems.append(
                    Problem(path=f"{path}.settings.{key}", message=_sentence(exc.message))
                )
    return problems


def _check_rules(definition: AgentDefinition) -> list[Problem]:
    problems: list[Problem] = []
    sound: list[RuleBase] = []

    for index, rule in enumerate(definition.rules):
        reference_problems = rule.reference_problems(definition.settings)
        if reference_problems:
            problems += [
                Problem(path=f"rules[{index}]", message=_sentence(message))
                for message in reference_problems
            ]
        else:
            sound.append(rule)

    # Only rules that make sense can say anything about the defaults. A
    # definition whose own defaults break its own rules would ship an agent
    # that is invalid before a workspace touches it.
    for violation in evaluate_rules(sound, defaults_of(definition)):
        problems.append(
            Problem(
                path=f"settings.{violation.setting}.default",
                message=f"The default breaks a rule: {violation.message}",
            )
        )
    return problems


def _check_upgrade(definition: AgentDefinition) -> list[Problem]:
    problems: list[Problem] = []
    settings = definition.settings

    for old, new in definition.upgrade.renamed_settings.items():
        path = f"upgrade.renamedSettings.{old}"
        if not is_valid_setting_key(old):
            problems.append(Problem(path=path, message=f'"{old}" is not a setting key'))
        if old == new:
            problems.append(Problem(path=path, message=f'"{old}" is renamed to itself'))
            continue
        if new not in settings:
            problems.append(
                Problem(path=path, message=f'Renamed to "{new}", which is not a setting')
            )
        if old in settings:
            problems.append(
                Problem(path=path, message=f'"{old}" still exists, so it was not renamed')
            )
    return problems
