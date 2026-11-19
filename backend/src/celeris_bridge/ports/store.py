"""
Ports for the records the platform keeps.

Three stores, one file, because they are one subject: the four-level hierarchy
and the conversations that run against it.

Two rules shape every signature here.

**A save states the revision it is based on.** Two admins with the same form
open, both pressing save, is a Tuesday. A store that takes the whole record and
writes it has no way to notice, so the second save silently discards the first.
Every mutating method takes ``expect_revision`` and raises ``RevisionConflict``.

**Published versions are append-only.** Nothing here updates or deletes a
version. A rollback publishes a new version that happens to carry older
overrides; the history is a history.
"""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence, runtime_checkable

from celeris_bridge.domain.agent.overrides import Overrides
from celeris_bridge.domain.agent.records import (
    AgentInstance,
    AgentInstanceVersion,
    AgentTemplate,
    AgentTemplateVersion,
    Deployment,
    InstanceStatus,
    PausedReason,
    TemplateStatus,
)
from celeris_bridge.domain.conversation import Channel, Conversation, Turn

__all__ = [
    "ConversationStore",
    "InstanceStore",
    "TemplateStore",
]


@runtime_checkable
class TemplateStore(Protocol):
    """Agent types and their published versions."""

    async def get(self, key: str) -> AgentTemplate | None: ...

    async def list(
        self, *, status: TemplateStatus | None = None
    ) -> Sequence[AgentTemplate]: ...

    async def create(self, template: AgentTemplate) -> AgentTemplate:
        """Add a template. Fails if the key is taken — keys are permanent.

        A workspace's agent names its template by key forever, so reusing one
        would point existing agents at a different agent type.
        """
        ...

    async def save_draft(
        self, key: str, draft: Mapping[str, object], *, expect_revision: int
    ) -> AgentTemplate:
        """Replace the draft, raising ``RevisionConflict`` if it has moved."""
        ...

    async def set_status(
        self, key: str, status: TemplateStatus, *, expect_revision: int
    ) -> AgentTemplate: ...

    async def publish(
        self, version: AgentTemplateVersion, *, expect_revision: int
    ) -> AgentTemplate:
        """
        Write a new version and point the template at it.

        Two writes, no transaction, and the order matters: the **version is
        written first**. A failure between them leaves a published version
        nothing points at, which is recoverable — the pointer is derivable from
        the versions. The other order loses a version that was announced as
        published, which is not.
        """
        ...

    async def version(self, key: str, version: int) -> AgentTemplateVersion | None: ...

    async def latest(self, key: str) -> AgentTemplateVersion | None: ...

    async def versions(
        self, key: str, *, limit: int = 50
    ) -> Sequence[AgentTemplateVersion]:
        """Newest first."""
        ...


@runtime_checkable
class InstanceStore(Protocol):
    """Workspaces' agents and their published snapshots."""

    async def get(self, workspace_id: str, instance_id: str) -> AgentInstance | None:
        """
        One agent, if this workspace owns it.

        The workspace is a parameter and not a filter the caller may forget:
        an id alone is guessable, and a store method that takes only an id is
        the one that eventually gets called with a request body's value.
        """
        ...

    async def list(
        self,
        workspace_id: str,
        *,
        template_key: str | None = None,
        status: InstanceStatus | None = None,
    ) -> Sequence[AgentInstance]: ...

    async def count_for_template(self, workspace_id: str, template_key: str) -> int:
        """How many a workspace has, for the definition's own limit."""
        ...

    async def create(self, instance: AgentInstance) -> AgentInstance: ...

    async def save_draft(
        self,
        workspace_id: str,
        instance_id: str,
        *,
        overrides: Overrides,
        expect_revision: int,
        label: str | None = None,
    ) -> AgentInstance:
        """Replace the draft overrides, raising ``RevisionConflict`` if stale."""
        ...

    async def set_status(
        self,
        workspace_id: str,
        instance_id: str,
        status: InstanceStatus,
        *,
        expect_revision: int,
        reason: PausedReason | None = None,
    ) -> AgentInstance:
        """
        Change the status, recording what a pause is a pause *from*.

        The store computes ``paused_from`` itself rather than taking it,
        because it is the only party that knows the current status without a
        read-then-write. Without it, ``resumed()`` falls back to ``draft`` --
        so resuming a paused agent that was live would quietly un-publish it.

        Moving out of ``paused`` clears both fields, or a later un-pause
        restores a state the agent is no longer in.
        """
        ...

    async def save_deployment(
        self,
        workspace_id: str,
        instance_id: str,
        deployment: Deployment,
        *,
        expect_revision: int,
    ) -> AgentInstance:
        """Where the agent may appear. Applies immediately, unversioned."""
        ...

    async def pin_template_version(
        self, workspace_id: str, instance_id: str, version: int, *, expect_revision: int
    ) -> AgentInstance:
        """Move the draft onto another template version — an opted-in upgrade."""
        ...

    async def publish(
        self, version: AgentInstanceVersion, *, expect_revision: int
    ) -> AgentInstance:
        """Snapshot the draft overrides. Version written before the pointer."""
        ...

    async def soft_delete(
        self, workspace_id: str, instance_id: str, *, expect_revision: int
    ) -> AgentInstance:
        """Marked deleted, not removed.

        A deleted agent still has conversations, leads and a transcript someone
        may need to read. Nothing here hard-deletes.
        """
        ...

    async def version(
        self, workspace_id: str, instance_id: str, version: int
    ) -> AgentInstanceVersion | None: ...

    async def published(
        self, workspace_id: str, instance_id: str
    ) -> AgentInstanceVersion | None:
        """The snapshot live traffic resolves against, if it is published."""
        ...

    async def published_many(
        self, workspace_id: str, instance_ids: Sequence[str]
    ) -> Mapping[str, AgentInstanceVersion]:
        """The published snapshots of several agents, keyed by instance id.

        Here so that a list screen can say which agents have unpublished
        changes without a read per agent. Ids with nothing published are
        absent from the mapping rather than present and ``None``: "no
        snapshot" is the same answer as "never published", and one way of
        saying it is enough.
        """
        ...

    async def versions(
        self, workspace_id: str, instance_id: str, *, limit: int = 50
    ) -> Sequence[AgentInstanceVersion]:
        """Newest first."""
        ...


@runtime_checkable
class ConversationStore(Protocol):
    """Transcripts."""

    async def start(self, conversation: Conversation) -> Conversation: ...

    async def get(
        self, workspace_id: str, conversation_id: str
    ) -> Conversation | None: ...

    async def append(
        self, workspace_id: str, conversation_id: str, *turns: Turn
    ) -> Conversation:
        """
        Add turns to the end.

        Append rather than save-the-whole-thing: a transcript only ever grows,
        and writing it back whole means one slow turn can overwrite a turn that
        arrived while it was thinking.
        """
        ...

    async def recent(
        self,
        workspace_id: str,
        *,
        instance_id: str | None = None,
        channel: Channel | None = None,
        limit: int = 50,
    ) -> Sequence[Conversation]:
        """Newest first, for the conversations screen."""
        ...
