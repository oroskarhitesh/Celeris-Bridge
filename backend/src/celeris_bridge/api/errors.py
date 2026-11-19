"""
What an exception is worth over HTTP. One table, and this is the only file
that knows.

The services raise their own exceptions and name no status code; this maps
them. Keeping the mapping here rather than in the handlers has two effects
worth the file:

* **No route contains a ``try``.** A handler that catches and re-raises as an
  HTTP error is a handler that will be copied, and the copy will use 400 where
  the original used 409.
* **An unmapped exception is a 500.** That is the correct answer for a bug,
  and the loud one: a new service exception does not quietly arrive at a
  client as a 400 that a frontend then learns to handle.

Every response body has the same shape, including the ones FastAPI raises
itself::

    {"error": {"code": "AGENT_NOT_LIVE", "message": "...", "details": {...}}}

``code`` is a stable string a client may branch on. ``message`` is prose for a
person: for a mapped exception it is the exception's own words, which are
written to be read, and for a 500 it is deliberately nothing -- an internal
failure's text is for the log, not for a stranger.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from celeris_bridge.adapters.local_documents import UnsafeIdentifier
from celeris_bridge.adapters.mongo.stores import AlreadyExists, MissingRecord
from celeris_bridge.domain.agent.field_spec import InvalidSettingValue
from celeris_bridge.domain.agent.overrides import OverridesRejected
from celeris_bridge.domain.agent.records import RevisionConflict
from celeris_bridge.domain.knowledge.extraction import (
    UnreadableDocument,
    UnsupportedDocument,
)
from celeris_bridge.domain.knowledge.ingestion import DocumentTooLarge
from celeris_bridge.domain.knowledge.retrieval import CrossTenantPassage
from celeris_bridge.domain.knowledge.urls import UnsafeUrl
from celeris_bridge.ports.documents import MissingDocument
from celeris_bridge.ports.web import FetchFailed
from celeris_bridge.services.conversations import ConversationTooLong, OriginNotAllowed
from celeris_bridge.services.instances import InstanceLimitReached, NothingToPublish
from celeris_bridge.services.knowledge import SourceNotFound
from celeris_bridge.services.resolution import (
    AgentNotFound,
    AgentNotLive,
    BrokenDefinition,
)
from celeris_bridge.services.templates import (
    DefinitionInvalid,
    TemplateNotFound,
    TemplateNotPublishable,
)

__all__ = [
    "ApiError",
    "Forbidden",
    "Unauthenticated",
    "install_error_handlers",
    "problem",
]

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """A refusal the API itself makes, before any service is called.

    Authentication and authorisation only. Everything else is a service's
    decision and arrives here as that service's exception -- a route that
    raises this for a domain reason has put a rule in the wrong layer.
    """

    def __init__(
        self,
        *,
        status: int,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.details = dict(details or {})
        super().__init__(message)


class Unauthenticated(ApiError):
    """No usable credential."""

    def __init__(self, message: str = "This endpoint needs a bearer token") -> None:
        super().__init__(status=401, code="UNAUTHENTICATED", message=message)


class Forbidden(ApiError):
    """A credential that is not allowed to do this."""

    def __init__(self, message: str = "This token may not do that") -> None:
        super().__init__(status=403, code="FORBIDDEN", message=message)


def _body(code: str, message: str, details: Mapping[str, Any] | None = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = dict(details)
    return {"error": error}


def problem(
    status: int, code: str, message: str, details: Mapping[str, Any] | None = None
) -> JSONResponse:
    """The envelope, as a response. The only place one is constructed."""
    return JSONResponse(status_code=status, content=_body(code, message, details))


#: The plain cases: an exception type, the status it is worth, and the code a
#: client sees. Ordered most specific first for a reader's benefit only --
#: dispatch walks the exception's own class hierarchy, so a subclass with its
#: own row always wins over its parent's.
_SIMPLE: tuple[tuple[type[BaseException], int, str], ...] = (
    # 400 -- the request asked for something the product does not accept.
    (UnsafeUrl, 400, "UNSAFE_URL"),
    (UnsafeIdentifier, 400, "UNSAFE_IDENTIFIER"),
    (InvalidSettingValue, 400, "SETTING_INVALID"),
    # 403 -- a page that is not allowed to use this agent. Distinct from 401:
    # the widget has no credential to fix, so retrying with one is not advice.
    (OriginNotAllowed, 403, "ORIGIN_NOT_ALLOWED"),
    # 404 -- and the same answer for "not yours", which is why the services
    # raise one exception for both.
    (AgentNotFound, 404, "AGENT_NOT_FOUND"),
    (TemplateNotFound, 404, "TEMPLATE_NOT_FOUND"),
    (SourceNotFound, 404, "SOURCE_NOT_FOUND"),
    (MissingDocument, 404, "DOCUMENT_MISSING"),
    (MissingRecord, 404, "NOT_FOUND"),
    # 409 -- the request was understood and the record is not in a state that
    # allows it. Nothing the caller got wrong; something they must be told.
    (NothingToPublish, 409, "NOTHING_TO_PUBLISH"),
    (TemplateNotPublishable, 409, "TEMPLATE_NOT_PUBLISHABLE"),
    (AlreadyExists, 409, "ALREADY_EXISTS"),
    (UnsupportedDocument, 415, "DOCUMENT_UNSUPPORTED"),
    (UnreadableDocument, 422, "DOCUMENT_UNREADABLE"),
    # 502 -- someone else's server, not ours. A customer's site refusing the
    # crawler is not a bug in this service and must not read like one.
    (FetchFailed, 502, "FETCH_FAILED"),
    # The widest net, last: every "no such thing" in the services is a
    # LookupError, so a new one is a 404 rather than a 500 on the day it is
    # written.
    (LookupError, 404, "NOT_FOUND"),
)


def install_error_handlers(app: FastAPI) -> None:
    """Register every handler. Called once, by :func:`create_app`."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return problem(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(OverridesRejected)
    async def _rejected(_: Request, exc: OverridesRejected) -> JSONResponse:
        # Every rejection, not the first, so a settings form can mark all of
        # its bad fields at once. The path is the field's own address and the
        # code is stable, which is what lets the form do that without parsing
        # prose.
        return problem(
            400,
            "OVERRIDES_REJECTED",
            "Some of those values were not accepted",
            {
                "rejections": [
                    {"path": one.path, "code": str(one.code), "message": one.message}
                    for one in exc.rejections
                ]
            },
        )

    @app.exception_handler(DefinitionInvalid)
    async def _definition_invalid(_: Request, exc: DefinitionInvalid) -> JSONResponse:
        return problem(
            400,
            "DEFINITION_INVALID",
            "That definition cannot be published",
            {
                "key": exc.key,
                "problems": [
                    {"path": path, "message": message} for path, message in exc.problems
                ],
            },
        )

    @app.exception_handler(RevisionConflict)
    async def _conflict(_: Request, exc: RevisionConflict) -> JSONResponse:
        # Both numbers travel, so a form can say "reload" and know how far
        # behind it is rather than guessing that its save vanished.
        return problem(
            409,
            "REVISION_CONFLICT",
            str(exc),
            {"expected": exc.expected, "found": exc.found},
        )

    @app.exception_handler(AgentNotLive)
    async def _not_live(_: Request, exc: AgentNotLive) -> JSONResponse:
        # The widget needs different words for never-published, paused by its
        # owner and paused by us, so both fields travel and this decides
        # nothing.
        return problem(
            409,
            "AGENT_NOT_LIVE",
            str(exc),
            {"status": exc.status, "reason": exc.reason},
        )

    @app.exception_handler(ConversationTooLong)
    async def _too_long(_: Request, exc: ConversationTooLong) -> JSONResponse:
        # Not really an error: the conversation is over and the widget shows
        # its hand-off copy. A 409 rather than a 200 so a client cannot
        # mistake it for a reply -- and the visitor's message was stored
        # either way.
        return problem(409, "CONVERSATION_TOO_LONG", str(exc), {"limit": exc.limit})

    @app.exception_handler(InstanceLimitReached)
    async def _limit(_: Request, exc: InstanceLimitReached) -> JSONResponse:
        return problem(
            409,
            "AGENT_LIMIT_REACHED",
            str(exc),
            {"templateKey": exc.template_key, "limit": exc.limit},
        )

    @app.exception_handler(DocumentTooLarge)
    async def _too_large(_: Request, exc: DocumentTooLarge) -> JSONResponse:
        return problem(413, "DOCUMENT_TOO_LARGE", str(exc))

    @app.exception_handler(BrokenDefinition)
    async def _broken(_: Request, exc: BrokenDefinition) -> JSONResponse:
        # 500, because nothing the caller did caused it and no retry fixes it:
        # a published version stopped validating against this build, which
        # means live traffic is failing until someone looks.
        logger.error(
            "Serving %s v%s failed: %s", exc.key, exc.version, "; ".join(exc.problems)
        )
        return problem(
            500,
            "DEFINITION_BROKEN",
            "This agent cannot be served by this build",
            {"templateKey": exc.key, "templateVersion": exc.version},
        )

    @app.exception_handler(CrossTenantPassage)
    async def _cross_tenant(_: Request, exc: CrossTenantPassage) -> JSONResponse:
        # The one failure here that is an incident rather than a fault. A
        # passage from another workspace reached a retrieval scoped to this
        # one, so the request is refused with nothing in it and the line is
        # written at a level that pages somebody.
        logger.critical("Cross-tenant passage reached retrieval: %s", exc)
        return problem(500, "INTERNAL", "Something went wrong")

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's own 422, in this envelope so a client has one error shape
        # to parse. The location is kept as it comes: it is the only thing
        # that says *which* field, including for a rejected unknown one.
        return problem(
            422,
            "VALIDATION_FAILED",
            "That request is not valid",
            {
                "problems": [
                    {
                        "path": ".".join(str(part) for part in one.get("loc", ())),
                        "message": one.get("msg", ""),
                    }
                    for one in exc.errors()
                ]
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # A route that does not exist, or a method that is not allowed. Same
        # envelope, so a client's error handling does not have two branches.
        return problem(
            exc.status_code, "HTTP_ERROR", str(exc.detail) if exc.detail else ""
        )

    for kind, status, code in _SIMPLE:
        app.add_exception_handler(kind, _simple_handler(status, code))


def _simple_handler(status: int, code: str):
    """One handler for the table above.

    A 5xx never carries the exception's words: whatever it says was written
    for whoever has to fix it, and a stranger reading it learns only which
    library this service uses.
    """

    async def handler(_: Request, exc: Exception) -> JSONResponse:
        if status >= 500:
            logger.exception("%s", code)
            return problem(status, code, "Something went wrong")
        return problem(status, code, str(exc))

    return handler
