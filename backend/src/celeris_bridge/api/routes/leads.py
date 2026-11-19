"""
The leads list.

A read model over two records that already exist, which is why there is no
``POST`` here: a lead is a conversation that produced some details, and
creating one by hand would be creating a conversation that never happened.

``withDetailsOnly`` is the view most workspaces want. A public widget has far
more conversations than leads, and a list that shows every passer-by who said
"hi" buries the ones worth calling.

The verdict shown is the one that was recorded when the visitor said it, not a
recomputation. A qualification rule changed last week must not quietly rewrite
what a lead was called on Tuesday.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from fastapi import APIRouter, Depends, Query

from celeris_bridge.api.dependencies import App, Workspace, require_workspace
from celeris_bridge.api.models import View
from celeris_bridge.domain.conversation import Channel
from celeris_bridge.services.leads import Lead

router = APIRouter(
    prefix="/v1/leads",
    tags=["leads"],
    dependencies=[Depends(require_workspace)],
)


class LeadView(View):
    conversation_id: str
    instance_id: str
    channel: str
    #: What the visitor gave, under the keys the workspace asked for. Not
    #: flattened into name and email columns: which details are collected is a
    #: setting, so a fixed shape here would be wrong for anyone who changed it.
    details: Mapping[str, str]
    visitor_type: str
    status: str
    #: Required details still missing, so the list can show what to follow up.
    missing: list[str]
    reasons: list[str]
    turns: int
    started_at: datetime | None
    last_at: datetime | None
    page_url: str
    #: The first thing the visitor said. The most useful column there is: it is
    #: why they got in touch, in their own words.
    opening_message: str
    complete: bool


def _lead(lead: Lead) -> LeadView:
    return LeadView(
        conversation_id=lead.conversation_id,
        instance_id=lead.instance_id,
        channel=lead.channel,
        details=dict(lead.details),
        visitor_type=lead.visitor_type,
        status=lead.status,
        missing=list(lead.missing),
        reasons=list(lead.reasons),
        turns=lead.turns,
        started_at=lead.started_at,
        last_at=lead.last_at,
        page_url=lead.page_url,
        opening_message=lead.opening_message,
        complete=lead.complete,
    )


@router.get("")
async def list_leads(
    application: App,
    workspace_id: Workspace,
    instance_id: str | None = Query(default=None, alias="instanceId"),
    channel: Channel | None = Query(default=None),
    status: str | None = Query(default=None),
    with_details_only: bool = Query(default=False, alias="withDetailsOnly"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[LeadView]:
    return [
        _lead(one)
        for one in await application.leads.list(
            workspace_id,
            instance_id=instance_id,
            channel=channel,
            status=status,
            with_details_only=with_details_only,
            limit=limit,
        )
    ]


@router.get("/{conversation_id}")
async def get_lead(
    application: App, workspace_id: Workspace, conversation_id: str
) -> LeadView:
    return _lead(await application.leads.get(workspace_id, conversation_id))
