"""
The application object.

A factory and not a module-level ``app``, for one concrete reason: the Mongo
client binds to the event loop it is first awaited on and refuses to be used
from another. Building the adapters at import time therefore works, starts,
serves one request and fails -- so everything is built inside the lifespan,
which runs on the loop that will do the serving.

Three things happen here and nowhere else: the services are built, an
authenticator is chosen, and the routers are mounted. There is no business
logic in this file and there should never be any; a decision that lands here
has escaped a layer that could have tested it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from celeris_bridge.api.cors import WidgetCors
from celeris_bridge.api.errors import install_error_handlers
from celeris_bridge.api.principal import (
    Authenticator,
    DevWorkspace,
    StaticTokens,
    parse_tokens,
)
from celeris_bridge.api.routes import ROUTERS
from celeris_bridge.composition.build import build_application
from celeris_bridge.composition.settings import Settings, settings

__all__ = ["build_authenticator", "create_app"]

logger = logging.getLogger(__name__)

#: Where the widget lives. One constant, because the CORS policy and the
#: router have to agree about it and a typo would silently leave the public
#: path without its headers.
WIDGET_PREFIX = "/v1/widget"


def build_authenticator(options: Settings) -> Authenticator:
    """The authenticator this configuration asks for.

    Tokens when there are tokens. ``CB_DEV_WORKSPACE`` otherwise, loudly, and
    only when it is set -- a build with neither answers 401 to every admin
    request, which is the right failure: visible on the first call, fixed
    without a deploy, and impossible to mistake for working.
    """
    tokens = parse_tokens(options.api_tokens)
    if tokens:
        if options.dev_workspace:
            logger.warning(
                "CB_DEV_WORKSPACE is set and is being ignored: CB_API_TOKENS wins."
            )
        logger.info("Authenticating with %d configured token(s)", len(tokens))
        return StaticTokens(tokens)

    if options.dev_workspace:
        logger.warning(
            "No CB_API_TOKENS: every request is being treated as an admin of "
            "workspace %r. This is not authentication and must not be used "
            "anywhere a stranger can reach.",
            options.dev_workspace,
        )
        return DevWorkspace(options.dev_workspace)

    logger.warning(
        "No CB_API_TOKENS and no CB_DEV_WORKSPACE: the admin API will refuse "
        "every request. The widget routes still work."
    )
    return StaticTokens({})


def create_app(options: Settings | None = None) -> FastAPI:
    """The API, wired for this configuration."""
    options = options or settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # On the serving loop. See the module docstring.
        app.state.application = build_application(options)
        app.state.authenticator = build_authenticator(options)

        database = app.state.application.stores.database
        if database is not None:
            from celeris_bridge.adapters.mongo import ensure_indexes

            # Idempotent, and two of these are correctness rather than speed,
            # so it runs on every start rather than in a migration somebody
            # forgets to deploy.
            await ensure_indexes(database)

        yield

        if database is not None:
            # Best effort: the process is going away either way, and a failure
            # to close a pool must not turn a clean shutdown into a stack
            # trace in a container log.
            try:
                await database.client.close()
            except Exception:
                logger.warning("Could not close the Mongo client cleanly")

    app = FastAPI(
        title="Celeris Bridge",
        version="0.1.0",
        summary="Agents a workspace configures, and the visitors they talk to.",
        lifespan=lifespan,
    )

    install_error_handlers(app)

    # The widget first: middleware runs outermost-last, and this one has to
    # see the response that the admin policy never touches.
    app.add_middleware(WidgetCors, prefix=WIDGET_PREFIX)

    admin_origins = [
        origin.strip() for origin in options.admin_origins.split(",") if origin.strip()
    ]
    if admin_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=admin_origins,
            # The admin app sends a bearer token, so credentials are allowed
            # here -- which is exactly why the list is explicit and never a
            # wildcard. A wildcard with credentials is rejected by browsers
            # anyway, and the reason is this one.
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["authorization", "content-type"],
        )

    for router in ROUTERS:
        app.include_router(router)

    return app
