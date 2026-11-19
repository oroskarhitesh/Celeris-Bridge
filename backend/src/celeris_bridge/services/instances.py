"""
A workspace's agents: created, edited, published, upgraded, rolled back.

Three rules shape every method here.

**An instance stores only what differs.** A patch goes through
``apply_override_patch``, which refuses anything unknown, locked or invalid
and stores a value only when it is not the default. That is what makes a
better default reach every workspace that never touched the setting -- and
what makes "reset" a deletion rather than a write.

**An instance pins a template version.** Editing settings never moves the pin.
Moving it is :meth:`upgrade`, which is an explicit act, carries the overrides
across with :func:`carry_overrides`, and reports what the new version would no
longer accept *before* anything is written.

**Publishing is a snapshot, and a rollback is a new snapshot.** No version is
ever edited or deleted. Going back to version 3 publishes version 7 carrying
version 3's overrides, with ``rolled_back_from`` recording where it came from
-- so the history reads as what happened rather than as what someone wishes
had happened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from celeris_bridge.domain.agent.definition_schema import (
    AbilityContract,
    AgentDefinition,
    validate_definition,
)
from celeris_bridge.domain.agent.overrides import (
    Dropped,
    Overrides,
    ResolvedConfig,
    apply_override_patch,
    carry_overrides,
    reset_overrides,
    resolve_config,
)
from celeris_bridge.domain.agent.records import (
    AgentInstance,
    AgentInstanceVersion,
    Deployment,
    InstanceStatus,
    PausedReason,
)
from celeris_bridge.ports.store import InstanceStore, TemplateStore
from celeris_bridge.services.resolution import (
    AgentNotFound,
    BrokenDefinition,
    DefinitionCache,
)

__all__ = [
    "InstanceLimitReached",
    "InstanceService",
    "NothingToPublish",
    "UpgradePreview",
]

logger = logging.getLogger(__name__)


class InstanceLimitReached(RuntimeError):
    """The definition's own cap on how many of this type a workspace may have."""

    def __init__(self, template_key: str, limit: int) -> None:
        self.template_key = template_key
        self.limit = limit
        super().__init__(
            f"This workspace already has {limit} {template_key} agent(s), "
            "which is the limit."
        )


class NothingToPublish(RuntimeError):
    """Publish was asked for and the draft matches what is already live.

    Refused rather than allowed, because a version is a line in a history
    someone reads: a list of twenty identical versions is a list nobody can
    find anything in.
    """


@dataclass(frozen=True)
class UpgradePreview:
    """What moving to another template version would cost.

    Computed without writing anything, so an upgrade can be shown and then
    declined. ``dropped`` is the list that matters: a workspace about to lose
    a setting it configured should see which one before agreeing, not
    afterwards.
    """

    from_version: int
    to_version: int
    dropped: tuple[Dropped, ...] = ()
    added_settings: tuple[str, ...] = ()

    @property
    def loses_nothing(self) -> bool:
        return not self.dropped


class InstanceService:
    """One workspace's agents."""

    def __init__(
        self,
        *,
        instances: InstanceStore,
        templates: TemplateStore,
        registry: Mapping[str, AbilityContract],
        cache: DefinitionCache | None = None,
    ) -> None:
        self._instances = instances
        self._templates = templates
        self._registry = registry
        self._cache = cache if cache is not None else DefinitionCache()

    # -- reading ---------------------------------------------------------

    async def get(self, workspace_id: str, instance_id: str) -> AgentInstance:
        instance = await self._instances.get(workspace_id, instance_id)
        if instance is None or instance.deleted_at is not None:
            # The same answer for "does not exist" and "belongs to someone
            # else": distinguishing them lets anyone with an id enumerate
            # other people's agents.
            raise AgentNotFound(f"No agent {instance_id!r} in workspace {workspace_id!r}")
        return instance

    async def list(
        self,
        workspace_id: str,
        *,
        template_key: str | None = None,
        status: InstanceStatus | None = None,
    ) -> Sequence[AgentInstance]:
        return await self._instances.list(
            workspace_id, template_key=template_key, status=status
        )

    async def unpublished(self, workspace_id: str, instance: AgentInstance) -> bool:
        """Whether this draft differs from what visitors are being served.

        The question a settings screen has to answer before it can warn
        anybody, and the reason it is here rather than on the record is that
        answering it needs the published snapshot -- which is a read, and a
        record cannot do a read. See ``AgentInstance.differs_from``.
        """
        snapshot = await self._instances.published(workspace_id, instance.id)
        return instance.differs_from(snapshot)

    async def unpublished_many(
        self, workspace_id: str, instances: Sequence[AgentInstance]
    ) -> Mapping[str, bool]:
        """The same answer for a list, in two queries instead of two per row."""
        snapshots = await self._instances.published_many(
            workspace_id, [one.id for one in instances]
        )
        return {
            one.id: one.differs_from(snapshots.get(one.id)) for one in instances
        }

    async def settings_form(
        self, workspace_id: str, instance_id: str
    ) -> tuple[AgentDefinition, ResolvedConfig]:
        """The definition and the resolved config, for an admin screen.

        Both, because the form needs the *declaration* to draw a control and
        the resolved value to fill it in -- and because the single most
        important thing a settings screen can show is which values are the
        workspace's own and which are the defaults it is inheriting.
        """
        instance = await self.get(workspace_id, instance_id)
        definition = await self._definition(
            instance.template_key, instance.draft_template_version
        )
        config = resolve_config(
            definition, instance.draft_overrides, abilities=self._registry
        )
        return definition, config

    # -- writing ---------------------------------------------------------

    async def create(
        self,
        *,
        workspace_id: str,
        template_key: str,
        label: str,
        template_version: int | None = None,
        patch: Mapping[str, Any] | None = None,
    ) -> AgentInstance:
        """
        Add an agent, pinned to a template version.

        The latest version by default, and *that pin is the only thing live
        traffic will follow afterwards*. Created as a draft: an agent appears
        when its owner publishes it, not when they name it.
        """
        latest = await self._templates.latest(template_key)
        if latest is None:
            raise BrokenDefinition(
                template_key, 0, ["This agent type has no published version yet"]
            )
        version = template_version if template_version is not None else latest.version
        definition = await self._definition(template_key, version)

        limit = definition.limits.max_agents_per_workspace
        if limit:
            existing = await self._instances.count_for_template(workspace_id, template_key)
            if existing >= limit:
                raise InstanceLimitReached(template_key, limit)

        # Through the real validator, so a value set at creation is refused for
        # exactly the reasons one set later would be. There is no back door
        # that accepts something the product would not.
        overrides = apply_override_patch(
            definition, None, patch, abilities=self._registry
        )

        return await self._instances.create(
            AgentInstance(
                id="",
                workspace_id=workspace_id,
                template_key=template_key,
                label=label,
                draft_template_version=version,
                draft_overrides=overrides,
                status=InstanceStatus.DRAFT,
                created_at=_now(),
            )
        )

    async def edit(
        self,
        workspace_id: str,
        instance_id: str,
        patch: Mapping[str, Any],
        *,
        expect_revision: int,
        label: str | None = None,
    ) -> AgentInstance:
        """
        Change settings on the draft.

        All or nothing: ``apply_override_patch`` raises ``OverridesRejected``
        with every reason and nothing is written. The draft, not the published
        snapshot -- an admin typing in a form is not changing what visitors
        are talking to.
        """
        instance = await self.get(workspace_id, instance_id)
        definition = await self._definition(
            instance.template_key, instance.draft_template_version
        )
        overrides = apply_override_patch(
            definition, instance.draft_overrides, patch, abilities=self._registry
        )
        return await self._instances.save_draft(
            workspace_id,
            instance_id,
            overrides=overrides,
            expect_revision=expect_revision,
            label=label,
        )

    async def reset(
        self,
        workspace_id: str,
        instance_id: str,
        *,
        expect_revision: int,
        settings: Sequence[str] = (),
        abilities: Sequence[str] = (),
        everything: bool = False,
    ) -> AgentInstance:
        """Drop overrides so those settings fall back to the default.

        A deletion and not a write of the current default, which is the whole
        point: a setting that has been reset follows the default *from now
        on*, including when a later version improves it.
        """
        instance = await self.get(workspace_id, instance_id)
        definition = await self._definition(
            instance.template_key, instance.draft_template_version
        )
        overrides = reset_overrides(
            definition,
            instance.draft_overrides,
            settings=settings,
            abilities=abilities,
            everything=everything,
        )
        return await self._instances.save_draft(
            workspace_id, instance_id, overrides=overrides, expect_revision=expect_revision
        )

    async def publish(
        self,
        workspace_id: str,
        instance_id: str,
        *,
        expect_revision: int,
        change_summary: str = "",
        published_by: str | None = None,
    ) -> AgentInstanceVersion:
        """
        Snapshot the draft, and make it what visitors talk to.

        The draft is resolved first, so a draft that cannot be resolved is
        refused here rather than at a visitor's first message.
        """
        instance = await self.get(workspace_id, instance_id)
        definition = await self._definition(
            instance.template_key, instance.draft_template_version
        )
        # Resolving is the check: it is what live traffic will do, so if it
        # raises now it would have raised then.
        resolve_config(definition, instance.draft_overrides, abilities=self._registry)

        # The same comparison the API reports as "unpublished changes", and
        # deliberately the same code: if publishing refuses because nothing
        # differs, no screen should be claiming something does. See
        # ``AgentInstance.differs_from``.
        current = await self._instances.published(workspace_id, instance_id)
        if current is not None and not instance.differs_from(current):
            raise NothingToPublish(
                f"Version {current.version} already matches this draft"
            )

        version = AgentInstanceVersion(
            id="",
            workspace_id=workspace_id,
            instance_id=instance_id,
            version=instance.next_version(),
            template_key=instance.template_key,
            template_version=instance.draft_template_version,
            overrides=instance.draft_overrides,
            change_summary=change_summary,
            published_at=_now(),
            published_by=published_by,
        )
        published = await self._instances.publish(version, expect_revision=expect_revision)
        if published.status is InstanceStatus.DRAFT:
            # Publishing a draft is what makes it live. Only a draft: an agent
            # its owner paused must not come back because someone pressed
            # publish, and a live one is already live.
            await self._instances.set_status(
                workspace_id,
                instance_id,
                InstanceStatus.LIVE,
                expect_revision=published.draft_revision,
            )
        logger.info(
            "Published agent %s/%s version %d on template %s v%d",
            workspace_id,
            instance_id,
            version.version,
            instance.template_key,
            instance.draft_template_version,
        )
        return version

    async def rollback(
        self,
        workspace_id: str,
        instance_id: str,
        *,
        to_version: int,
        expect_revision: int,
        published_by: str | None = None,
    ) -> AgentInstanceVersion:
        """
        Go back to an earlier published version.

        By publishing a *new* version carrying the old overrides, with
        ``rolled_back_from`` set. Nothing is edited or deleted: a history that
        can be rewritten is not a history, and "we rolled back on Tuesday" is
        the fact someone will need.

        The old overrides are carried through :func:`carry_overrides` because
        the earlier version may have been published against a different
        template version and may hold a value this one no longer accepts.
        """
        instance = await self.get(workspace_id, instance_id)
        earlier = await self._instances.version(workspace_id, instance_id, to_version)
        if earlier is None:
            raise AgentNotFound(f"No version {to_version} of agent {instance_id!r}")

        definition = await self._definition(
            instance.template_key, instance.draft_template_version
        )
        carried = carry_overrides(
            earlier.overrides,
            definition,
            abilities=self._registry,
            renames=await self._renames(
                instance.template_key, earlier.template_version, instance.draft_template_version
            ),
        )
        if carried.dropped:
            logger.warning(
                "Rolling %s/%s back to version %d drops: %s",
                workspace_id,
                instance_id,
                to_version,
                "; ".join(f"{one.path} ({one.reason})" for one in carried.dropped),
            )

        version = AgentInstanceVersion(
            id="",
            workspace_id=workspace_id,
            instance_id=instance_id,
            version=instance.next_version(),
            template_key=instance.template_key,
            template_version=instance.draft_template_version,
            overrides=carried.overrides,
            change_summary=f"Rolled back to version {to_version}",
            rolled_back_from=to_version,
            published_at=_now(),
            published_by=published_by,
        )
        await self._instances.publish(version, expect_revision=expect_revision)
        # The draft follows, or the next publish would undo the rollback.
        await self._instances.save_draft(
            workspace_id,
            instance_id,
            overrides=carried.overrides,
            expect_revision=expect_revision + 1,
        )
        return version

    # -- moving between template versions --------------------------------

    async def preview_upgrade(
        self, workspace_id: str, instance_id: str, *, to_version: int | None = None
    ) -> UpgradePreview:
        """What an upgrade would drop. Writes nothing.

        Separate from :meth:`upgrade` on purpose: a workspace that is about to
        lose a setting it configured should be shown which one and allowed to
        say no.
        """
        instance = await self.get(workspace_id, instance_id)
        target = to_version if to_version is not None else await self._latest_version(
            instance.template_key
        )
        current = await self._definition(
            instance.template_key, instance.draft_template_version
        )
        later = await self._definition(instance.template_key, target)
        carried = carry_overrides(
            instance.draft_overrides,
            later,
            abilities=self._registry,
            renames=await self._renames(
                instance.template_key, instance.draft_template_version, target
            ),
        )
        return UpgradePreview(
            from_version=instance.draft_template_version,
            to_version=target,
            dropped=tuple(carried.dropped),
            added_settings=tuple(sorted(set(later.settings) - set(current.settings))),
        )

    async def upgrade(
        self,
        workspace_id: str,
        instance_id: str,
        *,
        expect_revision: int,
        to_version: int | None = None,
    ) -> AgentInstance:
        """
        Move the draft onto a newer template version.

        Explicit, and it changes nothing live until the instance is published
        again -- which is the whole reason an instance pins a version rather
        than following the latest. A definition arriving unasked is how an
        upgrade breaks a customer's agent overnight.
        """
        instance = await self.get(workspace_id, instance_id)
        target = to_version if to_version is not None else await self._latest_version(
            instance.template_key
        )
        later = await self._definition(instance.template_key, target)
        carried = carry_overrides(
            instance.draft_overrides,
            later,
            abilities=self._registry,
            renames=await self._renames(
                instance.template_key, instance.draft_template_version, target
            ),
        )

        moved = await self._instances.pin_template_version(
            workspace_id, instance_id, target, expect_revision=expect_revision
        )
        return await self._instances.save_draft(
            workspace_id,
            instance_id,
            overrides=carried.overrides,
            expect_revision=moved.draft_revision,
        )

    # -- status and deployment -------------------------------------------

    async def pause(
        self,
        workspace_id: str,
        instance_id: str,
        *,
        expect_revision: int,
        reason: PausedReason | None = None,
    ) -> AgentInstance:
        """Stop answering, remembering what to go back to.

        The store records the status it was paused *from*, so resuming a
        paused draft does not accidentally publish it and resuming a paused
        live agent does not quietly leave it unpublished.

        ``reason`` is for a pause the platform applied and has to explain. An
        owner pausing their own agent passes none.
        """
        await self.get(workspace_id, instance_id)
        return await self._instances.set_status(
            workspace_id,
            instance_id,
            InstanceStatus.PAUSED,
            expect_revision=expect_revision,
            reason=reason,
        )

    async def resume(
        self, workspace_id: str, instance_id: str, *, expect_revision: int
    ) -> AgentInstance:
        instance = await self.get(workspace_id, instance_id)
        resumed = instance.resumed()
        if resumed.status is instance.status:
            return instance
        return await self._instances.set_status(
            workspace_id, instance_id, resumed.status, expect_revision=expect_revision
        )

    async def set_deployment(
        self,
        workspace_id: str,
        instance_id: str,
        deployment: Deployment,
        *,
        expect_revision: int,
    ) -> AgentInstance:
        """Where the agent may appear.

        Unversioned and immediate, unlike everything else here. Deliberate:
        the allowed origins are a security control, and taking a domain off
        the list has to take effect now rather than at the next publish.
        """
        await self.get(workspace_id, instance_id)
        return await self._instances.save_deployment(
            workspace_id, instance_id, deployment, expect_revision=expect_revision
        )

    async def delete(
        self, workspace_id: str, instance_id: str, *, expect_revision: int
    ) -> AgentInstance:
        """Mark it deleted. Nothing is removed.

        A deleted agent still has conversations, leads and a transcript
        someone may need to read, and a visitor mid-conversation gets a
        refusal rather than a stack trace.
        """
        await self.get(workspace_id, instance_id)
        return await self._instances.soft_delete(
            workspace_id, instance_id, expect_revision=expect_revision
        )

    async def versions(
        self, workspace_id: str, instance_id: str, *, limit: int = 50
    ) -> Sequence[AgentInstanceVersion]:
        await self.get(workspace_id, instance_id)
        return await self._instances.versions(workspace_id, instance_id, limit=limit)

    # -- internals -------------------------------------------------------

    async def _definition(self, template_key: str, version: int) -> AgentDefinition:
        stored = await self._templates.version(template_key, version)
        if stored is None:
            raise BrokenDefinition(
                template_key, version, ["That version of this agent type does not exist"]
            )
        return self._cache.parsed(
            key=template_key,
            version=version,
            document=stored.definition,
            registry=self._registry,
        )

    async def _latest_version(self, template_key: str) -> int:
        latest = await self._templates.latest(template_key)
        if latest is None:
            raise BrokenDefinition(
                template_key, 0, ["This agent type has no published version yet"]
            )
        return latest.version

    async def _renames(
        self, template_key: str, from_version: int, to_version: int
    ) -> list[Mapping[str, str]]:
        """Every ``upgrade.renamedSettings`` map between two versions.

        One per version stepped through, oldest first, so a setting renamed in
        version 4 and again in version 6 still arrives with its value --
        rather than being dropped twice and reported as two lost settings.
        Empty when going backwards: a rollback cannot un-rename, and guessing
        the inverse would put a value under a key the older version never had.
        """
        if to_version <= from_version:
            return []
        maps: list[Mapping[str, str]] = []
        for step in range(from_version + 1, to_version + 1):
            stored = await self._templates.version(template_key, step)
            if stored is None:
                continue
            report = validate_definition(dict(stored.definition), abilities=self._registry)
            if report.definition is not None:
                renamed = report.definition.upgrade.renamed_settings
                if renamed:
                    maps.append(dict(renamed))
        return maps


def _now() -> datetime:
    return datetime.now(timezone.utc)
