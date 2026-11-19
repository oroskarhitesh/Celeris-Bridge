"""
Is it up, and can it serve.

Two endpoints and not one, because an orchestrator asks two different
questions and giving it the same answer to both causes an outage.

``/health`` is liveness: does this process respond. It touches nothing --
no database, no provider -- because a liveness check that queries a database
restarts every healthy process in the fleet the moment the database has a bad
minute, which turns a brief degradation into one with no servers left.

``/readyz`` is readiness: can this process serve traffic right now. That one
does ping the database, and a failure takes the process out of the load
balancer without killing it, which is exactly the right response to a
dependency that is coming back.
"""

from __future__ import annotations

from fastapi import APIRouter

from celeris_bridge.api.dependencies import App
from celeris_bridge.api.errors import problem

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness. Deliberately does nothing."""
    return {"status": "ok"}


@router.get("/readyz")
async def ready(application: App):
    """Readiness: whether the records are reachable.

    Only meaningful under Mongo. The in-memory build has nothing to be
    unreachable, so it is ready as soon as it answers -- which is honest
    rather than a stub: there is no failure mode being hidden.
    """
    database = application.stores.database
    if database is None:
        return {"status": "ready", "records": "memory"}

    from celeris_bridge.adapters.mongo import ping

    if not await ping(database):
        return problem(503, "NOT_READY", "The database is not answering")
    return {"status": "ready", "records": "mongo"}
