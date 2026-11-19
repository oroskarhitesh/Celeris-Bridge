"""
The agent types a workspace may build on.

Separate from ``/v1/templates`` because the two answer different questions for
different people. That router is the platform's editor -- drafts, versions,
publishing -- and needs the platform role. This one is the catalogue a
workspace picks from, so it needs only a workspace, and it shows the published
face of each type and nothing about its draft.

It is the one place a template's *identity* is read for display, and it is
read out of the latest published version rather than off the template record,
because that is where it lives: the name and description are part of the
definition document, versioned with everything else.

A type with no published version is not listed. There is nothing to build on
yet, and a catalogue entry that fails when chosen is worse than an absent one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from celeris_bridge.api.dependencies import App, Workspace, require_workspace
from celeris_bridge.api.models import View
from celeris_bridge.domain.agent.records import TemplateStatus

router = APIRouter(
    prefix="/v1/catalogue",
    tags=["catalogue"],
    dependencies=[Depends(require_workspace)],
)


class CatalogueEntryView(View):
    template_key: str
    #: The version a new agent would be pinned to.
    version: int
    name: str
    description: str
    icon: str
    category: str
    channels: list[str]
    #: How many of this type one workspace may have, and how many they have.
    #: Both, so the screen can disable the button rather than explain a 409.
    max_per_workspace: int
    owned: int


@router.get("")
async def catalogue(
    application: App, workspace_id: Workspace
) -> list[CatalogueEntryView]:
    entries: list[CatalogueEntryView] = []
    for template in await application.templates.list(status=TemplateStatus.ACTIVE):
        latest = await application.stores.templates.latest(template.key)
        if latest is None:
            continue
        # Through the shared cache: this parses the same immutable documents
        # the serving path parses, so a catalogue screen does not pay for the
        # validation twice.
        definition = application.cache.parsed(
            key=template.key,
            version=latest.version,
            document=latest.definition,
            registry=application.registry,
        )
        entries.append(
            CatalogueEntryView(
                template_key=template.key,
                version=latest.version,
                name=definition.identity.name,
                description=definition.identity.description,
                icon=definition.identity.icon,
                category=definition.identity.category,
                channels=list(definition.identity.channels),
                max_per_workspace=definition.limits.max_agents_per_workspace,
                owned=await application.stores.instances.count_for_template(
                    workspace_id, template.key
                ),
            )
        )
    return entries
