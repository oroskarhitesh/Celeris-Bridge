"""
Records to documents and back.

Pure, and separate from the queries on purpose: this is where the bugs are.
A query that is wrong fails loudly on the first call; a mapping that is wrong
loses a field quietly and is noticed a week later when someone's setting
reverts. So all of it is testable without a database, and it is.

Two rules throughout.

**Ids are strings above this line.** ``_id`` becomes ``id``; nothing in the
domain imports ``bson``. A stored id that is not an ObjectId — a seeded
fixture, another database's key — is kept as the string it is rather than
rejected.

**Reading is forgiving, writing is exact.** A document written by an older
build is missing fields this one expects, and refusing to read it takes an
agent off a website. Every ``from_document`` fills a default; every
``to_document`` writes the field.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from bson import ObjectId
from bson.errors import InvalidId

from celeris_bridge.domain.agent.overrides import AbilityOverride, Overrides
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
from celeris_bridge.domain.conversation import Channel, Conversation, Speaker, Turn

__all__ = [
    "deployment_from_document",
    "deployment_to_document",
    "instance_from_document",
    "instance_to_document",
    "instance_version_from_document",
    "instance_version_to_document",
    "conversation_from_document",
    "conversation_to_document",
    "identifier",
    "object_id",
    "overrides_from_document",
    "overrides_to_document",
    "template_from_document",
    "template_to_document",
    "template_version_from_document",
    "template_version_to_document",
    "turn_from_document",
    "turn_to_document",
]


def identifier(value: Any) -> str:
    """A stored ``_id`` as the domain's string id."""
    return str(value) if value is not None else ""


def object_id(value: str) -> Any:
    """A domain id as something Mongo can match on.

    A value that is not an ObjectId is passed through unchanged rather than
    raising: seeded fixtures and imported data have their own keys, and a
    lookup that cannot match should return nothing, not crash.
    """
    try:
        return ObjectId(value)
    except (InvalidId, TypeError, ValueError):
        return value


def _text(document: Mapping[str, Any], key: str, default: str = "") -> str:
    value = document.get(key)
    return str(value) if value is not None else default


def _number(document: Mapping[str, Any], key: str, default: int = 0) -> int:
    value = document.get(key)
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _optional_number(document: Mapping[str, Any], key: str) -> int | None:
    value = document.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _moment(document: Mapping[str, Any], key: str) -> datetime | None:
    """A stored date, always timezone-aware.

    Mongo stores UTC and hands it back naive. A naive datetime compared against
    an aware one raises, and the comparison that matters is "is this booking in
    the past" — so the tzinfo is put back on the way in, once, here.
    """
    value = document.get(key)
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _mapping(document: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = document.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _enum(kind: Any, value: Any, default: Any) -> Any:
    try:
        return kind(value)
    except ValueError:
        # A status this build does not know, written by a newer one. Falling
        # back beats refusing to read the record.
        return default


# --------------------------------------------------------------------------- #
# Overrides
# --------------------------------------------------------------------------- #


def overrides_to_document(overrides: Overrides) -> dict[str, Any]:
    return {
        "settings": dict(overrides.settings),
        "abilities": {
            key: {
                **({"enabled": change.enabled} if change.enabled is not None else {}),
                "settings": dict(change.settings),
            }
            for key, change in overrides.abilities.items()
        },
    }


def overrides_from_document(document: Any) -> Overrides:
    if not isinstance(document, Mapping):
        return Overrides()
    abilities: dict[str, AbilityOverride] = {}
    raw = document.get("abilities")
    if isinstance(raw, Mapping):
        for key, change in raw.items():
            if not isinstance(change, Mapping):
                continue
            enabled = change.get("enabled")
            abilities[str(key)] = AbilityOverride(
                enabled=enabled if isinstance(enabled, bool) else None,
                settings=dict(change.get("settings") or {}),
            )
    settings = document.get("settings")
    return Overrides(
        settings=dict(settings) if isinstance(settings, Mapping) else {},
        abilities=abilities,
    )


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


def template_to_document(template: AgentTemplate) -> dict[str, Any]:
    return {
        "_id": template.key,  # the key *is* the id: permanent, and unique by nature
        "status": str(template.status),
        "draft": dict(template.draft),
        "draftRevision": template.draft_revision,
        "draftUpdatedAt": template.draft_updated_at,
        "latestVersion": template.latest_version,
        "lastPublishedAt": template.last_published_at,
        "copiedFrom": template.copied_from,
        "createdAt": template.created_at,
        "updatedAt": template.updated_at,
    }


def template_from_document(document: Mapping[str, Any]) -> AgentTemplate:
    return AgentTemplate(
        key=_text(document, "_id"),
        draft=_mapping(document, "draft"),
        status=_enum(TemplateStatus, document.get("status"), TemplateStatus.ACTIVE),
        draft_revision=_number(document, "draftRevision"),
        draft_updated_at=_moment(document, "draftUpdatedAt"),
        latest_version=_optional_number(document, "latestVersion"),
        last_published_at=_moment(document, "lastPublishedAt"),
        copied_from=document.get("copiedFrom") or None,
        created_at=_moment(document, "createdAt"),
        updated_at=_moment(document, "updatedAt"),
    )


def template_version_to_document(version: AgentTemplateVersion) -> dict[str, Any]:
    return {
        "templateKey": version.template_key,
        "version": version.version,
        "definition": dict(version.definition),
        "changeSummary": version.change_summary,
        "publishedAt": version.published_at,
        "publishedBy": version.published_by,
    }


def template_version_from_document(
    document: Mapping[str, Any],
) -> AgentTemplateVersion:
    return AgentTemplateVersion(
        template_key=_text(document, "templateKey"),
        version=_number(document, "version"),
        definition=_mapping(document, "definition"),
        change_summary=_text(document, "changeSummary"),
        published_at=_moment(document, "publishedAt"),
        published_by=document.get("publishedBy") and str(document["publishedBy"]),
    )


# --------------------------------------------------------------------------- #
# Instances
# --------------------------------------------------------------------------- #


def deployment_to_document(deployment: Deployment) -> dict[str, Any]:
    return {
        "allowedOrigins": list(deployment.allowed_origins),
        "widgetEnabled": deployment.widget_enabled,
        "launcherPosition": deployment.launcher_position,
        "launcherText": deployment.launcher_text,
        "lastSeenAt": deployment.last_seen_at,
        "lastSeenOrigin": deployment.last_seen_origin,
    }


def deployment_from_document(document: Any) -> Deployment:
    if not isinstance(document, Mapping):
        return Deployment()
    origins = document.get("allowedOrigins")
    position = document.get("launcherPosition")
    return Deployment(
        allowed_origins=tuple(str(origin) for origin in origins or ()),
        widget_enabled=bool(document.get("widgetEnabled", True)),
        launcher_position="left" if position == "left" else "right",
        launcher_text=_text(document, "launcherText"),
        last_seen_at=_moment(document, "lastSeenAt"),
        last_seen_origin=_text(document, "lastSeenOrigin"),
    )


def instance_to_document(instance: AgentInstance) -> dict[str, Any]:
    document: dict[str, Any] = {
        "workspaceId": instance.workspace_id,
        "templateKey": instance.template_key,
        "label": instance.label,
        "status": str(instance.status),
        "pausedReason": str(instance.paused_reason) if instance.paused_reason else None,
        "pausedFrom": str(instance.paused_from) if instance.paused_from else None,
        "draftOverrides": overrides_to_document(instance.draft_overrides),
        "draftTemplateVersion": instance.draft_template_version,
        "draftRevision": instance.draft_revision,
        "deployment": deployment_to_document(instance.deployment),
        "publishedVersion": instance.published_version,
        "lastPublishedAt": instance.last_published_at,
        "createdAt": instance.created_at,
        "updatedAt": instance.updated_at,
        "deletedAt": instance.deleted_at,
    }
    if instance.id:
        document["_id"] = object_id(instance.id)
    return document


def instance_from_document(document: Mapping[str, Any]) -> AgentInstance:
    return AgentInstance(
        id=identifier(document.get("_id")),
        workspace_id=_text(document, "workspaceId"),
        template_key=_text(document, "templateKey"),
        label=_text(document, "label"),
        draft_template_version=_number(document, "draftTemplateVersion", 1),
        draft_overrides=overrides_from_document(document.get("draftOverrides")),
        draft_revision=_number(document, "draftRevision"),
        status=_enum(InstanceStatus, document.get("status"), InstanceStatus.DRAFT),
        paused_reason=(
            _enum(PausedReason, document.get("pausedReason"), None)
            if document.get("pausedReason")
            else None
        ),
        paused_from=(
            _enum(InstanceStatus, document.get("pausedFrom"), None)
            if document.get("pausedFrom")
            else None
        ),
        deployment=deployment_from_document(document.get("deployment")),
        published_version=_optional_number(document, "publishedVersion"),
        last_published_at=_moment(document, "lastPublishedAt"),
        created_at=_moment(document, "createdAt"),
        updated_at=_moment(document, "updatedAt"),
        deleted_at=_moment(document, "deletedAt"),
    )


def instance_version_to_document(version: AgentInstanceVersion) -> dict[str, Any]:
    document: dict[str, Any] = {
        "workspaceId": version.workspace_id,
        "instanceId": object_id(version.instance_id),
        "version": version.version,
        "templateKey": version.template_key,
        "templateVersion": version.template_version,
        "overrides": overrides_to_document(version.overrides),
        "changeSummary": version.change_summary,
        "rolledBackFrom": version.rolled_back_from,
        "publishedAt": version.published_at,
        "publishedBy": version.published_by,
    }
    if version.id:
        document["_id"] = object_id(version.id)
    return document


def instance_version_from_document(
    document: Mapping[str, Any],
) -> AgentInstanceVersion:
    return AgentInstanceVersion(
        id=identifier(document.get("_id")),
        workspace_id=_text(document, "workspaceId"),
        instance_id=identifier(document.get("instanceId")),
        version=_number(document, "version"),
        template_key=_text(document, "templateKey"),
        template_version=_number(document, "templateVersion", 1),
        overrides=overrides_from_document(document.get("overrides")),
        change_summary=_text(document, "changeSummary"),
        rolled_back_from=_optional_number(document, "rolledBackFrom"),
        published_at=_moment(document, "publishedAt"),
        published_by=document.get("publishedBy") and str(document["publishedBy"]),
    )


# --------------------------------------------------------------------------- #
# Conversations
# --------------------------------------------------------------------------- #


def turn_to_document(turn: Turn) -> dict[str, Any]:
    return {
        "speaker": str(turn.speaker),
        "text": turn.text,
        "at": turn.at,
        "calls": [dict(call) for call in turn.calls],
        "sources": [dict(source) for source in turn.sources],
        "ending": turn.ending,
        "model": turn.model,
        "inputTokens": turn.input_tokens,
        "outputTokens": turn.output_tokens,
    }


def turn_from_document(document: Mapping[str, Any]) -> Turn:
    return Turn(
        speaker=_enum(Speaker, document.get("speaker"), Speaker.VISITOR),
        text=_text(document, "text"),
        at=_moment(document, "at"),
        calls=tuple(
            dict(call) for call in document.get("calls") or () if isinstance(call, Mapping)
        ),
        sources=tuple(
            dict(source)
            for source in document.get("sources") or ()
            if isinstance(source, Mapping)
        ),
        ending=_text(document, "ending"),
        model=_text(document, "model"),
        input_tokens=_number(document, "inputTokens"),
        output_tokens=_number(document, "outputTokens"),
    )


def conversation_to_document(conversation: Conversation) -> dict[str, Any]:
    document: dict[str, Any] = {
        "workspaceId": conversation.workspace_id,
        "instanceId": object_id(conversation.instance_id),
        "channel": str(conversation.channel),
        "turns": [turn_to_document(turn) for turn in conversation.turns],
        "templateVersion": conversation.template_version,
        "instanceVersion": conversation.instance_version,
        "pageUrl": conversation.page_url,
        "pageTitle": conversation.page_title,
        "startedAt": conversation.started_at,
        "updatedAt": conversation.updated_at,
    }
    if conversation.id:
        document["_id"] = object_id(conversation.id)
    return document


def conversation_from_document(document: Mapping[str, Any]) -> Conversation:
    return Conversation(
        id=identifier(document.get("_id")),
        workspace_id=_text(document, "workspaceId"),
        instance_id=identifier(document.get("instanceId")),
        channel=_enum(Channel, document.get("channel"), Channel.WEB_CHAT),
        turns=tuple(
            turn_from_document(turn)
            for turn in document.get("turns") or ()
            if isinstance(turn, Mapping)
        ),
        template_version=_optional_number(document, "templateVersion"),
        instance_version=_optional_number(document, "instanceVersion"),
        page_url=_text(document, "pageUrl"),
        page_title=_text(document, "pageTitle"),
        started_at=_moment(document, "startedAt"),
        updated_at=_moment(document, "updatedAt"),
    )
