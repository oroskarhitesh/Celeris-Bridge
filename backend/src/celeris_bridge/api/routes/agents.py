"""
A workspace's agents.

The workspace comes from the credential on every route in this file -- it is
the ``Workspace`` dependency, declared on the router, and there is no field in
any request body that could carry one. See
:mod:`celeris_bridge.api.principal` for why that is structural rather than a
convention.

Two endpoints here are worth more than their length.

``GET /{id}/settings`` is the admin form, and it is *generated*. Each setting
is sent as its declaration -- label, type, options, limits -- plus the
resolved value and whether this workspace is the one that set it. The same
``FieldSpec`` drives the default, the validator, this form, the JSON Schema
the model is given and the way a value is rendered into the prompt. A form
built by hand instead would be the fifth copy, and the one that disagrees.

``POST /{id}/preview/...`` talks to the *draft*. It is the only caller of
``resolve_draft`` and the reason that function exists: an admin needs to try a
change before visitors get it, and a preview that answered from the published
snapshot would be a preview of nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Mapping

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from celeris_bridge.api.dependencies import App, Who, Workspace, require_workspace
from celeris_bridge.api.models import Payload, View
from celeris_bridge.api.routes.widget import ReplyView, SayMessage, reply_view
from celeris_bridge.composition.build import Application
from celeris_bridge.domain.agent.definition_schema import AgentDefinition
from celeris_bridge.domain.agent.overrides import Overrides, ResolvedConfig
from celeris_bridge.domain.agent.records import (
    AgentInstance,
    AgentInstanceVersion,
    Deployment,
    InstanceStatus,
)
from celeris_bridge.domain.conversation import Channel
from celeris_bridge.services.resolution import resolve_draft

router = APIRouter(
    prefix="/v1/agents",
    tags=["agents"],
    dependencies=[Depends(require_workspace)],
)


# -- requests ------------------------------------------------------------


class AbilityChange(Payload):
    """A change to one ability. ``None`` for enabled means "leave it"."""

    enabled: bool | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class CreateAgent(Payload):
    template_key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    #: Which template version to pin to. The latest when absent, and the pin
    #: is the only thing live traffic follows afterwards.
    template_version: int | None = Field(default=None, ge=1)
    settings: dict[str, Any] = Field(default_factory=dict)
    abilities: dict[str, AbilityChange] = Field(default_factory=dict)


class EditAgent(Payload):
    settings: dict[str, Any] = Field(default_factory=dict)
    abilities: dict[str, AbilityChange] = Field(default_factory=dict)
    label: str | None = Field(default=None, min_length=1, max_length=120)
    expect_revision: int = Field(ge=0)


class ResetAgent(Payload):
    """Which overrides to drop, so those settings follow the default again."""

    settings: list[str] = Field(default_factory=list)
    abilities: list[str] = Field(default_factory=list)
    everything: bool = False
    expect_revision: int = Field(ge=0)


class PublishAgent(Payload):
    expect_revision: int = Field(ge=0)
    change_summary: str = Field(default="", max_length=500)


class RollbackAgent(Payload):
    to_version: int = Field(ge=1)
    expect_revision: int = Field(ge=0)


class UpgradeAgent(Payload):
    #: The latest template version when absent.
    to_version: int | None = Field(default=None, ge=1)
    expect_revision: int = Field(ge=0)


class StatusChange(Payload):
    expect_revision: int = Field(ge=0)


class DeploymentBody(Payload):
    """Where the agent may appear. Applies immediately, unversioned.

    Deliberately not published like settings: the allowed origins are a
    security control, and taking a domain off the list has to take effect now
    rather than at the next publish.
    """

    allowed_origins: list[str] = Field(default_factory=list, max_length=50)
    widget_enabled: bool = True
    launcher_position: Literal["left", "right"] = "right"
    launcher_text: str = Field(default="", max_length=120)
    expect_revision: int = Field(ge=0)


class StartPreview(Payload):
    page_url: str = Field(default="", max_length=2000)
    page_title: str = Field(default="", max_length=300)


# -- responses -----------------------------------------------------------


class DeploymentView(View):
    allowed_origins: list[str]
    widget_enabled: bool
    launcher_position: str
    launcher_text: str
    last_seen_at: datetime | None
    last_seen_origin: str


class AgentView(View):
    id: str
    template_key: str
    #: The template version the draft is pinned to. Not necessarily the one
    #: serving traffic: that is the one the published snapshot names.
    draft_template_version: int
    label: str
    status: str
    paused_reason: str | None
    published_version: int | None
    has_unpublished_changes: bool
    draft_revision: int
    last_published_at: datetime | None
    created_at: datetime | None
    deployment: DeploymentView


class SettingView(View):
    """One setting: its declaration, its value, and whose value it is."""

    key: str
    #: The ``FieldSpec`` as the definition wrote it -- type, label, options,
    #: limits, default. The control is drawn from this.
    spec: Mapping[str, Any]
    value: Any = None
    #: True when this workspace set it. What lets a form show "default" next
    #: to a field instead of pretending every value was chosen.
    overridden: bool = False


class AbilityView(View):
    key: str
    label: str = ""
    enabled: bool = False
    #: Whether this workspace may turn it on or off at all.
    editable: bool = False
    enabled_by_default: bool = True
    settings: Mapping[str, Any] = {}
    #: The declarations for that ability's own settings, same as above.
    settings_spec: Mapping[str, Any] = {}
    #: True when the definition names it and this build does not have it. The
    #: agent runs with one fewer tool, so the screen should say so.
    missing: bool = False


class LockedRuleView(View):
    id: str
    text: str


class SettingsFormView(View):
    """Everything an admin screen needs to draw itself."""

    agent: AgentView
    name: str
    description: str
    template_version: int
    #: The order the groups are shown in, from the definition.
    groups: list[str]
    settings: list[SettingView]
    abilities: list[AbilityView]
    #: Shown, never editable. A workspace should be able to read the rules its
    #: agent is held to.
    locked_rules: list[LockedRuleView]


class AgentVersionView(View):
    version: int
    template_key: str
    template_version: int
    change_summary: str
    rolled_back_from: int | None
    published_at: datetime | None
    published_by: str | None


class PublishedView(View):
    """A new version, and the agent as it is now.

    Both, because publishing bumps the draft revision: a client given only
    the version has to re-read the agent before it can make its next write,
    and the one that forgets sends a stale revision and gets a 409 it cannot
    explain. Every other write here returns the fresh record, so this one
    does too.
    """

    version: AgentVersionView
    agent: AgentView


class DroppedView(View):
    path: str
    reason: str


class UpgradePreviewView(View):
    from_version: int
    to_version: int
    #: What the newer version would no longer accept. The list that matters:
    #: this is shown *before* anything is written.
    dropped: list[DroppedView]
    added_settings: list[str]
    loses_nothing: bool


class PreviewConversationView(View):
    conversation_id: str
    #: Null, always, for a preview: a draft has no published version. It is
    #: also how a preview is told apart from real traffic afterwards.
    instance_version: int | None = None
    template_version: int | None = None


# -- presenters ----------------------------------------------------------


def _agent(instance: AgentInstance, unpublished: bool) -> AgentView:
    """One agent, as a view.

    ``unpublished`` is a parameter and not a field read off the record,
    because the record cannot answer it: the comparison needs the published
    snapshot. Required rather than defaulted, so a new route cannot quietly
    present a wrong answer -- see ``AgentInstance.differs_from``.
    """
    return AgentView(
        id=instance.id,
        template_key=instance.template_key,
        draft_template_version=instance.draft_template_version,
        label=instance.label,
        status=str(instance.status),
        paused_reason=str(instance.paused_reason) if instance.paused_reason else None,
        published_version=instance.published_version,
        has_unpublished_changes=unpublished,
        draft_revision=instance.draft_revision,
        last_published_at=instance.last_published_at,
        created_at=instance.created_at,
        deployment=DeploymentView(
            allowed_origins=list(instance.deployment.allowed_origins),
            widget_enabled=instance.deployment.widget_enabled,
            launcher_position=instance.deployment.launcher_position,
            launcher_text=instance.deployment.launcher_text,
            last_seen_at=instance.deployment.last_seen_at,
            last_seen_origin=instance.deployment.last_seen_origin,
        ),
    )


async def _view(
    application: Application, workspace_id: str, instance: AgentInstance
) -> AgentView:
    """An agent and the one thing about it that takes a read to know."""
    return _agent(
        instance, await application.instances.unpublished(workspace_id, instance)
    )


def _version(version: AgentInstanceVersion) -> AgentVersionView:
    return AgentVersionView(
        version=version.version,
        template_key=version.template_key,
        template_version=version.template_version,
        change_summary=version.change_summary,
        rolled_back_from=version.rolled_back_from,
        published_at=version.published_at,
        published_by=version.published_by,
    )


def _patch(body: CreateAgent | EditAgent) -> dict[str, Any]:
    """The request, as the override validator takes it.

    ``exclude_none`` matters: an ability change that does not mention
    ``enabled`` must leave it alone, and sending ``None`` would read as an
    instruction to set it.
    """
    return {
        "settings": dict(body.settings),
        "abilities": {
            key: change.model_dump(exclude_none=True)
            for key, change in body.abilities.items()
        },
    }


def _form(
    *,
    instance: AgentInstance,
    unpublished: bool,
    definition: AgentDefinition,
    config: ResolvedConfig,
    overrides: Overrides,
    registry: Mapping[str, Any],
    include_admin_only: bool,
) -> SettingsFormView:
    settings: list[SettingView] = []
    for key, spec in definition.settings.items():
        if spec.admin_only and not include_admin_only:
            # Left out rather than sent read-only: the validator refuses an
            # admin-only setting from a workspace patch, and a form that draws
            # a control the validator will refuse is a form that lies.
            continue
        settings.append(
            SettingView(
                key=key,
                spec=spec.model_dump(by_alias=True, exclude_none=True),
                value=config.settings.get(key),
                overridden=key in overrides.settings,
            )
        )

    abilities: list[AbilityView] = []
    for ref in definition.abilities:
        resolved = config.ability(ref.key)
        contract = registry.get(ref.key)
        abilities.append(
            AbilityView(
                key=ref.key,
                label=getattr(contract, "label", "") or ref.key,
                enabled=bool(resolved and resolved.enabled),
                editable=ref.editable,
                enabled_by_default=ref.enabled_by_default,
                settings=dict(resolved.settings) if resolved else {},
                settings_spec={
                    name: one.model_dump(by_alias=True, exclude_none=True)
                    for name, one in (
                        getattr(contract, "settings", {}) or {}
                    ).items()
                },
                missing=ref.key in config.missing_abilities,
            )
        )

    return SettingsFormView(
        agent=_agent(instance, unpublished),
        name=definition.identity.name,
        description=definition.identity.description,
        template_version=instance.draft_template_version,
        groups=list(definition.setting_groups),
        settings=settings,
        abilities=abilities,
        locked_rules=[
            LockedRuleView(id=rule.id, text=rule.text)
            for rule in definition.locked_rules
        ],
    )


# -- reading -------------------------------------------------------------


@router.get("")
async def list_agents(
    application: App,
    workspace_id: Workspace,
    template_key: str | None = Query(default=None, alias="templateKey"),
    status: InstanceStatus | None = Query(default=None),
) -> list[AgentView]:
    agents = await application.instances.list(
        workspace_id, template_key=template_key, status=status
    )
    unpublished = await application.instances.unpublished_many(workspace_id, agents)
    return [_agent(one, unpublished[one.id]) for one in agents]


@router.get("/{instance_id}")
async def get_agent(
    application: App, workspace_id: Workspace, instance_id: str
) -> AgentView:
    return await _view(
        application, workspace_id, await application.instances.get(workspace_id, instance_id)
    )


@router.get("/{instance_id}/settings")
async def settings_form(
    application: App, workspace_id: Workspace, instance_id: str, who: Who
) -> SettingsFormView:
    """The form: every declaration, every resolved value, and which are theirs."""
    instance = await application.instances.get(workspace_id, instance_id)
    definition, config = await application.instances.settings_form(
        workspace_id, instance_id
    )
    return _form(
        instance=instance,
        unpublished=await application.instances.unpublished(workspace_id, instance),
        definition=definition,
        config=config,
        overrides=instance.draft_overrides,
        registry=application.registry,
        include_admin_only=who.platform_admin,
    )


@router.get("/{instance_id}/versions")
async def list_agent_versions(
    application: App,
    workspace_id: Workspace,
    instance_id: str,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AgentVersionView]:
    return [
        _version(one)
        for one in await application.instances.versions(
            workspace_id, instance_id, limit=limit
        )
    ]


@router.get("/{instance_id}/upgrade")
async def preview_upgrade(
    application: App,
    workspace_id: Workspace,
    instance_id: str,
    to_version: int | None = Query(default=None, ge=1, alias="toVersion"),
) -> UpgradePreviewView:
    """What moving to another template version would cost. Writes nothing."""
    preview = await application.instances.preview_upgrade(
        workspace_id, instance_id, to_version=to_version
    )
    return UpgradePreviewView(
        from_version=preview.from_version,
        to_version=preview.to_version,
        dropped=[
            DroppedView(path=one.path, reason=one.reason) for one in preview.dropped
        ],
        added_settings=list(preview.added_settings),
        loses_nothing=preview.loses_nothing,
    )


# -- writing -------------------------------------------------------------


@router.post("", status_code=201)
async def create_agent(
    application: App, workspace_id: Workspace, body: CreateAgent
) -> AgentView:
    """Add an agent, as a draft. It appears when its owner publishes it."""
    return await _view(
        application,
        workspace_id,
        await application.instances.create(
            workspace_id=workspace_id,
            template_key=body.template_key,
            label=body.label,
            template_version=body.template_version,
            patch=_patch(body),
        )
    )


@router.patch("/{instance_id}")
async def edit_agent(
    application: App, workspace_id: Workspace, instance_id: str, body: EditAgent
) -> AgentView:
    """Change settings on the draft. All or nothing.

    A rejected value takes the whole request with it and writes nothing, so a
    form never ends up half-applied -- and the 400 carries every reason, not
    the first, so it can mark all of its bad fields at once.
    """
    return await _view(
        application,
        workspace_id,
        await application.instances.edit(
            workspace_id,
            instance_id,
            _patch(body),
            expect_revision=body.expect_revision,
            label=body.label,
        )
    )


@router.post("/{instance_id}/reset")
async def reset_agent(
    application: App, workspace_id: Workspace, instance_id: str, body: ResetAgent
) -> AgentView:
    """Drop overrides, so those settings follow the default again.

    A deletion and not a write of the current default, which is the whole
    point: a setting that has been reset follows the default *from now on*,
    including when a later version improves it.
    """
    return await _view(
        application,
        workspace_id,
        await application.instances.reset(
            workspace_id,
            instance_id,
            expect_revision=body.expect_revision,
            settings=body.settings,
            abilities=body.abilities,
            everything=body.everything,
        )
    )


@router.post("/{instance_id}/publish", status_code=201)
async def publish_agent(
    application: App,
    workspace_id: Workspace,
    instance_id: str,
    body: PublishAgent,
    who: Who,
) -> PublishedView:
    """Snapshot the draft, and make it what visitors talk to."""
    version = await application.instances.publish(
        workspace_id,
        instance_id,
        expect_revision=body.expect_revision,
        change_summary=body.change_summary,
        published_by=who.user_id,
    )
    return PublishedView(
        version=_version(version),
        agent=await _view(
            application,
            workspace_id,
            await application.instances.get(workspace_id, instance_id),
        ),
    )


@router.post("/{instance_id}/rollback", status_code=201)
async def rollback_agent(
    application: App,
    workspace_id: Workspace,
    instance_id: str,
    body: RollbackAgent,
    who: Who,
) -> PublishedView:
    """Go back to an earlier version, by publishing a new one.

    Nothing is edited or deleted: the new version carries the old overrides
    and records where they came from, so the history reads as what happened.
    """
    version = await application.instances.rollback(
        workspace_id,
        instance_id,
        to_version=body.to_version,
        expect_revision=body.expect_revision,
        published_by=who.user_id,
    )
    return PublishedView(
        version=_version(version),
        agent=await _view(
            application,
            workspace_id,
            await application.instances.get(workspace_id, instance_id),
        ),
    )


@router.post("/{instance_id}/upgrade")
async def upgrade_agent(
    application: App, workspace_id: Workspace, instance_id: str, body: UpgradeAgent
) -> AgentView:
    """Move the draft onto a newer template version.

    Explicit, and it changes nothing live until the agent is published again.
    ``GET /upgrade`` first if anything might be dropped.
    """
    return await _view(
        application,
        workspace_id,
        await application.instances.upgrade(
            workspace_id,
            instance_id,
            expect_revision=body.expect_revision,
            to_version=body.to_version,
        )
    )


@router.post("/{instance_id}/pause")
async def pause_agent(
    application: App, workspace_id: Workspace, instance_id: str, body: StatusChange
) -> AgentView:
    """Stop answering, remembering what to go back to.

    No reason is taken from the request: a reason is for a pause the platform
    applied and has to explain, and an owner pausing their own agent needs
    none beyond having done it.
    """
    return await _view(
        application,
        workspace_id,
        await application.instances.pause(
            workspace_id, instance_id, expect_revision=body.expect_revision
        )
    )


@router.post("/{instance_id}/resume")
async def resume_agent(
    application: App, workspace_id: Workspace, instance_id: str, body: StatusChange
) -> AgentView:
    """Back to whatever it was before it was paused -- draft or live."""
    return await _view(
        application,
        workspace_id,
        await application.instances.resume(
            workspace_id, instance_id, expect_revision=body.expect_revision
        )
    )


@router.put("/{instance_id}/deployment")
async def set_deployment(
    application: App, workspace_id: Workspace, instance_id: str, body: DeploymentBody
) -> AgentView:
    return await _view(
        application,
        workspace_id,
        await application.instances.set_deployment(
            workspace_id,
            instance_id,
            Deployment(
                allowed_origins=tuple(body.allowed_origins),
                widget_enabled=body.widget_enabled,
                launcher_position=body.launcher_position,
                launcher_text=body.launcher_text,
            ),
            expect_revision=body.expect_revision,
        )
    )


@router.delete("/{instance_id}")
async def delete_agent(
    application: App,
    workspace_id: Workspace,
    instance_id: str,
    expect_revision: int = Query(ge=0, alias="expectRevision"),
) -> AgentView:
    """Mark it deleted. Nothing is removed.

    A deleted agent still has conversations, leads and a transcript someone
    may need to read. The revision is a query parameter because a DELETE has
    no body in most clients, and it is still required: deleting an agent
    somebody else just edited should fail.
    """
    return await _view(
        application,
        workspace_id,
        await application.instances.delete(
            workspace_id, instance_id, expect_revision=expect_revision
        )
    )


# -- previewing the draft ------------------------------------------------


@router.post("/{instance_id}/preview/conversations", status_code=201)
async def start_preview(
    application: App,
    workspace_id: Workspace,
    instance_id: str,
    body: StartPreview,
) -> PreviewConversationView:
    """Open a conversation against the *draft*.

    Status is not checked, which is the point: previewing something not yet
    live is what this is for. The conversation is stored like any other, with
    no instance version -- which is both honest and how a preview is told
    apart from real traffic later.
    """
    agent = await resolve_draft(
        workspace_id=workspace_id,
        instance_id=instance_id,
        templates=application.stores.templates,
        instances=application.stores.instances,
        registry=application.registry,
        cache=application.cache,
    )
    conversation = await application.conversations.start(
        agent=agent,
        channel=Channel.WEB_CHAT,
        page_url=body.page_url,
        page_title=body.page_title,
    )
    return PreviewConversationView(
        conversation_id=conversation.id,
        instance_version=conversation.instance_version,
        template_version=conversation.template_version,
    )


@router.post("/{instance_id}/preview/conversations/{conversation_id}/messages")
async def say_to_preview(
    application: App,
    workspace_id: Workspace,
    instance_id: str,
    conversation_id: str,
    body: SayMessage,
) -> ReplyView:
    """One message to the draft. Costs real tokens, like a real turn does."""
    agent = await resolve_draft(
        workspace_id=workspace_id,
        instance_id=instance_id,
        templates=application.stores.templates,
        instances=application.stores.instances,
        registry=application.registry,
        cache=application.cache,
    )
    return reply_view(
        await application.conversations.say(
            agent=agent,
            conversation_id=conversation_id,
            message=body.message,
            services=application.services,
            company=application.company,
        )
    )
