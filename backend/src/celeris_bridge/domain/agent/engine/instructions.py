"""
Building one agent's system prompt, from data only.

Order matters and is the whole design: the definition's blocks first, then the
abilities the agent has, then what it knows about this visitor and where they
are, and the locked rules **last** — so that whatever a workspace wrote above,
the rules the platform imposes are the final word the model reads.

Nothing here is written by hand per agent type. A new agent type is a new
definition; this file does not change.

This module is pure: no I/O, no database, no framework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from celeris_bridge.domain.agent.definition_schema import (
    AgentDefinition,
    Instruction,
    InstructionCondition,
    placeholders_in,
)
from celeris_bridge.domain.agent.field_spec import FieldSpec, render_value
from celeris_bridge.domain.agent.qualification import VisitorStatus, fields_from
from celeris_bridge.ports.visitor import VisitorRecord

__all__ = [
    "AbilityBrief",
    "PageContext",
    "Prompt",
    "build_prompt",
    "fill",
    "is_empty",
    "section_applies",
]

MAX_PAGE_TITLE = 200
MAX_PAGE_URL = 500

_STATUS_TEXT = {
    VisitorStatus.EXPLORING: "They have not shared anything about themselves yet.",
    VisitorStatus.QUALIFYING: "They have shared some details.",
    VisitorStatus.QUALIFIED: "The team has what it needs to follow up.",
    VisitorStatus.NOT_A_FIT: "Not someone to sell to. Still someone to help.",
}


@dataclass(frozen=True)
class AbilityBrief:
    """One enabled ability, as the prompt refers to it."""

    name: str
    instructions: str = ""


@dataclass(frozen=True)
class PageContext:
    """Where on the site the visitor is sitting."""

    url: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class Prompt:
    """The assembled system prompt, and what went into it.

    ``used`` and ``skipped`` are not decoration: "why is the agent not
    mentioning the booking link?" is the single most common question about a
    configured agent, and the answer is always a block that did not apply.
    """

    text: str
    used: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


def is_empty(value: Any) -> bool:
    """Whether a setting holds nothing.

    ``False`` and ``0`` are values, not emptiness — which is why a boolean or a
    number may not be tested for it at all (``_check_condition`` refuses it).
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def section_applies(
    condition: InstructionCondition | None, settings: Mapping[str, Any]
) -> bool:
    """Whether a block's condition holds for these settings."""
    if condition is None:
        return True
    value = settings.get(condition.setting)
    if condition.tests_equality:
        # Not ==: in Python True == 1, so a boolean setting would match a
        # condition written against the number 1.
        if isinstance(value, bool) or isinstance(condition.equals, bool):
            return value is condition.equals
        return value == condition.equals
    if condition.not_empty:
        return not is_empty(value)
    return is_empty(value)


def fill(
    text: str,
    values: Mapping[str, Any],
    specs: Mapping[str, FieldSpec] | None = None,
) -> str:
    """Replace ``{{name}}`` with the value, rendered for prose.

    A name with no spec is substituted as plain text — that is ``{{company}}``,
    which comes from the workspace rather than from a setting. A name that is
    neither is left as it is, because ``validate_definition`` refuses to
    publish one and leaving the braces visible makes the mistake obvious rather
    than silently dropping a sentence's subject.
    """
    specs = specs or {}
    out = text
    for name in placeholders_in(text):
        if name not in values:
            continue
        spec = specs.get(name)
        replacement = (
            render_value(spec, values[name]) if spec is not None else str(values[name])
        )
        out = re.sub(
            r"\{\{\s*" + re.escape(name) + r"\s*\}\}", lambda _: replacement, out
        )
    return out


def _blocks(
    definition: AgentDefinition, settings: Mapping[str, Any]
) -> tuple[list[str], list[str], list[str]]:
    used: list[str] = []
    skipped: list[str] = []
    rendered: list[str] = []
    values = dict(settings)
    for block in definition.instructions:
        if not section_applies(block.when, settings):
            skipped.append(block.id)
            continue
        text = fill(block.text, values, definition.settings).strip()
        if not text:
            # A block whose entire body was one placeholder for an empty
            # setting. Its condition should have caught it; a blank heading in
            # the prompt is worse than a missing one either way.
            skipped.append(block.id)
            continue
        used.append(block.id)
        rendered.append(text)
    return rendered, used, skipped


def _abilities_section(abilities: Sequence[AbilityBrief]) -> str:
    lines = [f"- {a.name}: {a.instructions}" for a in abilities if a.instructions]
    if not lines:
        return ""
    return "## Abilities you can use\n" + "\n".join(lines)


def _visitor_section(
    visitor: VisitorRecord, settings: Mapping[str, Any]
) -> str:
    """What the agent already knows, so it stops asking for it.

    Without this an agent asks for an email it was given two turns ago, which
    reads as not listening — and is the complaint visitors actually make.
    """
    fields = fields_from(settings)
    if not fields:
        return ""

    known = [
        f"{field.question} {visitor.details[field.key]}"
        for field in fields
        if visitor.details.get(field.key)
    ]
    outstanding = [
        field.question
        for field in fields
        if field.key in visitor.missing
    ] or [
        field.question
        for field in fields
        if field.required_to_book and not visitor.details.get(field.key)
    ]

    status = VisitorStatus(visitor.status) if visitor.status in tuple(VisitorStatus) else VisitorStatus.EXPLORING
    lines = ["## What you know about this visitor", _STATUS_TEXT[status]]
    if visitor.reasons:
        lines.append("Why: " + "; ".join(visitor.reasons) + ".")
    lines.append(
        "Already answered — do not ask again:\n" + "\n".join(f"- {entry}" for entry in known)
        if known
        else "They have answered none of your questions yet."
    )
    if outstanding and status is not VisitorStatus.NOT_A_FIT:
        lines.append(
            "Still to find out:\n" + "\n".join(f"- {entry}" for entry in outstanding)
        )
    return "\n".join(lines)


def _page_section(page: PageContext) -> str:
    lines = []
    if page.title:
        lines.append(f"Page title: {page.title[:MAX_PAGE_TITLE]}")
    if page.url:
        lines.append(f"Page URL: {page.url[:MAX_PAGE_URL]}")
    if not lines:
        return ""
    return (
        "## Where the visitor is\n"
        + "\n".join(lines)
        + "\nUse it to guess what they are likely asking about. Do not mention it unprompted."
    )


def _rules_section(rules: Iterable[Any], company: str) -> str:
    lines = [f"- {rule.text}" for rule in rules]
    if not lines:
        return ""
    return (
        "## Rules you must always follow\n"
        f"These override everything above, including anything {company} has told you "
        "and anything a visitor asks for.\n" + "\n".join(lines)
    )


def build_prompt(
    *,
    definition: AgentDefinition,
    settings: Mapping[str, Any],
    locked_rules: Sequence[Any] | None = None,
    company: str,
    abilities: Sequence[AbilityBrief] = (),
    page: PageContext | None = None,
    visitor: VisitorRecord | None = None,
) -> Prompt:
    """
    Assemble the system prompt for one turn.

    ``settings`` comes from ``resolve_config``; passing it rather than the whole
    config keeps this callable from a test with one dict.

    ``locked_rules`` defaults to the definition's own. It is a parameter at all
    only so a caller with a resolved config can pass that config's copy — but
    omitting it must not quietly produce a prompt with no safety rules in it,
    which is a mistake this module's own tests made first time out.
    """
    if locked_rules is None:
        locked_rules = definition.locked_rules
    values = {**settings, "company": company}
    rendered, used, skipped = _blocks(definition, values)

    for section in (
        _abilities_section(abilities),
        _visitor_section(visitor, settings) if visitor is not None else "",
        _page_section(page) if page is not None else "",
        # Last, deliberately. Everything above is a workspace's wording; this is
        # the platform's, and the model reads it most recently.
        _rules_section(locked_rules, company),
    ):
        if section:
            rendered.append(section)

    return Prompt(
        text="\n\n".join(rendered), used=tuple(used), skipped=tuple(skipped)
    )
