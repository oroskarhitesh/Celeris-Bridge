"""
The four records the platform stores, and nothing about how they are stored.

The hierarchy is the whole point, so it is worth stating plainly:

``AgentTemplate``
    One agent type, owned by the platform. Mutable: it holds the draft a
    platform admin edits, which may have problems.
``AgentTemplateVersion``
    A published definition. **Immutable** — never updated, never deleted — so a
    workspace pinned to it behaves the same next year.
``AgentInstance``
    A workspace's own agent. Holds its *overrides* and the template version its
    draft is pinned to. Never a copy of the definition, which is what makes a
    better default reach every workspace that never touched that setting.
``AgentInstanceVersion``
    A published snapshot of those overrides. Live traffic resolves the pinned
    template version plus one of these.

Ids are strings here. Nothing in the domain knows what an ``ObjectId`` is; the
adapter converts, which is the only reason a second database would be a second
adapter rather than a change to these records.

This module is pure: no I/O, no database, no framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Mapping

from celeris_bridge.domain.agent.overrides import Overrides

__all__ = [
    "AgentInstance",
    "AgentInstanceVersion",
    "AgentTemplate",
    "AgentTemplateVersion",
    "Deployment",
    "InstanceStatus",
    "PausedReason",
    "RevisionConflict",
    "TemplateStatus",
]


class TemplateStatus(StrEnum):
    ACTIVE = "active"
    #: No new agents may be built on it. Existing ones keep running — archiving
    #: a template must not take a customer's agent off their website.
    ARCHIVED = "archived"


class InstanceStatus(StrEnum):
    DRAFT = "draft"
    LIVE = "live"
    PAUSED = "paused"


class PausedReason(StrEnum):
    #: The workspace lost access to the template it was built on.
    ACCESS_REVOKED = "access_revoked"


class RevisionConflict(RuntimeError):
    """Someone else saved this draft first.

    Two admins with the same settings form open, both pressing save, is not an
    edge case — it is a Tuesday. Last write wins means the second one silently
    discards the first one's work, so every save states the revision it is
    based on and this is what a mismatch raises.
    """

    def __init__(self, *, expected: int, found: int) -> None:
        self.expected = expected
        self.found = found
        super().__init__(
            f"This draft has moved on since you loaded it (you have revision "
            f"{expected}, it is now {found}). Reload and apply your change again."
        )


@dataclass(frozen=True)
class AgentTemplate:
    """One agent type, with the draft a platform admin is editing."""

    key: str
    #: The draft definition, as plain JSON. Deliberately unvalidated: a draft is
    #: allowed to be wrong, which is how an admin saves half an idea. Publishing
    #: is what requires ``validate_definition`` to come back clean.
    draft: Mapping[str, Any] = field(default_factory=dict)
    status: TemplateStatus = TemplateStatus.ACTIVE
    #: Rises on every draft save. The guard behind :class:`RevisionConflict`.
    draft_revision: int = 0
    draft_updated_at: datetime | None = None
    #: Cached pointer to the newest published version, for list screens. The
    #: versions themselves are the source of truth; see ``TemplateStore.publish``.
    latest_version: int | None = None
    last_published_at: datetime | None = None
    #: The template this one started as a copy of, if any.
    copied_from: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_publishable_target(self) -> bool:
        """Whether new agents may be built on it."""
        return self.status is TemplateStatus.ACTIVE

    def next_version(self) -> int:
        return (self.latest_version or 0) + 1


@dataclass(frozen=True)
class AgentTemplateVersion:
    """A published definition. Never changes once written."""

    template_key: str
    version: int
    #: Validated at publish time. Whatever else happens, this document is what
    #: every pinned workspace resolves against.
    definition: Mapping[str, Any] = field(default_factory=dict)
    change_summary: str = ""
    published_at: datetime | None = None
    published_by: str | None = None


@dataclass(frozen=True)
class Deployment:
    """Where an agent may appear. Operational, so changes apply immediately.

    Deliberately **not** versioned, unlike settings. Taking an agent off a site
    it should never have been on is an incident, and an incident must not wait
    for a publish.
    """

    #: The browser refuses to show the chat frame on any site not listed.
    allowed_origins: tuple[str, ...] = ()
    widget_enabled: bool = True
    launcher_position: Literal["left", "right"] = "right"
    launcher_text: str = ""
    #: Install check: where the widget was last opened from.
    last_seen_at: datetime | None = None
    last_seen_origin: str = ""


@dataclass(frozen=True)
class AgentInstance:
    """A workspace's agent: its overrides, and the version they apply to."""

    id: str
    workspace_id: str
    template_key: str
    #: Internal label ("Main website"). What a visitor sees is a setting.
    label: str
    #: The template version this draft is pinned to. Changes only on an
    #: upgrade the workspace opts into, which is what stops a publish by the
    #: platform rewriting a live agent underneath its owner.
    draft_template_version: int = 1
    draft_overrides: Overrides = field(default_factory=Overrides)
    draft_revision: int = 0
    status: InstanceStatus = InstanceStatus.DRAFT
    paused_reason: PausedReason | None = None
    #: What to restore when it is un-paused. Without it, un-pausing has to
    #: guess, and guessing "live" puts an agent back on a website by accident.
    paused_from: InstanceStatus | None = None
    deployment: Deployment = field(default_factory=Deployment)
    published_version: int | None = None
    last_published_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    @property
    def is_live(self) -> bool:
        return self.status is InstanceStatus.LIVE and self.deleted_at is None

    def differs_from(self, snapshot: AgentInstanceVersion | None) -> bool:
        """Whether publishing this draft would change what visitors get.

        It takes the snapshot rather than answering from a counter, because a
        counter cannot answer it: publishing bumps ``draft_revision`` itself,
        so ``draft_revision > 0`` is true the instant after a publish on a
        draft nobody has touched. The overrides and the pinned template
        version are the whole of what a snapshot holds, so comparing them is
        exact -- an edit that puts a value back where it was stops counting,
        which is the honest answer and not a tolerated inaccuracy.

        No snapshot means nothing is published, so everything here is
        unpublished.
        """
        if snapshot is None:
            return True
        return (
            self.draft_template_version != snapshot.template_version
            or self.draft_overrides != snapshot.overrides
        )

    def next_version(self) -> int:
        return (self.published_version or 0) + 1

    def paused(self, reason: PausedReason | None = None) -> AgentInstance:
        """Paused, remembering what it was so un-pausing can put it back.

        ``reason`` is for a pause the *platform* applied and has to explain.
        An owner pausing their own agent needs no reason beyond having done
        it, which is why it is optional rather than an enum member meaning
        "because they said so".
        """
        if self.status is InstanceStatus.PAUSED:
            return self
        return replace(
            self,
            status=InstanceStatus.PAUSED,
            paused_reason=reason,
            paused_from=self.status,
        )

    def resumed(self) -> AgentInstance:
        """Back to whatever it was before it was paused."""
        if self.status is not InstanceStatus.PAUSED:
            return self
        return replace(
            self,
            status=self.paused_from or InstanceStatus.DRAFT,
            paused_reason=None,
            paused_from=None,
        )


@dataclass(frozen=True)
class AgentInstanceVersion:
    """A published snapshot of one workspace's overrides. Immutable."""

    id: str
    workspace_id: str
    instance_id: str
    version: int
    template_key: str
    #: The template version live traffic uses while this snapshot is published.
    template_version: int
    overrides: Overrides = field(default_factory=Overrides)
    change_summary: str = ""
    #: Set when this version exists because someone rolled back to an earlier
    #: one. A rollback publishes a new version rather than deleting any, so the
    #: history stays append-only and the rollback itself is auditable.
    rolled_back_from: int | None = None
    published_at: datetime | None = None
    published_by: str | None = None
