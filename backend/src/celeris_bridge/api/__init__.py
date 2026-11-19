"""
The HTTP edge.

Nothing above this package knows what a status code is, and nothing in it
makes a decision an application service could have made. What it owns is the
three things that are genuinely HTTP's problem:

**Who is asking.** A principal comes from a credential and a workspace comes
from the principal -- never from a path, a body or a header the caller can
choose. See :mod:`celeris_bridge.api.principal`.

**What an exception is worth over the wire.** One table, in
:mod:`celeris_bridge.api.errors`, so no route handler contains a ``try``. A
service exception that reaches a route unmapped becomes a 500, which is the
right answer for a bug.

**The shape of the JSON.** Requests and responses are declared types with
camelCase names, separate from the records they are built from: a response is
a contract with a browser, and it must not change because a dataclass gained
a field.
"""

from celeris_bridge.api.app import create_app

__all__ = ["create_app"]
