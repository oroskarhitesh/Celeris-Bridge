"""
The connection.

One client per process, because a client is a connection pool and making one
per request is how a service runs out of sockets under load.
"""

from __future__ import annotations

import logging
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

__all__ = ["connect", "ping"]

logger = logging.getLogger(__name__)


def connect(url: str, database: str, **options: Any) -> tuple[Any, AsyncDatabase]:
    """A client and a database handle.

    ``AsyncMongoClient`` rather than motor: motor is deprecated, and PyMongo's
    own async client is the supported path. Connecting is lazy, so this does
    not fail on an unreachable server -- call :func:`ping` when you want to
    know.

    **Call this on the event loop that will use it.** The client binds to the
    loop it is first awaited on and refuses to be used from another, with
    "Cannot use AsyncMongoClient in different event loop". Building one at
    import time and serving requests on a different loop therefore fails on
    the first query, not at construction -- so under a web server this belongs
    in a startup hook, and in a script inside the ``asyncio.run`` that uses it.
    """
    client: Any = AsyncMongoClient(
        url,
        tz_aware=True,  # so a stored date comes back comparable to datetime.now(utc)
        **options,
    )
    return client, client[database]


async def ping(database: AsyncDatabase) -> bool:
    """Whether the server answers. For a health check, and for startup."""
    try:
        await database.command("ping")
        return True
    except Exception:
        logger.exception("Could not reach Mongo")
        return False
