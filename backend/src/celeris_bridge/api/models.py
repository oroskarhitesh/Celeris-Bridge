"""
The two base classes every request and response here is built from.

Separate bases because they want opposite things.

``Payload`` -- what arrives -- **forbids unknown fields**. That is a security
property and not tidiness: the one thing a request body must never be able to
carry is a workspace id, and the way to guarantee a field is ignored is to
make sending it an error rather than to remember not to read it.

``View`` -- what is sent -- allows the code to grow fields without breaking a
client, and is written by hand rather than serialised from a record. A
response is a contract with a browser; a record is an internal detail. Keeping
them the same object means the day someone adds ``internal_notes`` to a
dataclass is the day it appears in an API.

Both speak camelCase on the wire and snake_case in Python, so neither side
has to spell the other's convention.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

__all__ = ["Payload", "View"]


class Payload(BaseModel):
    """A request body."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class View(BaseModel):
    """A response body."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )
