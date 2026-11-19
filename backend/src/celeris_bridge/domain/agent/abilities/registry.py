"""
The abilities this build has.

A registry is an **object**, not module-level state. The reference keeps one
``Map`` at module scope, which means every test that registers an ability
changes what every later test sees, and the only way back is an
``unregisterAbility`` that exists solely for the tests. Here a registry is
constructed, passed where it is needed, and thrown away.

It is also a ``Mapping``, so it goes straight into the one place that asks for
``Mapping[str, AbilityContract]``::

    report = validate_definition(raw, abilities=registry)

That is the whole point of the registry as far as a definition is concerned:
``abilities: [{"key": "search_knowledge"}]`` is only publishable if this build
actually has an ability by that name, with the settings the definition sets.

This module is pure: no I/O, no database, no framework.
"""

from __future__ import annotations

from typing import Iterable, Iterator, Mapping

from celeris_bridge.domain.agent.abilities.contract import Ability

__all__ = ["AbilityRegistry", "DuplicateAbility", "UnknownAbility"]


class DuplicateAbility(ValueError):
    """Two abilities claim the same key."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f'An ability called "{key}" is already registered')


class UnknownAbility(LookupError):
    """Something asked for an ability this build does not have."""

    def __init__(self, key: str, known: Iterable[str] = ()) -> None:
        self.key = key
        listed = ", ".join(sorted(known))
        super().__init__(
            f'No ability called "{key}"' + (f" (this build has: {listed})" if listed else "")
        )


class AbilityRegistry(Mapping[str, Ability]):
    """
    The abilities available, by key.

    Read-only once built unless something calls :meth:`register`; composition
    builds one at startup, tests build their own with a fake or two.
    """

    __slots__ = ("_abilities",)

    def __init__(self, abilities: Iterable[Ability] = ()) -> None:
        self._abilities: dict[str, Ability] = {}
        for ability in abilities:
            self.register(ability)

    def register(self, ability: Ability, *, replace: bool = False) -> Ability:
        """Add an ability. Refuses a duplicate key unless asked to replace it.

        Registering is the last gate: by here the declaration has already been
        checked by :class:`Ability` itself, so there is nothing left to validate
        — only the question of whether this key is taken.
        """
        if ability.key in self._abilities and not replace:
            raise DuplicateAbility(ability.key)
        self._abilities[ability.key] = ability
        return ability

    def require(self, key: str) -> Ability:
        """The ability, or :class:`UnknownAbility`.

        Used by anything that cannot carry on without it — the runner about to
        execute a tool call. Code that merely reports on a missing ability uses
        ``.get`` instead; see ``ResolvedConfig.missing_abilities``.
        """
        try:
            return self._abilities[key]
        except KeyError:
            raise UnknownAbility(key, self._abilities) from None

    def __getitem__(self, key: str) -> Ability:
        return self._abilities[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._abilities)

    def __len__(self) -> int:
        return len(self._abilities)

    def __repr__(self) -> str:
        return f"AbilityRegistry({', '.join(sorted(self._abilities))})"
