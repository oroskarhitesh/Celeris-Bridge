"""
Agent types: the platform's own screens.

Every route here needs the platform role, because a template version is
resolved by every workspace pinned to it -- this is the one surface in the
product where one request affects somebody else's agent. That is also why the
dependency is declared on the router rather than on each handler: a route
added later is protected by having been added here.

The draft is stored as the raw document, unvalidated, exactly as the service
takes it. A definition is written in several passes and a draft that has to be
valid to be saved cannot be written at all. ``GET /check`` answers what
publishing it would do, and ``POST /publish`` is the only thing that refuses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from celeris_bridge.api.dependencies import App, Platform, require_platform
from celeris_bridge.api.models import Payload, View
from celeris_bridge.domain.agent.records import (
    AgentTemplate,
    AgentTemplateVersion,
    TemplateStatus,
)

router = APIRouter(
    prefix="/v1/templates",
    tags=["templates"],
    dependencies=[Depends(require_platform)],
)

#: What a key may look like. Enforced here and not in the service because the
#: reason is HTTP's: a key goes in a URL and in a filename, so the characters
#: it may contain is the API's business. It is permanent once created, which
#: is the service's rule and stated there.
KEY = r"^[a-z][a-z0-9_]{1,63}$"


class CreateTemplate(Payload):
    key: str = Field(pattern=KEY)
    #: The starting draft, which may be anything -- including nothing, for a
    #: type somebody is about to write from scratch in the editor.
    draft: dict[str, Any] = Field(default_factory=dict)


class SaveDraft(Payload):
    draft: dict[str, Any]
    expect_revision: int = Field(ge=0)


class PublishTemplate(Payload):
    expect_revision: int = Field(ge=0)
    change_summary: str = Field(default="", max_length=500)


class SetTemplateStatus(Payload):
    status: TemplateStatus
    expect_revision: int = Field(ge=0)


class RestoreDraft(Payload):
    expect_revision: int = Field(ge=0)


class TemplateView(View):
    """One agent type, for a list screen."""

    key: str
    status: str
    draft_revision: int
    latest_version: int | None
    last_published_at: datetime | None
    created_at: datetime | None
    #: Whether anything has been saved into the draft at all. The list needs
    #: it to distinguish "never written" from "written and published".
    has_draft: bool


class TemplateDetailView(TemplateView):
    #: The raw draft document. Only on the detail view: it is the biggest
    #: thing here and a list of thirty of them is a slow screen for no gain.
    draft: Mapping[str, Any]


class VersionView(View):
    template_key: str
    version: int
    change_summary: str
    published_at: datetime | None
    published_by: str | None


class VersionDetailView(VersionView):
    definition: Mapping[str, Any]


class TemplatePublishedView(View):
    """The new version, and the template as it is now.

    The draft revision moves when a version is published, so a client given
    only the version would have to re-read the template before its next save.
    """

    version: VersionView
    template: TemplateDetailView


class ProblemView(View):
    path: str
    message: str


class DraftCheckView(View):
    """What publishing the current draft would do."""

    ok: bool
    problems: list[ProblemView]
    #: The name and description the published version would carry. Read off
    #: the parsed definition rather than stored on the record: they are part
    #: of the document, versioned with everything else, and a second copy on
    #: the record would be the one that drifts.
    name: str = ""
    description: str = ""
    #: Settings this draft adds, and ones it removes. Shown next to the
    #: publish button, because adding one is safe and removing one silently
    #: drops every workspace's value for it.
    added_settings: list[str] = []
    removed_settings: list[str] = []


def _template(template: AgentTemplate) -> TemplateView:
    return TemplateView(
        key=template.key,
        status=str(template.status),
        draft_revision=template.draft_revision,
        latest_version=template.latest_version,
        last_published_at=template.last_published_at,
        created_at=template.created_at,
        has_draft=bool(template.draft),
    )


def _detail(template: AgentTemplate) -> TemplateDetailView:
    return TemplateDetailView(
        **_template(template).model_dump(), draft=dict(template.draft)
    )


def _version(version: AgentTemplateVersion) -> VersionView:
    return VersionView(
        template_key=version.template_key,
        version=version.version,
        change_summary=version.change_summary,
        published_at=version.published_at,
        published_by=version.published_by,
    )


@router.get("")
async def list_templates(
    application: App, status: TemplateStatus | None = Query(default=None)
) -> list[TemplateView]:
    return [_template(one) for one in await application.templates.list(status=status)]


@router.post("", status_code=201)
async def create_template(
    application: App, body: CreateTemplate
) -> TemplateDetailView:
    return _detail(
        await application.templates.create(key=body.key, draft=body.draft)
    )


@router.get("/{key}")
async def get_template(application: App, key: str) -> TemplateDetailView:
    return _detail(await application.templates.get(key))


@router.put("/{key}/draft")
async def save_draft(
    application: App, key: str, body: SaveDraft
) -> TemplateDetailView:
    return _detail(
        await application.templates.save_draft(
            key, body.draft, expect_revision=body.expect_revision
        )
    )


@router.get("/{key}/check")
async def check_draft(application: App, key: str) -> DraftCheckView:
    """Never fails. A broken draft is a 200 with the problems listed."""
    check = await application.templates.check_draft(key)
    identity = check.definition.identity if check.definition is not None else None
    return DraftCheckView(
        ok=check.ok,
        problems=[
            ProblemView(path=path, message=message) for path, message in check.problems
        ],
        name=identity.name if identity else "",
        description=identity.description if identity else "",
        added_settings=list(check.added_settings),
        removed_settings=list(check.removed_settings),
    )


@router.post("/{key}/publish", status_code=201)
async def publish_template(
    application: App, key: str, body: PublishTemplate, who: Platform
) -> TemplatePublishedView:
    version = await application.templates.publish(
        key,
        expect_revision=body.expect_revision,
        change_summary=body.change_summary,
        # From the credential, like every other identity in this API.
        published_by=who.user_id,
    )
    return TemplatePublishedView(
        version=_version(version),
        template=_detail(await application.templates.get(key)),
    )


@router.get("/{key}/versions")
async def list_versions(
    application: App, key: str, limit: int = Query(default=50, ge=1, le=200)
) -> list[VersionView]:
    return [
        _version(one) for one in await application.templates.versions(key, limit=limit)
    ]


@router.get("/{key}/versions/{version}")
async def get_version(
    application: App, key: str, version: int
) -> VersionDetailView:
    stored = await application.templates.version(key, version)
    return VersionDetailView(
        **_version(stored).model_dump(), definition=dict(stored.definition)
    )


@router.post("/{key}/versions/{version}/restore")
async def restore_draft(
    application: App, key: str, version: int, body: RestoreDraft
) -> TemplateDetailView:
    """Copy a published version back into the draft. Changes nothing live.

    How a rollback of an agent *type* begins, and it is two steps on purpose:
    nothing here edits or deletes a version, so going back is a new version
    that happens to carry older content, and publishing it stays deliberate.
    """
    return _detail(
        await application.templates.restore_draft_from(
            key, version, expect_revision=body.expect_revision
        )
    )


@router.put("/{key}/status")
async def set_status(
    application: App, key: str, body: SetTemplateStatus
) -> TemplateDetailView:
    """Archive a type, or bring it back. Nothing is ever hard-deleted.

    Workspaces have agents pinned to versions of this type; removing it would
    leave them unresolvable, which is an outage for a customer who did
    nothing.
    """
    return _detail(
        await application.templates.set_status(
            key, body.status, expect_revision=body.expect_revision
        )
    )
