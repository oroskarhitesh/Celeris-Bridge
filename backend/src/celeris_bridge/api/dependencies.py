"""
What a route may ask for.

Four things, and the reason they are declared once here rather than taken
apart in each handler is that two of them are the tenancy rule:

``App``
    The application services, built once at start-up and kept on the app. A
    route never builds an adapter -- composition is the only layer allowed to
    know which adapters exist.
``Who``
    The authenticated principal.
``Workspace``
    **A workspace id, from the principal.** This is the type an admin route
    asks for, and it is a plain ``str`` so that writing the route is easier
    than not: the id is already in scope, correct and unforgeable, before the
    handler's first line.
``Platform``
    A principal allowed to edit agent *types*. Publishing a template version
    affects every workspace pinned to it, so it is the platform's key.

The widget routes ask for none of these except ``App``. A visitor has no
account, and what identifies the agent there is a public id in the URL --
see :mod:`celeris_bridge.api.routes.widget`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from celeris_bridge.api.errors import Forbidden, Unauthenticated
from celeris_bridge.api.principal import Principal
from celeris_bridge.composition.build import Application

__all__ = [
    "App",
    "Platform",
    "Who",
    "Workspace",
    "require_platform",
    "require_workspace",
]


def current_application(request: Request) -> Application:
    return request.app.state.application


def _bearer(header: str | None) -> str:
    """The token out of an ``Authorization`` header, or an empty string.

    Case-insensitive on the scheme, because clients vary and a 401 that is
    really a capital B is an afternoon.
    """
    if not header:
        return ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


async def current_principal(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Principal:
    """The principal, or a 401.

    The token is handed over even when it is empty, rather than short-circuited
    here: whether no credential is acceptable is the authenticator's decision,
    and that is what lets the development one exist without a branch in this
    function.
    """
    who = await request.app.state.authenticator.identify(_bearer(authorization))
    if who is None:
        raise Unauthenticated()
    return who


async def require_workspace(
    who: Annotated[Principal, Depends(current_principal)],
) -> str:
    if not who.has_workspace:
        # A platform token is not a customer: it has no workspace, so it
        # cannot read one's leads by default. Granting it one would have to be
        # an explicit act, logged, which is a feature and not this one.
        raise Forbidden("This token is not scoped to a workspace")
    return who.workspace_id


async def require_platform(
    who: Annotated[Principal, Depends(current_principal)],
) -> Principal:
    if not who.platform_admin:
        raise Forbidden("Only the platform may change agent types")
    return who


App = Annotated[Application, Depends(current_application)]
Who = Annotated[Principal, Depends(current_principal)]
Workspace = Annotated[str, Depends(require_workspace)]
Platform = Annotated[Principal, Depends(require_platform)]
