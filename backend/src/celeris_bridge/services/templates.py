"""
Agent types: drafting a definition and publishing versions of it.

One invariant lives here and justifies the module on its own: **a definition
is validated against this build's abilities before it is published, and
nothing else validates it.** A draft may be as broken as whoever is writing it
wants -- that is what a draft is for -- but a published version is what live
traffic resolves, and a version that references an ability this build does not
have is an agent that fails on a visitor rather than on the person who broke
it. Validating at the publish boundary is the only place that holds.

The second thing here is that publishing is *two* writes -- the version, then
the pointer -- and the store does them in that order with the expected
revision in the filter. This module's job is to have computed the right
version number and to have refused the publish before either write happens.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from celeris_bridge.domain.agent.definition_schema import (
    AbilityContract,
    AgentDefinition,
    DefinitionReport,
    validate_definition,
)
from celeris_bridge.domain.agent.records import (
    AgentTemplate,
    AgentTemplateVersion,
    TemplateStatus,
)
from celeris_bridge.ports.store import TemplateStore

__all__ = [
    "DefinitionInvalid",
    "TemplateNotFound",
    "TemplateService",
    "TemplateNotPublishable",
]

logger = logging.getLogger(__name__)


class TemplateNotFound(LookupError):
    """No agent type by that key."""


class DefinitionInvalid(ValueError):
    """A definition that will not publish, with every problem listed.

    Every problem and not the first, for the same reason
    :class:`~celeris_bridge.domain.agent.overrides.OverridesRejected` carries
    all of its rejections: a form should be able to mark all of its bad fields
    at once, and someone writing a definition should not fix thirty mistakes
    one round trip at a time.
    """

    def __init__(self, key: str, report: DefinitionReport) -> None:
        self.key = key
        self.problems = [(problem.path, problem.message) for problem in report.problems]
        listed = "".join(f"\n  - {path}: {message}" for path, message in self.problems)
        super().__init__(f'The definition for "{key}" cannot be published:{listed}')


class TemplateNotPublishable(RuntimeError):
    """An archived or deprecated type cannot take a new version.

    Archiving is the signal that nothing new should be built on a type, and a
    new version is the most "new" thing there is.
    """


@dataclass(frozen=True)
class DraftCheck:
    """What a draft would do if it were published."""

    ok: bool
    problems: list[tuple[str, str]]
    definition: AgentDefinition | None = None
    #: Settings the draft declares that the current version does not, and the
    #: reverse. Shown next to a publish button, because adding a setting is
    #: safe and removing one silently drops every workspace's value for it.
    added_settings: tuple[str, ...] = ()
    removed_settings: tuple[str, ...] = ()


class TemplateService:
    """Agent types, drafted and published."""

    def __init__(
        self,
        *,
        templates: TemplateStore,
        registry: Mapping[str, AbilityContract],
    ) -> None:
        self._templates = templates
        self._registry = registry

    async def get(self, key: str) -> AgentTemplate:
        template = await self._templates.get(key)
        if template is None:
            raise TemplateNotFound(f'No agent type "{key}"')
        return template

    async def list(
        self, *, status: TemplateStatus | None = None
    ) -> Sequence[AgentTemplate]:
        return await self._templates.list(status=status)

    async def create(
        self, *, key: str, draft: Mapping[str, Any] | None = None
    ) -> AgentTemplate:
        """Add an agent type. The key is permanent.

        Permanent because a workspace's agent names its template by key
        forever: reusing a key would point existing agents at a different
        agent type. The store enforces it; this is where it is said.

        There is no name argument, and that is not an omission: the name and
        description are ``identity`` in the definition document, versioned with
        everything else. A second copy on the record would be the one that
        drifts.
        """
        return await self._templates.create(
            AgentTemplate(
                key=key,
                draft=dict(draft or {}),
                status=TemplateStatus.ACTIVE,
                created_at=_now(),
            )
        )

    async def save_draft(
        self, key: str, draft: Mapping[str, Any], *, expect_revision: int
    ) -> AgentTemplate:
        """Store a draft definition, however broken it is.

        Deliberately no validation: a definition is written in several passes
        and a draft that must be valid to be saved cannot be written at all.
        The check happens at :meth:`publish`.
        """
        await self.get(key)
        return await self._templates.save_draft(
            key, dict(draft), expect_revision=expect_revision
        )

    async def check_draft(self, key: str) -> DraftCheck:
        """What publishing this draft would do. Never raises.

        Answers the question a publish button should have already asked, and
        says which settings are being added and removed -- adding one is safe,
        and removing one drops every workspace's value for it, which is the
        thing worth seeing *before* pressing the button rather than in a
        support ticket afterwards.
        """
        template = await self.get(key)
        report = validate_definition(dict(template.draft), abilities=self._registry)
        if not report.ok or report.definition is None:
            return DraftCheck(
                ok=False,
                problems=[(one.path, one.message) for one in report.problems],
            )

        current = await self._templates.latest(key)
        before: set[str] = set()
        if current is not None:
            live = validate_definition(dict(current.definition), abilities=self._registry)
            if live.definition is not None:
                before = set(live.definition.settings)
        after = set(report.definition.settings)

        return DraftCheck(
            ok=True,
            problems=[],
            definition=report.definition,
            added_settings=tuple(sorted(after - before)),
            removed_settings=tuple(sorted(before - after)),
        )

    async def publish(
        self,
        key: str,
        *,
        expect_revision: int,
        change_summary: str = "",
        published_by: str | None = None,
    ) -> AgentTemplateVersion:
        """
        Publish the current draft as the next version.

        The draft is validated here and the publish is refused if it does not
        pass -- the one check between a definition someone is writing and a
        definition a visitor's message resolves against.
        """
        template = await self.get(key)
        if not template.is_publishable_target:
            raise TemplateNotPublishable(
                f'"{key}" is {template.status} and cannot take a new version'
            )

        report = validate_definition(dict(template.draft), abilities=self._registry)
        if not report.ok or report.definition is None:
            raise DefinitionInvalid(key, report)

        version = AgentTemplateVersion(
            template_key=key,
            version=template.next_version(),
            definition=dict(template.draft),
            change_summary=change_summary,
            published_at=_now(),
            published_by=published_by,
        )
        await self._templates.publish(version, expect_revision=expect_revision)
        logger.info(
            "Published %s version %d (%s)",
            key,
            version.version,
            change_summary or "no summary",
        )
        return version

    async def version(self, key: str, version: int) -> AgentTemplateVersion:
        stored = await self._templates.version(key, version)
        if stored is None:
            raise TemplateNotFound(f'No version {version} of "{key}"')
        return stored

    async def versions(
        self, key: str, *, limit: int = 50
    ) -> Sequence[AgentTemplateVersion]:
        await self.get(key)
        return await self._templates.versions(key, limit=limit)

    async def restore_draft_from(
        self, key: str, version: int, *, expect_revision: int
    ) -> AgentTemplate:
        """Copy a published version's definition back into the draft.

        How a rollback of an agent *type* works, and why nothing here deletes
        or edits a version: the history stays a history, and going back is a
        new version that happens to carry older content. Publishing is still a
        separate, deliberate step -- restoring a draft changes nothing live.
        """
        stored = await self.version(key, version)
        return await self._templates.save_draft(
            key, dict(stored.definition), expect_revision=expect_revision
        )

    async def set_status(
        self, key: str, status: TemplateStatus, *, expect_revision: int
    ) -> AgentTemplate:
        """Archive or deprecate a type, or bring it back.

        Nothing is hard-deleted: workspaces have agents pinned to versions of
        this type, and removing it would leave them unresolvable.
        """
        await self.get(key)
        return await self._templates.set_status(key, status, expect_revision=expect_revision)


def _now() -> datetime:
    return datetime.now(timezone.utc)
