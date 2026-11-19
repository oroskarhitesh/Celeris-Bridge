"""
Application services: the layer between HTTP and the domain.

Each module here owns one subject -- agent types, a workspace's agents, the
conversation, knowledge, leads -- and each one takes the ports it needs as
arguments. Nothing in here imports FastAPI, and nothing in here knows a status
code: a service raises its own exception and the API layer decides what that is
worth over HTTP.

What makes this a layer rather than a pile of helpers is that the invariants
spanning two records live here and nowhere else. "A definition is validated
before it is published", "an instance pins a template version and only moves
off it when asked", "a conversation's turns are appended in the order they
happened" are each true of a pair of writes, so they cannot be enforced by
either record alone and must not be left to a route handler.
"""
