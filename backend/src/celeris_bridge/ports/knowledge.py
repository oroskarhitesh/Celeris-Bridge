"""
Ports for reading a workspace's knowledge.

A port is a ``Protocol``, not a base class. Nothing has to inherit from it: an
adapter satisfies it by having the right methods, and a test fake is a small
class with two of them. That also keeps the arrow pointing the right way — the
adapter depends on the domain, never the other way round.

Two ports rather than one. The reference has a single ``RetrievalService`` that
embeds the query, searches the store, decides which sources this agent may read,
scores, truncates and strips prompt injections. Half of that is policy with no
I/O in it, and policy behind an interface is policy you cannot test without
standing up a database. Here the ports do only what needs the outside world —
turn text into a vector, and find near vectors — and everything else lives in
:mod:`celeris_bridge.domain.knowledge.retrieval`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

__all__ = ["EmbeddingModel", "KnowledgeScope", "PassageHit", "PassageIndex"]


@dataclass(frozen=True)
class KnowledgeScope:
    """
    Whose knowledge may be read.

    Built from the authenticated principal and the agent being run — never from
    anything a model or a visitor wrote. It is passed on every search rather
    than held on the adapter, so a single long-lived adapter cannot end up
    answering with the wrong tenant's data.
    """

    workspace_id: str
    #: Which agent is asking. An agent reads its own sources plus those shared
    #: with every agent, so without it there is no way to narrow correctly.
    agent_instance_id: str | None = None


@dataclass(frozen=True)
class PassageHit:
    """One passage an index found."""

    passage_id: str
    source_id: str
    #: What the visitor is shown as the citation — a file name, a page title.
    source_name: str
    text: str
    #: Similarity, higher is nearer. Comparable only within one embedding model.
    score: float
    #: Which workspace this passage belongs to. The adapter stamps it and
    #: :func:`~celeris_bridge.domain.knowledge.retrieval.retrieve` checks it
    #: against the scope it asked for, so a filter missed in a query is caught
    #: before the text reaches a model.
    workspace_id: str
    source_url: str | None = None
    #: An answer a human approved. Used as written, ahead of better-scoring text.
    approved: bool = False


@runtime_checkable
class EmbeddingModel(Protocol):
    """Turns text into a vector."""

    @property
    def identifier(self) -> str:
        """Which model this is, e.g. ``voyage-3``.

        Reported with every search. Vectors from two different models are not
        comparable at all, so when answers go strange after a provider change
        this is the first thing worth seeing in the logs.
        """
        ...

    @property
    def dimensions(self) -> int:
        """How long its vectors are."""
        ...

    async def embed_query(self, text: str) -> Sequence[float]:
        """Embed one search query.

        Separate from :meth:`embed_documents` because it is a different
        operation, not a convenience: several providers take an input type and
        embed a question differently from the passage that answers it, and
        asking the wrong way measurably costs recall.
        """
        ...

    async def embed_documents(
        self, texts: Sequence[str]
    ) -> Sequence[Sequence[float]]:
        """Embed passages, in order, one vector each.

        A list rather than one at a time because ingesting a document is
        hundreds of passages, and hundreds of round trips is the difference
        between a few seconds and a few minutes. An implementation batches as
        its provider allows and still returns one vector per text, in the
        order given — callers pair them with chunks by position.
        """
        ...


@runtime_checkable
class PassageIndex(Protocol):
    """Finds the passages nearest a vector."""

    async def search(
        self,
        *,
        scope: KnowledgeScope,
        query_vector: Sequence[float],
        limit: int,
        min_score: float,
        model: str = "",
    ) -> Sequence[PassageHit]:
        """
        The nearest passages this scope may read, best first.

        An implementation **must fail closed**: given a scope it cannot narrow
        — no workspace, or an agent it knows nothing about — it returns nothing
        rather than everything. Deciding which sources an agent may read is a
        storage query, which is why it sits behind this port; that it is also a
        tenancy rule is why the result is checked again on the way out.

        ``model`` is which embedder produced ``query_vector``. Passages
        embedded by a different one **must not** be searched: the two vector
        spaces have nothing to do with each other, so the results are not worse
        but meaningless, ranked confidently. An implementation that stores the
        model alongside each passage filters on it and returns nothing until
        the corpus is re-indexed — which is detectable, where nonsense is not.
        An empty string means "do not filter", for a store with one embedder.
        """
        ...
