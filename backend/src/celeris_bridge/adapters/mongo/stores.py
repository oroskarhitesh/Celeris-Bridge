"""
The stores, over Mongo.

Queries only — the record/document mapping is in ``codec.py`` and tested
without a database. What is left here is the part that genuinely needs one:
filters, indexes, atomic updates.

The pattern throughout is ``find_one_and_update`` with the expected revision
**in the filter**. That is what makes a concurrent save a conflict rather than
a silent overwrite: if the revision has moved the filter does not match, the
update does not happen, and nothing was read-then-written in between.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from celeris_bridge.adapters.mongo.codec import (
    conversation_from_document,
    deployment_to_document,
    conversation_to_document,
    identifier,
    instance_from_document,
    instance_to_document,
    instance_version_from_document,
    instance_version_to_document,
    object_id,
    overrides_to_document,
    template_from_document,
    template_to_document,
    template_version_from_document,
    template_version_to_document,
    turn_to_document,
)
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
    "AlreadyExists",
    "MissingRecord",
    "MongoConversationStore",
    "MongoInstanceStore",
    "MongoTemplateStore",
    "ensure_indexes",
]

TEMPLATES = "agent_templates"
TEMPLATE_VERSIONS = "agent_template_versions"
INSTANCES = "agent_instances"
INSTANCE_VERSIONS = "agent_instance_versions"
CONVERSATIONS = "conversations"
VISITORS = "visitor_records"
PASSAGES = "knowledge_passages"


class MissingRecord(LookupError):
    """Asked to change something that is not there."""


class AlreadyExists(ValueError):
    """A key that must be unique is taken."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_indexes(database: AsyncDatabase) -> None:
    """
    Create every index this build relies on. Safe to run on every start.

    Two of these are correctness, not speed. The unique index on
    ``(templateKey, version)`` is what makes publishing twice at the same
    moment produce one version instead of two documents claiming to be v4 —
    the ``DuplicateKeyError`` is the mechanism, not an inconvenience. The same
    goes for ``(instanceId, version)``.
    """
    await database[TEMPLATE_VERSIONS].create_index(
        [("templateKey", ASCENDING), ("version", DESCENDING)], unique=True
    )
    await database[INSTANCES].create_index(
        [("workspaceId", ASCENDING), ("templateKey", ASCENDING), ("deletedAt", ASCENDING)]
    )
    await database[INSTANCE_VERSIONS].create_index(
        [("instanceId", ASCENDING), ("version", DESCENDING)], unique=True
    )
    await database[CONVERSATIONS].create_index(
        [("workspaceId", ASCENDING), ("instanceId", ASCENDING), ("updatedAt", DESCENDING)]
    )
    await database[VISITORS].create_index(
        [("workspaceId", ASCENDING), ("conversationId", ASCENDING)], unique=True
    )
    await database[PASSAGES].create_index(
        [("workspaceId", ASCENDING), ("sourceId", ASCENDING)]
    )


class MongoTemplateStore:
    """Agent types and their immutable published versions."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._templates = database[TEMPLATES]
        self._versions = database[TEMPLATE_VERSIONS]

    async def get(self, key: str) -> AgentTemplate | None:
        document = await self._templates.find_one({"_id": key})
        return template_from_document(document) if document else None

    async def list(
        self, *, status: TemplateStatus | None = None
    ) -> Sequence[AgentTemplate]:
        query: dict[str, Any] = {}
        if status is not None:
            query["status"] = str(status)
        cursor = self._templates.find(query).sort("_id", ASCENDING)
        return [template_from_document(document) async for document in cursor]

    async def create(self, template: AgentTemplate) -> AgentTemplate:
        document = template_to_document(template)
        document["createdAt"] = document["updatedAt"] = _now()
        try:
            await self._templates.insert_one(document)
        except DuplicateKeyError as clash:
            # Keys are permanent: a workspace's agent names its template by key
            # forever, so reusing one would point live agents somewhere else.
            raise AlreadyExists(f'A template called "{template.key}" already exists') from clash
        return template_from_document(document)

    async def _revise(self, key: str, expect_revision: int, changes: Mapping[str, Any]) -> AgentTemplate:
        document = await self._templates.find_one_and_update(
            {"_id": key, "draftRevision": expect_revision},
            {"$set": {**changes, "updatedAt": _now()}, "$inc": {"draftRevision": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is not None:
            return template_from_document(document)
        # The update did not match. Either the record is gone or someone else
        # saved first — and those need different answers.
        current = await self._templates.find_one({"_id": key}, {"draftRevision": 1})
        if current is None:
            raise MissingRecord(f'No template called "{key}"')
        raise RevisionConflict(
            expected=expect_revision, found=int(current.get("draftRevision", 0))
        )

    async def save_draft(
        self, key: str, draft: Mapping[str, Any], *, expect_revision: int
    ) -> AgentTemplate:
        return await self._revise(
            key, expect_revision, {"draft": dict(draft), "draftUpdatedAt": _now()}
        )

    async def set_status(
        self, key: str, status: TemplateStatus, *, expect_revision: int
    ) -> AgentTemplate:
        return await self._revise(key, expect_revision, {"status": str(status)})

    async def publish(
        self, version: AgentTemplateVersion, *, expect_revision: int
    ) -> AgentTemplate:
        published_at = version.published_at or _now()
        document = template_version_to_document(version)
        document["publishedAt"] = published_at
        try:
            # The version first, always. A failure after this leaves a version
            # nothing points at, which is recoverable from the versions
            # themselves; the other order loses a version that was announced as
            # published, which is not.
            await self._versions.insert_one(document)
        except DuplicateKeyError as clash:
            raise AlreadyExists(
                f'Version {version.version} of "{version.template_key}" is already published'
            ) from clash

        return await self._revise(
            version.template_key,
            expect_revision,
            {"latestVersion": version.version, "lastPublishedAt": published_at},
        )

    async def version(self, key: str, version: int) -> AgentTemplateVersion | None:
        document = await self._versions.find_one(
            {"templateKey": key, "version": version}
        )
        return template_version_from_document(document) if document else None

    async def latest(self, key: str) -> AgentTemplateVersion | None:
        document = await self._versions.find_one(
            {"templateKey": key}, sort=[("version", DESCENDING)]
        )
        return template_version_from_document(document) if document else None

    async def versions(
        self, key: str, *, limit: int = 50
    ) -> Sequence[AgentTemplateVersion]:
        cursor = (
            self._versions.find({"templateKey": key})
            .sort("version", DESCENDING)
            .limit(limit)
        )
        return [template_version_from_document(document) async for document in cursor]


class MongoInstanceStore:
    """Workspaces' agents and their published snapshots."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._instances = database[INSTANCES]
        self._versions = database[INSTANCE_VERSIONS]

    @staticmethod
    def _own(workspace_id: str, instance_id: str) -> dict[str, Any]:
        """The filter every single-instance query uses.

        The workspace is in the filter, not checked after the read. An id alone
        is guessable, and the check that happens after a read is the one that
        gets forgotten in the next method somebody adds.
        """
        return {"_id": object_id(instance_id), "workspaceId": workspace_id}

    async def get(self, workspace_id: str, instance_id: str) -> AgentInstance | None:
        document = await self._instances.find_one(self._own(workspace_id, instance_id))
        return instance_from_document(document) if document else None

    async def list(
        self,
        workspace_id: str,
        *,
        template_key: str | None = None,
        status: InstanceStatus | None = None,
    ) -> Sequence[AgentInstance]:
        query: dict[str, Any] = {"workspaceId": workspace_id, "deletedAt": None}
        if template_key is not None:
            query["templateKey"] = template_key
        if status is not None:
            query["status"] = str(status)
        cursor = self._instances.find(query).sort("createdAt", ASCENDING)
        return [instance_from_document(document) async for document in cursor]

    async def count_for_template(self, workspace_id: str, template_key: str) -> int:
        return await self._instances.count_documents(
            {"workspaceId": workspace_id, "templateKey": template_key, "deletedAt": None}
        )

    async def create(self, instance: AgentInstance) -> AgentInstance:
        document = instance_to_document(instance)
        document.pop("_id", None)
        document["createdAt"] = document["updatedAt"] = _now()
        result = await self._instances.insert_one(document)
        document["_id"] = result.inserted_id
        return instance_from_document(document)

    async def _revise(
        self, workspace_id: str, instance_id: str, expect_revision: int, changes: Mapping[str, Any]
    ) -> AgentInstance:
        document = await self._instances.find_one_and_update(
            {**self._own(workspace_id, instance_id), "draftRevision": expect_revision},
            {"$set": {**changes, "updatedAt": _now()}, "$inc": {"draftRevision": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is not None:
            return instance_from_document(document)
        current = await self._instances.find_one(
            self._own(workspace_id, instance_id), {"draftRevision": 1}
        )
        if current is None:
            raise MissingRecord(f'No agent "{instance_id}" in this workspace')
        raise RevisionConflict(
            expected=expect_revision, found=int(current.get("draftRevision", 0))
        )

    async def save_draft(
        self,
        workspace_id: str,
        instance_id: str,
        *,
        overrides: Overrides,
        expect_revision: int,
        label: str | None = None,
    ) -> AgentInstance:
        changes: dict[str, Any] = {"draftOverrides": overrides_to_document(overrides)}
        if label is not None:
            changes["label"] = label
        return await self._revise(workspace_id, instance_id, expect_revision, changes)

    async def set_status(
        self,
        workspace_id: str,
        instance_id: str,
        status: InstanceStatus,
        *,
        expect_revision: int,
        reason: PausedReason | None = None,
    ) -> AgentInstance:
        changes: dict[str, Any] = {"status": str(status)}
        if status is InstanceStatus.PAUSED:
            changes["pausedReason"] = str(reason) if reason else None
            current = await self.get(workspace_id, instance_id)
            if current is not None and current.status is not InstanceStatus.PAUSED:
                # Recorded so un-pausing puts the agent back where it was. An
                # extra read, on an action a person takes by hand -- the cost
                # of getting it wrong is a live agent quietly un-published.
                changes["pausedFrom"] = str(current.status)
        else:
            # Leaving these behind would have a later un-pause restore a state
            # the agent is no longer in.
            changes["pausedReason"] = None
            changes["pausedFrom"] = None
        return await self._revise(workspace_id, instance_id, expect_revision, changes)

    async def save_deployment(
        self,
        workspace_id: str,
        instance_id: str,
        deployment: Deployment,
        *,
        expect_revision: int,
    ) -> AgentInstance:
        return await self._revise(
            workspace_id,
            instance_id,
            expect_revision,
            {"deployment": deployment_to_document(deployment)},
        )

    async def pin_template_version(
        self, workspace_id: str, instance_id: str, version: int, *, expect_revision: int
    ) -> AgentInstance:
        return await self._revise(
            workspace_id, instance_id, expect_revision, {"draftTemplateVersion": version}
        )

    async def publish(
        self, version: AgentInstanceVersion, *, expect_revision: int
    ) -> AgentInstance:
        published_at = version.published_at or _now()
        document = instance_version_to_document(version)
        document.pop("_id", None)
        document["publishedAt"] = published_at
        try:
            await self._versions.insert_one(document)
        except DuplicateKeyError as clash:
            raise AlreadyExists(
                f"Version {version.version} of this agent is already published"
            ) from clash

        return await self._revise(
            version.workspace_id,
            version.instance_id,
            expect_revision,
            {"publishedVersion": version.version, "lastPublishedAt": published_at},
        )

    async def soft_delete(
        self, workspace_id: str, instance_id: str, *, expect_revision: int
    ) -> AgentInstance:
        # Marked, not removed: a deleted agent still has conversations and
        # leads that someone may need to read.
        return await self._revise(
            workspace_id, instance_id, expect_revision, {"deletedAt": _now()}
        )

    async def version(
        self, workspace_id: str, instance_id: str, version: int
    ) -> AgentInstanceVersion | None:
        document = await self._versions.find_one(
            {
                "workspaceId": workspace_id,
                "instanceId": object_id(instance_id),
                "version": version,
            }
        )
        return instance_version_from_document(document) if document else None

    async def published(
        self, workspace_id: str, instance_id: str
    ) -> AgentInstanceVersion | None:
        instance = await self.get(workspace_id, instance_id)
        if instance is None or instance.published_version is None:
            return None
        return await self.version(workspace_id, instance_id, instance.published_version)

    async def published_many(
        self, workspace_id: str, instance_ids: Sequence[str]
    ) -> Mapping[str, AgentInstanceVersion]:
        """Two queries whatever the number of agents.

        One to learn which version each agent points at, one to fetch those
        versions. Per-agent reads would be a query per row of a list screen,
        which is the shape of slowness that only appears once a customer has
        twenty agents.
        """
        if not instance_ids:
            return {}
        pointers = self._instances.find(
            {
                "_id": {"$in": [object_id(one) for one in instance_ids]},
                "workspaceId": workspace_id,
            },
            {"publishedVersion": 1},
        )
        wanted = [
            {"instanceId": document["_id"], "version": document["publishedVersion"]}
            async for document in pointers
            if document.get("publishedVersion") is not None
        ]
        if not wanted:
            return {}
        # The workspace stays in the filter even though the pointers were
        # already read through it: a query that could return another tenant's
        # document is one refactor away from doing so.
        cursor = self._versions.find({"workspaceId": workspace_id, "$or": wanted})
        return {
            str(document["instanceId"]): instance_version_from_document(document)
            async for document in cursor
        }

    async def versions(
        self, workspace_id: str, instance_id: str, *, limit: int = 50
    ) -> Sequence[AgentInstanceVersion]:
        cursor = (
            self._versions.find(
                {"workspaceId": workspace_id, "instanceId": object_id(instance_id)}
            )
            .sort("version", DESCENDING)
            .limit(limit)
        )
        return [instance_version_from_document(document) async for document in cursor]


class MongoConversationStore:
    """Transcripts."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._conversations = database[CONVERSATIONS]

    async def start(self, conversation: Conversation) -> Conversation:
        document = conversation_to_document(conversation)
        document.pop("_id", None)
        document["startedAt"] = document["updatedAt"] = _now()
        result = await self._conversations.insert_one(document)
        document["_id"] = result.inserted_id
        return conversation_from_document(document)

    async def get(
        self, workspace_id: str, conversation_id: str
    ) -> Conversation | None:
        document = await self._conversations.find_one(
            {"_id": object_id(conversation_id), "workspaceId": workspace_id}
        )
        return conversation_from_document(document) if document else None

    async def append(
        self, workspace_id: str, conversation_id: str, *turns: Turn
    ) -> Conversation:
        if not turns:
            existing = await self.get(workspace_id, conversation_id)
            if existing is None:
                raise MissingRecord(f'No conversation "{conversation_id}"')
            return existing

        now = _now()
        document = await self._conversations.find_one_and_update(
            {"_id": object_id(conversation_id), "workspaceId": workspace_id},
            {
                # $push, not a whole-document write: a transcript only grows,
                # and writing it back whole lets one slow turn overwrite a turn
                # that arrived while it was thinking.
                "$push": {
                    "turns": {
                        "$each": [
                            turn_to_document(
                                turn if turn.at else _stamped(turn, now)
                            )
                            for turn in turns
                        ]
                    }
                },
                "$set": {"updatedAt": now},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise MissingRecord(f'No conversation "{conversation_id}"')
        return conversation_from_document(document)

    async def recent(
        self,
        workspace_id: str,
        *,
        instance_id: str | None = None,
        channel: Channel | None = None,
        limit: int = 50,
    ) -> Sequence[Conversation]:
        query: dict[str, Any] = {"workspaceId": workspace_id}
        if instance_id is not None:
            query["instanceId"] = object_id(instance_id)
        if channel is not None:
            query["channel"] = str(channel)
        cursor = (
            self._conversations.find(query).sort("updatedAt", DESCENDING).limit(limit)
        )
        return [conversation_from_document(document) async for document in cursor]


def _stamped(turn: Turn, at: datetime) -> Turn:
    """A turn with the time it was stored, when the caller did not set one."""
    return replace(turn, at=at)
