"""
The routers, one per thing a client talks about.

Ordered here the way the product is: what the platform owns, what a workspace
owns, and what a visitor can reach. The last of those is the one with no
credential, and keeping it in its own module is deliberate -- a public route
written next to an authenticated one is a public route somebody will later
assume is authenticated.
"""

from celeris_bridge.api.routes import (
    agents,
    catalogue,
    conversations,
    health,
    knowledge,
    leads,
    templates,
    widget,
)

#: Every router, in the order they are mounted.
ROUTERS = (
    health.router,
    templates.router,
    catalogue.router,
    agents.router,
    knowledge.router,
    conversations.router,
    leads.router,
    widget.router,
)

__all__ = ["ROUTERS"]
