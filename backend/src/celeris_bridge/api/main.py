"""
The entry point a server runs.

    uvicorn celeris_bridge.api.main:app --port 8000

A module-level app is safe here even though the adapters must be built on the
serving loop: create_app only reads settings and mounts routers, and
everything that touches a connection is built inside the lifespan. See
api/app.py.
"""

from __future__ import annotations

from celeris_bridge.api.app import create_app

__all__ = ["app"]

app = create_app()
