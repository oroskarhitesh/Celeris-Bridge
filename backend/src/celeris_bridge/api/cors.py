"""
Two trust boundaries, so two CORS policies.

The admin API is called by one application on one known origin, and it is the
one that can delete a workspace's knowledge, so its allow-list is explicit and
empty by default: a browser on an origin nobody configured gets nothing.

The widget is the opposite. It is embedded on customers' websites, which are
by definition origins this service has never heard of, so its policy has to
answer every origin. That is safe for exactly one reason, and it is a reason
and not a hope: **the widget routes never allow credentials.** Without
``Access-Control-Allow-Credentials`` a browser attaches no cookie and no
authorization header, so a hostile page scripting these endpoints learns only
what it could have learned by calling them from its own server -- which is a
public chat widget answering a question.

What actually limits where an agent may be embedded is its own
``allowed_origins``, checked in the conversation service on every message. It
is worth being precise about what that buys: the ``Origin`` header is set by
the browser and cannot be forged *by a page*, but anything that is not a
browser can send whatever it likes. The list is therefore a deployment
control -- it keeps a customer's agent off sites it does not belong on -- and
not authentication. Treating it as authentication is how an endpoint ends up
with no other defence.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

__all__ = ["WidgetCors"]

#: Allowed on the widget routes. Deliberately short: the widget sends JSON and
#: nothing else, so there is no reason to accept a custom header that a page
#: could use to reach something.
_ALLOWED_HEADERS = "content-type"
_ALLOWED_METHODS = "GET, POST, OPTIONS"


class WidgetCors(BaseHTTPMiddleware):
    """Answer any origin, for the widget paths only.

    Scoped by path rather than applied to the whole app, because a wildcard
    that reaches the admin routes is the mistake this file exists to prevent.
    """

    def __init__(self, app, *, prefix: str) -> None:
        super().__init__(app)
        self._prefix = prefix

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith(self._prefix):
            return await call_next(request)

        if request.method == "OPTIONS":
            # Answered here rather than by the router, which would call this a
            # 405: a preflight is a question about the policy, and the policy
            # lives in this file.
            response: Response = Response(status_code=204)
        else:
            response = await call_next(request)

        origin = request.headers.get("origin", "")
        response.headers["access-control-allow-origin"] = origin or "*"
        # Because the answer depends on the request's origin, a cache that
        # forgets to vary on it would serve one site's headers to another.
        response.headers["vary"] = "Origin"
        response.headers["access-control-allow-headers"] = _ALLOWED_HEADERS
        response.headers["access-control-allow-methods"] = _ALLOWED_METHODS
        response.headers["access-control-max-age"] = "600"
        return response
