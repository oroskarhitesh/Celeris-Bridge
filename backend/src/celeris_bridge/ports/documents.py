"""
Ports for the documents a workspace gives its agent.

Two ports here, both on the write side, because ingesting is the only thing in
this project that *writes* knowledge — everything else reads it through
:mod:`celeris_bridge.ports.knowledge`.

**Why the original is kept at all.** The passages in the index are derived
data: chunking and embedding are a pure function of the bytes plus the settings
in force when they ran. Change the embedder and every stored vector becomes
unsearchable — that is the guard in the index, and it is the right behaviour,
but it is only survivable if the text can be embedded again. Keeping the
original turns "change the embedding model" from an email asking every customer
to re-upload their files into a background job.

**The document store is also the source registry.** It records what was last
done with each document — which embedder indexed it and how many passages it
became — rather than a fourth record store existing to hold two fields. That is
what makes the admin screen ("14 sources, 402 passages") and the re-index job
("which of these is on an old model") answerable with one query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

__all__ = [
    "DocumentStore",
    "MissingDocument",
    "PassageWriter",
    "StoredDocument",
]


class MissingDocument(LookupError):
    """Asked for a document that is not there."""


@dataclass(frozen=True)
class StoredDocument:
    """One document a workspace uploaded or the crawler fetched."""

    workspace_id: str
    #: Stable for the life of the document. Passages carry it, so re-indexing
    #: replaces them instead of adding a second copy.
    source_id: str
    #: What a citation shows a visitor. A file name or a page title — display
    #: text, never a path: see :class:`LocalDocumentStore` on why that matters.
    name: str
    content_type: str = ""
    bytes_stored: int = 0
    source_url: str | None = None
    #: Of the stored bytes. The crawler re-fetches pages that mostly have not
    #: changed, and re-embedding an unchanged page costs money for nothing.
    checksum: str = ""
    #: The ``ETag`` the server sent, offered back on the next fetch. Saves the
    #: download where the checksum only saves the embedding.
    etag: str = ""

    #: Carried so a re-index does not quietly downgrade an approved answer to
    #: an ordinary passage, or widen one agent's document to the workspace.
    approved: bool = False
    agent_instance_ids: tuple[str, ...] = ()

    #: Which embedder indexed it, and into how many passages. Empty means it
    #: has not been indexed — a document that failed to parse looks exactly
    #: like this, which is why the admin screen can show it as such.
    indexed_model: str = ""
    indexed_passages: int = 0


@runtime_checkable
class DocumentStore(Protocol):
    """Where the original bytes live."""

    async def put(
        self,
        *,
        workspace_id: str,
        source_id: str,
        name: str,
        content: bytes,
        content_type: str = "",
        source_url: str | None = None,
        approved: bool = False,
        agent_instance_ids: Sequence[str] = (),
        etag: str = "",
    ) -> StoredDocument:
        """Write a document, replacing any earlier version of it.

        Resets the indexed fields: the bytes have changed, so whatever is in
        the index for this source is about to be wrong and must not be
        reported as current.
        """
        ...

    async def content(self, *, workspace_id: str, source_id: str) -> bytes:
        """The stored bytes. Raises :class:`MissingDocument` if there are none."""
        ...

    async def describe(
        self, *, workspace_id: str, source_id: str
    ) -> StoredDocument | None:
        """What is known about it, without reading it."""
        ...

    async def list(self, *, workspace_id: str) -> Sequence[StoredDocument]:
        """Every document in a workspace. Scoped, never global."""
        ...

    async def stale(
        self, *, workspace_id: str, model: str
    ) -> Sequence[StoredDocument]:
        """Documents not indexed by ``model``.

        The work list for a re-index. Separate from filtering :meth:`list` in
        the caller because this is the question a store can answer cheaply and
        a caller cannot.
        """
        ...

    async def mark_indexed(
        self, *, workspace_id: str, source_id: str, model: str, passages: int
    ) -> None:
        """Record that this document is now in the index under ``model``.

        Called after the passages are written, never before: a crash between
        the two leaves a document that looks un-indexed and gets done again,
        which is harmless, where the other order leaves one that looks done
        and is not.
        """
        ...

    async def delete(self, *, workspace_id: str, source_id: str) -> bool:
        """Remove it. ``False`` if it was not there."""
        ...


@runtime_checkable
class PassageWriter(Protocol):
    """The write half of the passage index."""

    async def add(
        self,
        *,
        workspace_id: str,
        source_id: str,
        source_name: str,
        passages: Sequence[tuple[str, Sequence[float]]],
        source_url: str | None = None,
        approved: bool = False,
        agent_instance_ids: Sequence[str] = (),
        model: str = "",
    ) -> int:
        """Replace this source's passages with these. Returns how many.

        Replace rather than append: a document that has been edited must not
        leave yesterday's paragraphs in the index, answering questions with
        text the customer has already deleted.

        ``passages`` pairs each chunk with its vector **by position**, and
        ``model`` names the embedder that produced them — stored with each
        passage, because a vector from another model is not comparable at all.
        """
        ...

    async def remove_source(self, *, workspace_id: str, source_id: str) -> int:
        """Drop every passage of a source. Returns how many went."""
        ...
