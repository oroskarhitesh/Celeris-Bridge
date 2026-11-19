"""
Workspace overrides: the only thing a workspace stores for its agent.

    overrides = {
      "settings":  { settingKey: value },
      "abilities": { abilityKey: { "enabled": bool, "settings": { key: value } } },
    }

A workspace never stores a copy of the definition — only where it differs from
it. Everything follows from that one rule:

  * ``resolve_config``        definition defaults + overrides -> the live config
  * ``apply_override_patch``  validate a change, then **drop anything equal to
                              the default**, so the delta stays a delta
  * ``carry_overrides``       move a delta onto another definition version,
                              dropping what no longer applies *with a reason*

Why it matters: when you publish a better default — a clearer greeting, a safer
limit — every workspace that never touched that setting gets it. If overrides
were a copy, they would all keep whatever the default was on the day they
signed up, and you could never improve anything for existing customers.

That only works if "equal to the default" is reliably detectable, which is why
``field_spec.validate_value`` normalises as it validates.

This module is pure: no I/O, no database, no framework.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from celeris_bridge.domain.agent.definition_schema import (
    AbilityContract,
    AbilityRef,
    AgentDefinition,
    LockedRule,
)
from celeris_bridge.domain.agent.field_spec import (
    FieldSpec,
    InvalidSettingValue,
    validate_value,
)
from celeris_bridge.domain.agent.rules import evaluate_rules

__all__ = [
    "AbilityOverride",
    "CarriedOverrides",
    "Dropped",
    "Overrides",
    "OverridesRejected",
    "Rejection",
    "RejectionCode",
    "ResolvedAbility",
    "ResolvedConfig",
    "apply_override_patch",
    "carry_overrides",
    "empty_overrides",
    "resolve_config",
    "reset_overrides",
]


class RejectionCode(StrEnum):
    """Why a change was refused. Stable, so a client can act on it."""

    SETTING_UNKNOWN = "SETTING_UNKNOWN"
    SETTING_LOCKED = "SETTING_LOCKED"
    SETTING_INVALID = "SETTING_INVALID"
    ABILITY_UNKNOWN = "ABILITY_UNKNOWN"
    ABILITY_INVALID = "ABILITY_INVALID"
    ABILITY_LOCKED = "ABILITY_LOCKED"
    ABILITY_SETTING_UNKNOWN = "ABILITY_SETTING_UNKNOWN"
    ABILITY_SETTING_LOCKED = "ABILITY_SETTING_LOCKED"
    ABILITY_SETTING_INVALID = "ABILITY_SETTING_INVALID"
    SETTINGS_RULE = "SETTINGS_RULE"


@dataclass(frozen=True)
class Rejection:
    """One reason a patch was refused, addressed to the field that caused it."""

    path: str
    code: RejectionCode
    message: str


class OverridesRejected(Exception):
    """
    A patch was refused, in full.

    Carries every reason, not just the first, so a form can mark all of its bad
    fields at once. The API layer turns this into a 400; nothing in here knows
    about HTTP.
    """

    def __init__(self, rejections: Sequence[Rejection]) -> None:
        self.rejections = list(rejections)
        super().__init__("; ".join(f"{r.path}: {r.message}" for r in self.rejections))


@dataclass(frozen=True)
class Dropped:
    """An override that could not come along to another definition version."""

    path: str
    reason: str


class AbilityOverride(BaseModel):
    """A workspace's changes to one ability."""

    model_config = ConfigDict(extra="forbid")

    #: ``None`` means "not overridden", so the definition's default decides.
    enabled: bool | None = None
    settings: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return self.enabled is None and not self.settings


class Overrides(BaseModel):
    """Everything a workspace has chosen differently. Nothing more."""

    model_config = ConfigDict(extra="forbid")

    settings: dict[str, Any] = Field(default_factory=dict)
    abilities: dict[str, AbilityOverride] = Field(default_factory=dict)

    def copied(self) -> Overrides:
        """A deep copy, so applying a patch cannot mutate what is stored."""
        return Overrides.model_validate(deepcopy(self.model_dump()))

    def pruned(self) -> Overrides:
        """The same overrides with empty ability entries removed."""
        return Overrides(
            settings=dict(self.settings),
            abilities={
                key: entry for key, entry in self.abilities.items() if not entry.is_empty
            },
        )


def empty_overrides() -> Overrides:
    """A workspace that has changed nothing."""
    return Overrides()


@dataclass(frozen=True)
class ResolvedAbility:
    """One ability as it will actually run."""

    key: str
    enabled: bool
    settings: dict[str, Any]


@dataclass(frozen=True)
class ResolvedConfig:
    """
    What the engine runs: no overrides, no specs, just values.

    Identity (which definition, which version) is stamped on by the caller that
    loaded them — a definition does not know its own version number.
    """

    settings: dict[str, Any]
    abilities: tuple[ResolvedAbility, ...] = ()
    locked_rules: tuple[LockedRule, ...] = ()
    #: Abilities the definition names that this build does not have. Resolving
    #: happens anyway, without them; the caller should treat a non-empty list
    #: as an alert, since a published definition referenced something real.
    missing_abilities: tuple[str, ...] = ()

    def ability(self, key: str) -> ResolvedAbility | None:
        return next((a for a in self.abilities if a.key == key), None)

    @property
    def enabled_abilities(self) -> tuple[ResolvedAbility, ...]:
        return tuple(a for a in self.abilities if a.enabled)


def _same(left: Any, right: Any) -> bool:
    """
    Whether a value is indistinguishable from a default.

    Python compares dicts and lists structurally, so unlike the JavaScript
    original this needs no stable serialisation — ``==`` is the whole test.

    The one trap is that ``True == 1`` and ``0 == False``. Every caller here
    validates first, so a type mix-up cannot arrive today; the guard stays
    because what it prevents is silent — an override deleted as "same as the
    default" — and the next caller may not validate.
    """
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return bool(left == right)


def _ability_setting_default(ref: AbilityRef, spec: FieldSpec, key: str) -> Any:
    """The definition's choice for an ability setting, else the ability's own."""
    return ref.settings[key] if key in ref.settings else spec.default


def resolve_config(
    definition: AgentDefinition,
    overrides: Overrides | None = None,
    *,
    abilities: Mapping[str, AbilityContract],
) -> ResolvedConfig:
    """
    The config a workspace's agent actually runs on.

    An override is honoured only while its setting is still ``editable``. If a
    later version locks a setting, the stored value is ignored at once rather
    than waiting for the next upgrade to clean it up — the safe direction.
    """
    chosen = overrides or Overrides()

    settings: dict[str, Any] = {}
    for key, spec in definition.settings.items():
        overridden = spec.editable and key in chosen.settings
        settings[key] = deepcopy(chosen.settings[key] if overridden else spec.default)

    resolved: list[ResolvedAbility] = []
    missing: list[str] = []
    for ref in definition.abilities:
        ability = abilities.get(ref.key)
        if ability is None:
            missing.append(ref.key)
            continue
        entry = chosen.abilities.get(ref.key) or AbilityOverride()
        enabled = (
            entry.enabled
            if ref.editable and entry.enabled is not None
            else ref.enabled_by_default
        )
        ability_settings: dict[str, Any] = {}
        for key, spec in ability.settings.items():
            overridden = spec.editable and key in entry.settings
            ability_settings[key] = deepcopy(
                entry.settings[key]
                if overridden
                else _ability_setting_default(ref, spec, key)
            )
        resolved.append(
            ResolvedAbility(key=ref.key, enabled=enabled, settings=ability_settings)
        )

    return ResolvedConfig(
        settings=settings,
        abilities=tuple(resolved),
        locked_rules=tuple(definition.locked_rules),
        missing_abilities=tuple(missing),
    )


def _ability_ref(definition: AgentDefinition, key: str) -> AbilityRef | None:
    return next((ref for ref in definition.abilities if ref.key == key), None)


def _apply_settings_patch(
    definition: AgentDefinition,
    into: Overrides,
    patch: Mapping[str, Any],
    rejections: list[Rejection],
) -> None:
    for key, value in patch.items():
        spec = definition.settings.get(key)
        path = f"settings.{key}"
        if spec is None:
            rejections.append(
                Rejection(path, RejectionCode.SETTING_UNKNOWN, f'"{key}" is not a setting of this agent')
            )
            continue
        if not spec.editable:
            rejections.append(
                Rejection(path, RejectionCode.SETTING_LOCKED, f'"{spec.label}" cannot be changed')
            )
            continue
        try:
            clean = validate_value(spec, value)
        except InvalidSettingValue as exc:
            rejections.append(
                Rejection(path, RejectionCode.SETTING_INVALID, f"{spec.label} {exc}")
            )
            continue
        # A value equal to the default is not an override at all. Storing it
        # would freeze this workspace on today's default forever.
        if _same(clean, spec.default):
            into.settings.pop(key, None)
        else:
            into.settings[key] = clean


def _apply_ability_patch(
    definition: AgentDefinition,
    into: Overrides,
    key: str,
    change: Any,
    abilities: Mapping[str, AbilityContract],
    rejections: list[Rejection],
) -> None:
    path = f"abilities.{key}"
    ref = _ability_ref(definition, key)
    ability = abilities.get(key) if ref else None
    if ref is None or ability is None:
        rejections.append(
            Rejection(path, RejectionCode.ABILITY_UNKNOWN, f'"{key}" is not an ability of this agent')
        )
        return

    if not isinstance(change, Mapping) or set(change) - {"enabled", "settings"}:
        rejections.append(
            Rejection(
                path,
                RejectionCode.ABILITY_INVALID,
                f'Use {{ enabled, settings }} to change "{ability.label}"',
            )
        )
        return

    entry = into.abilities.setdefault(key, AbilityOverride())

    if "enabled" in change:
        wanted = change["enabled"]
        if not ref.editable:
            rejections.append(
                Rejection(
                    f"{path}.enabled",
                    RejectionCode.ABILITY_LOCKED,
                    f'"{ability.label}" cannot be switched off',
                )
            )
        elif not isinstance(wanted, bool):
            rejections.append(
                Rejection(
                    f"{path}.enabled",
                    RejectionCode.ABILITY_INVALID,
                    f'"{ability.label}" must be on or off',
                )
            )
        else:
            entry.enabled = None if wanted == ref.enabled_by_default else wanted

    patch_settings = change.get("settings") or {}
    if not isinstance(patch_settings, Mapping):
        rejections.append(
            Rejection(
                f"{path}.settings",
                RejectionCode.ABILITY_INVALID,
                f'Settings for "{ability.label}" must be a map of setting to value',
            )
        )
        return

    for setting_key, value in patch_settings.items():
        spec = ability.settings.get(setting_key)
        setting_path = f"{path}.settings.{setting_key}"
        if spec is None:
            rejections.append(
                Rejection(
                    setting_path,
                    RejectionCode.ABILITY_SETTING_UNKNOWN,
                    f'"{setting_key}" is not a setting of "{ability.label}"',
                )
            )
            continue
        if not spec.editable:
            rejections.append(
                Rejection(
                    setting_path,
                    RejectionCode.ABILITY_SETTING_LOCKED,
                    f'"{spec.label}" cannot be changed',
                )
            )
            continue
        try:
            clean = validate_value(spec, value)
        except InvalidSettingValue as exc:
            rejections.append(
                Rejection(
                    setting_path,
                    RejectionCode.ABILITY_SETTING_INVALID,
                    f"{spec.label} {exc}",
                )
            )
            continue
        if _same(clean, _ability_setting_default(ref, spec, setting_key)):
            entry.settings.pop(setting_key, None)
        else:
            entry.settings[setting_key] = clean


def apply_override_patch(
    definition: AgentDefinition,
    current: Overrides | None,
    patch: Mapping[str, Any] | None = None,
    *,
    abilities: Mapping[str, AbilityContract],
) -> Overrides:
    """
    Validate a workspace's change and return the overrides it should now store.

    All or nothing: if any part is unknown, locked or invalid, nothing is
    applied and :class:`OverridesRejected` carries every reason.
    """
    into = (current or Overrides()).copied()
    patch = patch or {}
    rejections: list[Rejection] = []

    patch_settings = patch.get("settings") or {}
    if isinstance(patch_settings, Mapping):
        _apply_settings_patch(definition, into, patch_settings, rejections)
    else:
        rejections.append(
            Rejection("settings", RejectionCode.SETTING_INVALID, "Settings must be a map of setting to value")
        )

    patch_abilities = patch.get("abilities") or {}
    if isinstance(patch_abilities, Mapping):
        for key, change in patch_abilities.items():
            _apply_ability_patch(definition, into, key, change, abilities, rejections)
    else:
        rejections.append(
            Rejection("abilities", RejectionCode.ABILITY_INVALID, "Abilities must be a map of ability to change")
        )

    into = into.pruned()

    # Rules judge the whole resolved config, so they only make sense once every
    # field is known good. Reporting them alongside field errors would blame a
    # rule for a value the workspace never managed to set.
    if not rejections:
        resolved = resolve_config(definition, into, abilities=abilities)
        rejections += [
            Rejection(f"settings.{v.setting}", RejectionCode.SETTINGS_RULE, v.message)
            for v in evaluate_rules(definition.rules, resolved.settings)
        ]

    if rejections:
        raise OverridesRejected(rejections)
    return into


def reset_overrides(
    definition: AgentDefinition,
    current: Overrides | None,
    *,
    settings: Iterable[str] = (),
    abilities: Iterable[str] = (),
    everything: bool = False,
) -> Overrides:
    """Drop overrides so those settings and abilities fall back to the default."""
    if everything:
        return empty_overrides()

    into = (current or Overrides()).copied()
    rejections: list[Rejection] = []

    for key in settings:
        if key not in definition.settings:
            rejections.append(
                Rejection(
                    f"settings.{key}",
                    RejectionCode.SETTING_UNKNOWN,
                    f'"{key}" is not a setting of this agent',
                )
            )
        into.settings.pop(key, None)

    for key in abilities:
        if _ability_ref(definition, key) is None:
            rejections.append(
                Rejection(
                    f"abilities.{key}",
                    RejectionCode.ABILITY_UNKNOWN,
                    f'"{key}" is not an ability of this agent',
                )
            )
        into.abilities.pop(key, None)

    if rejections:
        raise OverridesRejected(rejections)
    return into.pruned()


@dataclass(frozen=True)
class CarriedOverrides:
    """The result of moving overrides to another definition version."""

    overrides: Overrides
    dropped: list[Dropped] = field(default_factory=list)


def _apply_renames(
    settings: dict[str, Any],
    renames: Sequence[Mapping[str, str]],
    dropped: list[Dropped],
) -> None:
    """
    Follow ``upgrade.renamedSettings`` so a renamed setting keeps its value.

    ``renames`` is one map per version stepped through, oldest first, so a
    setting renamed twice still arrives.
    """
    for mapping in renames:
        for old, new in mapping.items():
            if old not in settings:
                continue
            if new in settings:
                # Both the old and the new key hold a value. The new one is
                # what this version calls the setting, so it wins — but the
                # workspace loses an edit, and should be told.
                dropped.append(
                    Dropped(
                        path=f"settings.{old}",
                        reason=f'Replaced by "{new}", which you had also set',
                    )
                )
            else:
                settings[new] = settings[old]
            del settings[old]


def carry_overrides(
    overrides: Overrides | None,
    definition: AgentDefinition,
    *,
    abilities: Mapping[str, AbilityContract],
    renames: Sequence[Mapping[str, str]] = (),
) -> CarriedOverrides:
    """
    Move a workspace's overrides onto another definition version.

    Used for an upgrade and for a rollback. Anything the target version no
    longer allows is dropped **with a reason** — never silently, because a
    workspace that loses a setting it configured deserves to know which one.
    """
    current = (overrides or Overrides()).copied()
    dropped: list[Dropped] = []
    _apply_renames(current.settings, renames, dropped)

    kept = empty_overrides()

    for key, value in current.settings.items():
        path = f"settings.{key}"
        spec = definition.settings.get(key)
        if spec is None:
            dropped.append(Dropped(path, "This setting no longer exists"))
        elif not spec.editable:
            dropped.append(Dropped(path, f'"{spec.label}" can no longer be changed'))
        else:
            try:
                clean = validate_value(spec, value)
            except InvalidSettingValue as exc:
                dropped.append(
                    Dropped(path, f'Your value for "{spec.label}" is no longer allowed ({exc})')
                )
                continue
            if not _same(clean, spec.default):
                kept.settings[key] = clean

    for key, entry in current.abilities.items():
        ref = _ability_ref(definition, key)
        ability = abilities.get(key) if ref else None
        if ref is None or ability is None:
            dropped.append(Dropped(f"abilities.{key}", "This ability no longer exists"))
            continue

        carried = AbilityOverride()
        if entry.enabled is not None:
            if ref.editable:
                if entry.enabled != ref.enabled_by_default:
                    carried.enabled = entry.enabled
            else:
                dropped.append(
                    Dropped(
                        f"abilities.{key}.enabled",
                        f'"{ability.label}" can no longer be switched on or off',
                    )
                )

        for setting_key, value in entry.settings.items():
            path = f"abilities.{key}.settings.{setting_key}"
            spec = ability.settings.get(setting_key)
            if spec is None:
                dropped.append(Dropped(path, "This setting no longer exists"))
                continue
            if not spec.editable:
                dropped.append(Dropped(path, f'"{spec.label}" can no longer be changed'))
                continue
            try:
                clean = validate_value(spec, value)
            except InvalidSettingValue as exc:
                dropped.append(
                    Dropped(path, f'Your value for "{spec.label}" is no longer allowed ({exc})')
                )
                continue
            if not _same(clean, _ability_setting_default(ref, spec, setting_key)):
                carried.settings[setting_key] = clean

        if not carried.is_empty:
            kept.abilities[key] = carried

    # A kept edit may break a rule this version added. Drop only the setting
    # the rule names: its default is checked at publish, so falling back to it
    # is guaranteed to satisfy the rule.
    for violation in evaluate_rules(
        definition.rules, resolve_config(definition, kept, abilities=abilities).settings
    ):
        if violation.setting in kept.settings:
            del kept.settings[violation.setting]
            dropped.append(
                Dropped(
                    f"settings.{violation.setting}",
                    f"Reset to the default: {violation.message}",
                )
            )

    return CarriedOverrides(overrides=kept, dropped=dropped)
