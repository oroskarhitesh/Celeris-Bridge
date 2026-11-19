"""
The three stores, in memory.

These exist to be *the same behaviour* as the Mongo ones, not an easier
version. In particular they enforce the revision check and they keep published
versions append-only — because a test that passes against a store with looser
rules proves nothing about the store that ships.

Records are deep-copied on the way in and out. Without that, a caller holding
a record would see later changes, which no real store does, and a test would
pass on an aliasing bug that a database makes impossible.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from itertools import count
from typing import Any, Mapping, Sequence

from celeris_bridge.adapters.mongo.stores import AlreadyExists, MissingRecord
from celeris_bridge.domain.agent.overrides import Overrides
from celeris_bridge.domain.agent.records import (
    PausedReason,
    AgentInstance,
    AgentInstanceVersion,
    AgentTemplate,
    AgentTemplateVersion,
    Deployment,
    InstanceStatus,
    RevisionConflict,
    TemplateStatus,
)
from celeris_bridge.domain.conversation import Channel, Conversation, Turn

__all__ = [
    "InMemoryConversationStore",
    "InMemoryInstanceStore",
    "InMemoryTemplateStore",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryTemplateStore:
    """Agent types, in a dict."""

    def __init__(self) -> None:
        self._templates: dict[str, AgentTemplate] = {}
        self._versions: dict[tuple[str, int], AgentTemplateVersion] = {}

    async def get(self, key: str) -> AgentTemplate | None:
        template = self._templates.get(key)
        return deepcopy(template) if template else None

    async def list(
        self, *, status: TemplateStatus | None = None
    ) -> Sequence[AgentTemplate]:
        return [
            deepcopy(template)
            for key in sorted(self._templates)
            if (template := self._templates[key])
            and (status is None or template.status is status)
        ]

    async def create(self, template: AgentTemplate) -> AgentTemplate:
        if template.key in self._templates:
            raise AlreadyExists(f'A template called "{template.key}" already exists')
        now = _now()
        stored = replace(template, created_at=now, updated_at=now)
        self._templates[template.key] = stored
        return deepcopy(stored)

    def _revise(self, key: str, expect_revision: int, **changes: Any) -> AgentTemplate:
        template = self._templates.get(key)
        if template is None:
            raise MissingRecord(f'No template called "{key}"')
        if template.draft_revision != expect_revision:
            raise RevisionConflict(
                expected=expect_revision, found=template.draft_revision
            )
        stored = replace(
            template,
            draft_revision=template.draft_revision + 1,
            updated_at=_now(),
            **changes,
        )
        self._templates[key] = stored
        return deepcopy(stored)

    async def save_draft(
        self, key: str, draft: Mapping[str, Any], *, expect_revision: int
    ) -> AgentTemplate:
        return self._revise(
            key, expect_revision, draft=dict(draft), draft_updated_at=_now()
        )

    async def set_status(
        self, key: str, status: TemplateStatus, *, expect_revision: int
    ) -> AgentTemplate:
        return self._revise(key, expect_revision, status=status)

    async def publish(
        self, version: AgentTemplateVersion, *, expect_revision: int
    ) -> AgentTemplate:
        key = (version.template_key, version.version)
        if key in self._versions:
            raise AlreadyExists(
                f'Version {version.version} of "{version.template_key}" is already published'
            )
        published_at = version.published_at or _now()
        # The version first, matching the Mongo order and for the same reason.
        self._versions[key] = replace(version, published_at=published_at)
        return self._revise(
            version.template_key,
            expect_revision,
            latest_version=version.version,
            last_published_at=published_at,
        )

    async def version(self, key: str, version: int) -> AgentTemplateVersion | None:
        found = self._versions.get((key, version))
        return deepcopy(found) if found else None

    async def latest(self, key: str) -> AgentTemplateVersion | None:
        numbers = [number for stored, number in self._versions if stored == key]
        return await self.version(key, max(numbers)) if numbers else None

    async def versions(
        self, key: str, *, limit: int = 50
    ) -> Sequence[AgentTemplateVersion]:
        found = [
            deepcopy(version)
            for (stored, _), version in self._versions.items()
            if stored == key
        ]
        found.sort(key=lambda version: -version.version)
        return found[:limit]


class InMemoryInstanceStore:
    """Workspaces' agents, in a dict."""

    def __init__(self) -> None:
        self._instances: dict[str, AgentInstance] = {}
        self._versions: dict[tuple[str, int], AgentInstanceVersion] = {}
        self._ids = count(1)

    def _owned(self, workspace_id: str, instance_id: str) -> AgentInstance | None:
        instance = self._instances.get(instance_id)
        # The workspace is part of the lookup, not a check afterwards — the
        # same shape as the Mongo filter.
        if instance is None or instance.workspace_id != workspace_id:
            return None
        return instance

    async def get(self, workspace_id: str, instance_id: str) -> AgentInstance | None:
        instance = self._owned(workspace_id, instance_id)
        return deepcopy(instance) if instance else None

    async def list(
        self,
        workspace_id: str,
        *,
        template_key: str | None = None,
        status: InstanceStatus | None = None,
    ) -> Sequence[AgentInstance]:
        return [
            deepcopy(instance)
            for instance in self._instances.values()
            if instance.workspace_id == workspace_id
            and instance.deleted_at is None
            and (template_key is None or instance.template_key == template_key)
            and (status is None or instance.status is status)
        ]

    async def count_for_template(self, workspace_id: str, template_key: str) -> int:
        return sum(
            1
            for instance in self._instances.values()
            if instance.workspace_id == workspace_id
            and instance.template_key == template_key
            and instance.deleted_at is None
        )

    async def create(self, instance: AgentInstance) -> AgentInstance:
        now = _now()
        stored = replace(
            instance,
            id=instance.id or f"instance-{next(self._ids)}",
            created_at=now,
            updated_at=now,
        )
        self._instances[stored.id] = stored
        return deepcopy(stored)

    def _revise(
        self, workspace_id: str, instance_id: str, expect_revision: int, **changes: Any
    ) -> AgentInstance:
        instance = self._owned(workspace_id, instance_id)
        if instance is None:
            raise MissingRecord(f'No agent "{instance_id}" in this workspace')
        if instance.draft_revision != expect_revision:
            raise RevisionConflict(
                expected=expect_revision, found=instance.draft_revision
            )
        stored = replace(
            instance,
            draft_revision=instance.draft_revision + 1,
            updated_at=_now(),
            **changes,
        )
        self._instances[instance_id] = stored
        return deepcopy(stored)

    async def save_draft(
        self,
        workspace_id: str,
        instance_id: str,
        *,
        overrides: Overrides,
        expect_revision: int,
        label: str | None = None,
    ) -> AgentInstance:
        changes: dict[str, Any] = {"draft_overrides": overrides.copied()}
        if label is not None:
            changes["label"] = label
        return self._revise(workspace_id, instance_id, expect_revision, **changes)

    async def set_status(
        self,
        workspace_id: str,
        instance_id: str,
        status: InstanceStatus,
        *,
        expect_revision: int,
        reason: PausedReason | None = None,
    ) -> AgentInstance:
        changes: dict[str, Any] = {"status": status}
        if status is InstanceStatus.PAUSED:
            current = self._owned(workspace_id, instance_id)
            changes["paused_reason"] = reason
            if current is not None and current.status is not InstanceStatus.PAUSED:
                # Computed here because this is the only place that knows the
                # current status without a read-then-write.
                changes["paused_from"] = current.status
        else:
            changes["paused_reason"] = None
            changes["paused_from"] = None
        return self._revise(workspace_id, instance_id, expect_revision, **changes)

    async def save_deployment(
        self,
        workspace_id: str,
        instance_id: str,
        deployment: Deployment,
        *,
        expect_revision: int,
    ) -> AgentInstance:
        return self._revise(
            workspace_id, instance_id, expect_revision, deployment=deployment
        )

    async def pin_template_version(
        self, workspace_id: str, instance_id: str, version: int, *, expect_revision: int
    ) -> AgentInstance:
        return self._revise(
            workspace_id, instance_id, expect_revision, draft_template_version=version
        )

    async def publish(
        self, version: AgentInstanceVersion, *, expect_revision: int
    ) -> AgentInstance:
        key = (version.instance_id, version.version)
        if key in self._versions:
            raise AlreadyExists(
                f"Version {version.version} of this agent is already published"
            )
        published_at = version.published_at or _now()
        self._versions[key] = replace(
            version,
            id=version.id or f"version-{version.instance_id}-{version.version}",
            published_at=published_at,
        )
        return self._revise(
            version.workspace_id,
            version.instance_id,
            expect_revision,
            published_version=version.version,
            last_published_at=published_at,
        )

    async def soft_delete(
        self, workspace_id: str, instance_id: str, *, expect_revision: int
    ) -> AgentInstance:
        return self._revise(
            workspace_id, instance_id, expect_revision, deleted_at=_now()
        )

    async def version(
        self, workspace_id: str, instance_id: str, version: int
    ) -> AgentInstanceVersion | None:
        found = self._versions.get((instance_id, version))
        if found is None or found.workspace_id != workspace_id:
            return None
        return deepcopy(found)

    async def published(
        self, workspace_id: str, instance_id: str
    ) -> AgentInstanceVersion | None:
        instance = self._owned(workspace_id, instance_id)
        if instance is None or instance.published_version is None:
            return None
        return await self.version(workspace_id, instance_id, instance.published_version)

    async def published_many(
        self, workspace_id: str, instance_ids: Sequence[str]
    ) -> Mapping[str, AgentInstanceVersion]:
        """A loop, because a dictionary has no round trips to save.

        It does not call ``published`` -- that would make this store's call
        counts differ in shape from Mongo's, and a test that asks "how many
        reads did that take" would then measure the fake instead of the
        thing.
        """
        found: dict[str, AgentInstanceVersion] = {}
        for instance_id in instance_ids:
            instance = self._owned(workspace_id, instance_id)
            if instance is None or instance.published_version is None:
                continue
            snapshot = await self.version(
                workspace_id, instance_id, instance.published_version
            )
            if snapshot is not None:
                found[instance_id] = snapshot
        return found

    async def versions(
        self, workspace_id: str, instance_id: str, *, limit: int = 50
    ) -> Sequence[AgentInstanceVersion]:
        found = [
            deepcopy(version)
            for (stored, _), version in self._versions.items()
            if stored == instance_id and version.workspace_id == workspace_id
        ]
        found.sort(key=lambda version: -version.version)
        return found[:limit]


class InMemoryConversationStore:
    """Transcripts, in a dict."""

    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._ids = count(1)

    def _owned(self, workspace_id: str, conversation_id: str) -> Conversation | None:
        conversation = self._conversations.get(conversation_id)
        if conversation is None or conversation.workspace_id != workspace_id:
            return None
        return conversation

    async def start(self, conversation: Conversation) -> Conversation:
        now = _now()
        stored = replace(
            conversation,
            id=conversation.id or f"conversation-{next(self._ids)}",
            started_at=now,
            updated_at=now,
        )
        self._conversations[stored.id] = stored
        return deepcopy(stored)

    async def get(
        self, workspace_id: str, conversation_id: str
    ) -> Conversation | None:
        conversation = self._owned(workspace_id, conversation_id)
        return deepcopy(conversation) if conversation else None

    async def append(
        self, workspace_id: str, conversation_id: str, *turns: Turn
    ) -> Conversation:
        conversation = self._owned(workspace_id, conversation_id)
        if conversation is None:
            raise MissingRecord(f'No conversation "{conversation_id}"')
        if not turns:
            return deepcopy(conversation)
        now = _now()
        stored = replace(
            conversation,
            turns=conversation.turns
            + tuple(turn if turn.at else replace(turn, at=now) for turn in turns),
            updated_at=now,
        )
        self._conversations[conversation_id] = stored
        return deepcopy(stored)

    async def recent(
        self,
        workspace_id: str,
        *,
        instance_id: str | None = None,
        channel: Channel | None = None,
        limit: int = 50,
    ) -> Sequence[Conversation]:
        found = [
            deepcopy(conversation)
            for conversation in self._conversations.values()
            if conversation.workspace_id == workspace_id
            and (instance_id is None or conversation.instance_id == instance_id)
            and (channel is None or conversation.channel is channel)
        ]
        found.sort(key=lambda c: c.updated_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return found[:limit]
