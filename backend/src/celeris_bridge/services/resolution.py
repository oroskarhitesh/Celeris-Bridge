"""
Turning records into a runnable agent.

This is the hot path: every visitor message goes through it, so it is one
file, it does the minimum, and what it does is explicit.

Resolving live traffic is four lookups and no decisions:

1. the **instance**, which says which template and whether it is live,
2. its **published snapshot**, which holds the overrides *and* the template
   version they were published against,
3. that **template version**, which is the definition document,
4. the **config**, from the definition's defaults plus those overrides.

Two things are deliberately not here. Live traffic never reads an instance's
*draft* -- an admin halfway through editing must not be answering visitors --
and it never reads the template's *latest* version, because an instance pins
the version it was published against. A better default reaching every
workspace is the point of defaults living in code; a changed *definition*
reaching them without being opted into is how an upgrade breaks a customer's
agent at three in the morning.

**Published versions are immutable, so parsing them is cached.** Validating a
definition document is schema work on every field, and doing it per message
would be the most expensive thing in a turn. A version that cannot change
cannot go stale, so the cache needs no invalidation -- which is the only kind
of cache worth having on a path like this.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from celeris_bridge.domain.agent.definition_schema import (
    AbilityContract,
    AgentDefinition,
    validate_definition,
)
from celeris_bridge.domain.agent.engine.instructions import AbilityBrief
from celeris_bridge.domain.agent.engine.runner import enabled_tools
from celeris_bridge.domain.agent.overrides import Overrides, ResolvedConfig, resolve_config
from celeris_bridge.domain.agent.records import AgentInstance, InstanceStatus
from celeris_bridge.ports.store import InstanceStore, TemplateStore

__all__ = [
    "AgentNotLive",
    "AgentNotFound",
    "BrokenDefinition",
    "DefinitionCache",
    "ResolvedAgent",
    "resolve_draft",
    "resolve_live",
]

logger = logging.getLogger(__name__)


class AgentNotFound(LookupError):
    """No such agent in this workspace.

    The same answer for an agent that does not exist and one belonging to
    another workspace: telling the two apart would let anyone with an id
    enumerate other people's agents.
    """


class AgentNotLive(RuntimeError):
    """The agent exists but is not answering.

    Carries ``status`` and ``reason`` because the three cases need different
    words in front of a visitor: never published, paused by its owner, paused
    by us. The widget decides what to show; this decides nothing.
    """

    def __init__(self, *, status: str, reason: str = "") -> None:
        self.status = status
        self.reason = reason
        super().__init__(f"The agent is {status}" + (f" ({reason})" if reason else ""))


class BrokenDefinition(RuntimeError):
    """A published template version that no longer validates.

    Possible without anyone editing anything: an ability can be removed from a
    build, and a version published against it then references something that
    does not exist. Loud, because it means live traffic is failing and no
    amount of retrying will help.
    """

    def __init__(self, key: str, version: int, problems: list[str]) -> None:
        self.key = key
        self.version = version
        self.problems = problems
        listed = "".join(f"\n  - {problem}" for problem in problems)
        super().__init__(
            f"Template {key} version {version} no longer validates against this "
            f"build:{listed}"
        )


@dataclass(frozen=True)
class ResolvedAgent:
    """An agent, ready to take a turn."""

    instance: AgentInstance
    definition: AgentDefinition
    config: ResolvedConfig
    registry: Mapping[str, AbilityContract]
    template_key: str
    template_version: int
    #: ``None`` for a draft resolution: a draft has no published version.
    instance_version: int | None = None

    @property
    def workspace_id(self) -> str:
        return self.instance.workspace_id

    @property
    def briefs(self) -> tuple[AbilityBrief, ...]:
        """Each enabled ability's instructions, for the prompt."""
        return tuple(
            AbilityBrief(name=tool.name, instructions=tool.instructions)
            for tool in enabled_tools(self.config, self.registry)
        )


class DefinitionCache:
    """Parsed definitions, by template key and version.

    Keyed on both: a key alone would serve version 3's definition to an
    instance pinned to version 2, which is the exact failure pinning exists to
    prevent. Unbounded on purpose -- the number of published versions a process
    sees is small and they are each a few kilobytes, and an eviction policy
    here would be code with no symptom to justify it.
    """

    def __init__(self) -> None:
        self._parsed: dict[tuple[str, int], AgentDefinition] = {}
        self.hits = 0
        self.misses = 0

    def parsed(
        self,
        *,
        key: str,
        version: int,
        document: Mapping[str, Any],
        registry: Mapping[str, AbilityContract],
    ) -> AgentDefinition:
        cached = self._parsed.get((key, version))
        if cached is not None:
            self.hits += 1
            return cached

        self.misses += 1
        report = validate_definition(dict(document), abilities=registry)
        if not report.ok or report.definition is None:
            raise BrokenDefinition(
                key,
                version,
                [f"{problem.path}: {problem.message}" for problem in report.problems],
            )
        self._parsed[(key, version)] = report.definition
        return report.definition

    def clear(self) -> None:
        """Forget everything. For a test, or a build that swapped its registry."""
        self._parsed.clear()


async def resolve_live(
    *,
    workspace_id: str,
    instance_id: str,
    templates: TemplateStore,
    instances: InstanceStore,
    registry: Mapping[str, AbilityContract],
    cache: DefinitionCache | None = None,
) -> ResolvedAgent:
    """
    The agent a visitor is talking to.

    Raises :class:`AgentNotFound`, :class:`AgentNotLive` or
    :class:`BrokenDefinition`. Never falls back to a draft, a default or
    another version: an agent that is not live does not answer, and an agent
    that cannot be resolved fails rather than improvising.
    """
    instance = await instances.get(workspace_id, instance_id)
    if instance is None or instance.deleted_at is not None:
        raise AgentNotFound(f"No agent {instance_id!r} in workspace {workspace_id!r}")

    if not instance.is_live:
        raise AgentNotLive(
            status=str(instance.status),
            reason=str(instance.paused_reason or ""),
        )

    snapshot = await instances.published(workspace_id, instance_id)
    if snapshot is None:
        # ``is_live`` passed, so the pointer says published and the version is
        # missing. The store writes the version before the pointer, so this is
        # a lost write rather than a race, and it is not something to paper
        # over with the draft.
        logger.error(
            "Agent %s/%s is live but version %s is missing",
            workspace_id,
            instance_id,
            instance.published_version,
        )
        raise AgentNotLive(status="published-version-missing")

    return await _resolve(
        instance=instance,
        template_key=snapshot.template_key,
        template_version=snapshot.template_version,
        overrides=snapshot.overrides,
        instance_version=snapshot.version,
        templates=templates,
        registry=registry,
        cache=cache,
    )


async def resolve_draft(
    *,
    workspace_id: str,
    instance_id: str,
    templates: TemplateStore,
    instances: InstanceStore,
    registry: Mapping[str, AbilityContract],
    cache: DefinitionCache | None = None,
) -> ResolvedAgent:
    """
    The agent as it is being edited, for a preview.

    Resolves against the draft's overrides and its *pinned* template version,
    which is what makes a preview worth having: it shows what publishing would
    do, not what some other version would do. Status is not checked -- the
    whole point is previewing something not yet live.
    """
    instance = await instances.get(workspace_id, instance_id)
    if instance is None or instance.deleted_at is not None:
        raise AgentNotFound(f"No agent {instance_id!r} in workspace {workspace_id!r}")

    return await _resolve(
        instance=instance,
        template_key=instance.template_key,
        template_version=instance.draft_template_version,
        overrides=instance.draft_overrides,
        instance_version=None,
        templates=templates,
        registry=registry,
        cache=cache,
    )


async def _resolve(
    *,
    instance: AgentInstance,
    template_key: str,
    template_version: int,
    overrides: Overrides,
    instance_version: int | None,
    templates: TemplateStore,
    registry: Mapping[str, AbilityContract],
    cache: DefinitionCache | None,
) -> ResolvedAgent:
    stored = await templates.version(template_key, template_version)
    if stored is None:
        raise BrokenDefinition(
            template_key,
            template_version,
            ["That version of this agent type does not exist"],
        )

    cache = cache if cache is not None else DefinitionCache()
    definition = cache.parsed(
        key=template_key,
        version=template_version,
        document=stored.definition,
        registry=registry,
    )
    config = resolve_config(definition, overrides, abilities=registry)

    if config.missing_abilities:
        # Resolvable but degraded: the definition names an ability this build
        # does not have, so the agent runs with one fewer tool than it was
        # published with. Worth a line in the log every time, because the
        # symptom ("it stopped booking meetings") points nowhere near it.
        logger.warning(
            "Agent %s/%s is missing abilities this build does not have: %s",
            instance.workspace_id,
            instance.id,
            ", ".join(config.missing_abilities),
        )

    return ResolvedAgent(
        instance=instance,
        definition=definition,
        config=config,
        registry=registry,
        template_key=template_key,
        template_version=template_version,
        instance_version=instance_version,
    )
