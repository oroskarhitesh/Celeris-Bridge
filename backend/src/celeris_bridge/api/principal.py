"""
Who is asking, and which workspace that means.

The rule this module exists to make structural: **a workspace id is derived
from a credential and never read from the request.** Not from a body, not from
a query string, not from a header a caller can set. Every admin route takes
its workspace from :class:`Principal`, which only an authenticator can
produce, so a route cannot be written the other way round by accident -- there
is no workspace field in scope to reach for.

The reason is that the alternative fails quietly. An endpoint that trusts a
``workspaceId`` in the body works perfectly in every test, serves every
customer correctly for a year, and hands one of them another one's leads the
first time someone edits the number in a devtools tab.

Two authenticators ship.

:class:`StaticTokens` is real, if simple: bearer tokens configured for the
process, each naming a workspace. It is what a demo and a first deployment
use, and it is a placeholder for sessions and an identity provider -- which is
the point of the port. Swapping it is a new class, not a change to any route.

:class:`DevWorkspace` is not authentication at all and says so: it treats
every request as an admin of one workspace. It exists because a crawl and an
upload are easier to try with curl than with a token table, and it is
impossible to turn on without saying so in the environment.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

__all__ = [
    "Authenticator",
    "DevWorkspace",
    "Principal",
    "StaticTokens",
    "parse_tokens",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    """Who the credential belongs to."""

    #: The workspace every request of theirs is scoped to. Empty only for a
    #: platform principal, which has no workspace of its own.
    workspace_id: str = ""
    #: For the audit fields on a published version: who pressed publish.
    user_id: str = ""
    #: Whether they may edit agent *types*. A different job from editing an
    #: agent: a template version is published once and resolved by every
    #: workspace pinned to it, so this is the platform's own key and not a
    #: customer's.
    platform_admin: bool = False

    @property
    def has_workspace(self) -> bool:
        return bool(self.workspace_id)


@runtime_checkable
class Authenticator(Protocol):
    """Turns a bearer token into a principal, or into nothing."""

    async def identify(self, token: str) -> Principal | None:
        """``None`` for an unknown or malformed token.

        Deliberately not an exception: "no principal" is an ordinary answer
        here, and the API layer is what decides whether a route needed one.
        """
        ...


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class StaticTokens:
    """Bearer tokens configured for the process.

    The table is keyed on a SHA-256 digest rather than on the token, which
    buys two things for one line: a dictionary lookup cannot leak a token
    through timing the way a sequence of comparisons can, and a core dump or a
    debugger session on this process does not contain anybody's credential.
    """

    def __init__(self, tokens: Mapping[str, Principal]) -> None:
        self._by_digest = {_digest(token): who for token, who in tokens.items()}

    @property
    def configured(self) -> int:
        return len(self._by_digest)

    async def identify(self, token: str) -> Principal | None:
        return self._by_digest.get(_digest(token))


class DevWorkspace:
    """Every request is an admin of one workspace. Not authentication.

    Only built when ``CB_DEV_WORKSPACE`` is set, warns on every start-up, and
    grants the platform role too -- because the alternative is somebody
    discovering halfway through a demo that they cannot publish a template and
    reaching for a second workaround.
    """

    def __init__(self, workspace_id: str) -> None:
        self._principal = Principal(
            workspace_id=workspace_id, user_id="dev", platform_admin=True
        )

    async def identify(self, token: str) -> Principal | None:
        return self._principal


def parse_tokens(configured: str) -> dict[str, Principal]:
    """Read ``CB_API_TOKENS``.

    ``token:workspace`` per entry, comma separated, and a third field of
    ``platform`` grants the platform role::

        CB_API_TOKENS=tok_live_a1:ws_acme,tok_plat_b2::platform

    Malformed entries are dropped with a warning rather than raising. A
    process that refuses to start because one line of an environment variable
    is wrong is a process that cannot be fixed without a deploy; one that
    starts without that token refuses its requests with a 401, which is
    visible immediately and recoverable. The token itself is never logged.
    """
    tokens: dict[str, Principal] = {}
    for index, entry in enumerate(configured.split(",")):
        entry = entry.strip()
        if not entry:
            continue
        parts = [part.strip() for part in entry.split(":")]
        token, workspace = parts[0], parts[1] if len(parts) > 1 else ""
        role = parts[2] if len(parts) > 2 else ""
        if not token or (not workspace and role != "platform"):
            logger.warning(
                "CB_API_TOKENS entry %d is not usable and was ignored", index + 1
            )
            continue
        tokens[token] = Principal(
            workspace_id=workspace,
            user_id=workspace or "platform",
            platform_admin=role == "platform",
        )
    return tokens
