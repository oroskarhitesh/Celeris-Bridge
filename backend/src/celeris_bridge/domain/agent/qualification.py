"""
Where a visitor stands — decided here, in code, from the workspace's settings.

The model reports what the visitor *said*. It never decides whether they are a
fit, because that decision drives who the sales team calls and must not vary
with the model's mood, the phrasing of a sentence, or a visitor who insists.

The checks follow each field's declared ``format``, not its name. The reference
keys them off the field key — ``if (key === "email")`` — so a workspace that
renames the field to ``work_email`` quietly loses email validation and starts
collecting "yes please" as an address. A declaration cannot drift that way.

This module is pure: no I/O, no database, no framework.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Accepted",
    "Assessment",
    "DETAIL_FORMATS",
    "FREE_EMAIL_DOMAINS",
    "MAX_DETAIL_CHARACTERS",
    "QualificationField",
    "Rejected",
    "VISITOR_TYPES",
    "VisitorStatus",
    "assess",
    "check_detail",
    "fields_from",
    "parse_quantity",
]

#: Who a visitor turns out to be. A marketing agent meets all of these.
VISITOR_TYPES = (
    "prospect",
    "existing_customer",
    "student",
    "job_seeker",
    "vendor",
    "competitor",
    "unknown",
)

#: How a detail is checked. Adding one is a code change here plus an option in
#: the definition's item spec — which is the point: a format the checks do not
#: know about cannot be declared.
DETAIL_FORMATS = ("text", "email", "number")

#: Long enough for a company name or a sentence about a timeline.
MAX_DETAIL_CHARACTERS = 300

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_QUANTITY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(k)?", re.IGNORECASE)

#: Personal mailboxes, for a workspace that only wants work addresses.
FREE_EMAIL_DOMAINS = frozenset(
    {
        "aol.com",
        "gmail.com",
        "googlemail.com",
        "gmx.com",
        "gmx.de",
        "hotmail.com",
        "icloud.com",
        "live.com",
        "mac.com",
        "mail.com",
        "me.com",
        "msn.com",
        "outlook.com",
        "proton.me",
        "protonmail.com",
        "qq.com",
        "rediffmail.com",
        "yahoo.co.in",
        "yahoo.co.uk",
        "yahoo.com",
        "yandex.com",
        "zoho.com",
        "163.com",
    }
)


class VisitorStatus(StrEnum):
    """How far a conversation has got."""

    #: Nothing shared yet.
    EXPLORING = "exploring"
    #: Something shared, something required still missing.
    QUALIFYING = "qualifying"
    #: Everything required is in hand.
    QUALIFIED = "qualified"
    #: Not someone to sell to. Still someone to help.
    NOT_A_FIT = "not_a_fit"


@dataclass(frozen=True)
class QualificationField:
    """One detail the workspace wants, as declared in its settings."""

    key: str
    question: str
    required_to_book: bool
    format: str = "text"


@dataclass(frozen=True)
class Accepted:
    """The detail is usable, in the form it will be stored."""

    value: str


@dataclass(frozen=True)
class Rejected:
    """The detail cannot be stored. ``reason`` is addressed to the model.

    Phrased as an instruction rather than a complaint: "ask the visitor to
    check it" gets a useful next turn, "invalid email" gets the same bad value
    sent again.
    """

    reason: str


@dataclass(frozen=True)
class Assessment:
    """Where the visitor stands, and why."""

    status: VisitorStatus
    missing: tuple[QualificationField, ...] = ()
    reasons: tuple[str, ...] = ()


def fields_from(settings: Mapping[str, Any]) -> tuple[QualificationField, ...]:
    """The qualification fields declared in an agent's resolved settings.

    Tolerant of a row that is missing something: these settings have been
    through ``validate_value`` already, but this is also reachable from a
    conversation that started before an admin changed the definition.
    """
    rows = settings.get("qualificationFields")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return ()
    out: list[QualificationField] = []
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("key"):
            continue
        out.append(
            QualificationField(
                key=str(row["key"]),
                question=str(row.get("question") or row["key"]),
                required_to_book=bool(row.get("requiredToBook")),
                format=str(row.get("format") or "text"),
            )
        )
    return tuple(out)


def parse_quantity(value: str) -> int | None:
    """``"about 40"``, ``"40-50 people"``, ``"1.5k"`` → 40, 40, 1500.

    Visitors answer "how many people?" in prose. Refusing anything that is not
    a bare integer throws away most real answers.
    """
    match = _QUANTITY_RE.search(value.replace(",", ""))
    if match is None:
        return None
    number = float(match.group(1)) * (1000 if match.group(2) else 1)
    rounded = round(number)
    return rounded if rounded > 0 else None


def check_detail(
    field: QualificationField, raw: Any, *, work_email_only: bool = False
) -> Accepted | Rejected:
    """Check one detail the model wants to save."""
    value = " ".join(str(raw).split())[:MAX_DETAIL_CHARACTERS]
    if not value:
        return Rejected(f"{field.question} came through empty")

    if field.format == "email":
        email = value.lower()
        if not _EMAIL_RE.match(email):
            return Rejected(
                "That does not look like an email address — ask the visitor to check it"
            )
        if work_email_only and email.split("@")[1] in FREE_EMAIL_DOMAINS:
            return Rejected(
                "This company only accepts work email addresses — politely ask for one"
            )
        return Accepted(email)

    if field.format == "number":
        if parse_quantity(value) is None:
            return Rejected(f"{field.question} needs a number in the answer")
        return Accepted(value)

    return Accepted(value)


def assess(
    *,
    fields: Iterable[QualificationField],
    details: Mapping[str, str],
    visitor_type: str = "unknown",
    not_a_fit_types: Iterable[str] = (),
) -> Assessment:
    """Where this visitor stands, from what is known and the workspace's rules."""
    excluded = set(not_a_fit_types)
    reasons: list[str] = []
    if visitor_type in excluded and visitor_type != "unknown":
        reasons.append(f"The visitor is a {visitor_type.replace('_', ' ')}")

    missing = tuple(
        field
        for field in fields
        if field.required_to_book and not details.get(field.key)
    )

    if reasons:
        status = VisitorStatus.NOT_A_FIT
    elif not missing:
        # Nothing required is outstanding. With no required fields declared at
        # all this is reached immediately, which is correct: a workspace that
        # asks for nothing has nothing to wait for.
        status = VisitorStatus.QUALIFIED
    elif details:
        status = VisitorStatus.QUALIFYING
    else:
        status = VisitorStatus.EXPLORING

    return Assessment(status=status, missing=missing, reasons=tuple(reasons))
