"""
Getting a document into the index.

Five steps, in this order, and the order is the design:

1. **store the original** -- before anything can go wrong with it,
2. **extract** text from the bytes,
3. **chunk** it into passages,
4. **embed** every passage, in one batch,
5. **write** the passages, then record what was done.

Store first because a file that cannot be parsed must still be downloadable:
the admin needs to see what they uploaded, and a bug in step 2 must not be the
reason a customer's handbook no longer exists. Embed before writing because
:meth:`PassageWriter.add` replaces a source's passages -- so a provider outage
half way through leaves yesterday's passages serving traffic, where writing as
each batch returned would leave a document indexed up to paragraph 40.

Nothing here does I/O itself. It takes the three ports it needs as arguments,
which is why the whole pipeline is tested with fakes and no network.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Sequence

from celeris_bridge.domain.knowledge.chunking import (
    DEFAULT_CHUNK_CHARACTERS,
    DEFAULT_OVERLAP_SENTENCES,
    chunk_text,
)
from celeris_bridge.domain.knowledge.extraction import (
    UnreadableDocument,
    UnsupportedDocument,
    extract,
)
from celeris_bridge.ports.documents import DocumentStore, PassageWriter, StoredDocument
from celeris_bridge.ports.knowledge import EmbeddingModel

__all__ = [
    "MAX_DOCUMENT_CHARACTERS",
    "DocumentTooLarge",
    "IngestionResult",
    "checksum",
    "forget",
    "ingest",
    "reindex",
    "reindex_workspace",
]

#: About half a million tokens of text, or a 600-page book. Past this a single
#: document is thousands of embedding calls and a bill nobody approved, and it
#: is almost always a mistake -- a log file, a database dump, a site crawled
#: into one page. Refused rather than truncated: answering from the first
#: two-thirds of a contract without saying so is worse than refusing it.
MAX_DOCUMENT_CHARACTERS = 2_000_000

logger = logging.getLogger(__name__)


class DocumentTooLarge(ValueError):
    """A document past :data:`MAX_DOCUMENT_CHARACTERS`."""

    def __init__(self, characters: int, limit: int) -> None:
        self.characters = characters
        self.limit = limit
        super().__init__(
            f"This document is {characters:,} characters and the limit is "
            f"{limit:,}. Split it, or upload the part the agent needs."
        )


@dataclass(frozen=True)
class IngestionResult:
    """What happened to one document."""

    workspace_id: str
    source_id: str
    source_name: str
    passages: int = 0
    characters: int = 0
    #: The embedder that produced the vectors. Stored with the passages, so a
    #: later change of provider can find the ones that need doing again.
    model: str = ""
    kind: str = ""
    #: Set when nothing was indexed and that is not an error -- a blank page,
    #: an image-only PDF. Prose, because it is shown to whoever uploaded it.
    skipped: str = ""

    @property
    def indexed(self) -> bool:
        return self.passages > 0


def checksum(content: bytes) -> str:
    """Of the stored bytes.

    Lets the crawler skip a page it has already indexed unchanged, which is
    most pages on most nights. SHA-256 rather than something faster because
    this is not a hot path and a collision here would silently serve the wrong
    document.
    """
    return hashlib.sha256(content).hexdigest()


async def ingest(
    *,
    workspace_id: str,
    source_id: str,
    name: str,
    content: bytes,
    documents: DocumentStore,
    embeddings: EmbeddingModel,
    index: PassageWriter,
    content_type: str = "",
    source_url: str | None = None,
    approved: bool = False,
    agent_instance_ids: Sequence[str] = (),
    chunk_characters: int = DEFAULT_CHUNK_CHARACTERS,
    overlap_sentences: int = DEFAULT_OVERLAP_SENTENCES,
    max_characters: int = MAX_DOCUMENT_CHARACTERS,
    use_document_title: bool = False,
    etag: str = "",
) -> IngestionResult:
    """
    Store a document and index it, replacing any earlier version.

    Raises :class:`~celeris_bridge.domain.knowledge.extraction.UnsupportedDocument`,
    :class:`~celeris_bridge.domain.knowledge.extraction.UnreadableDocument` or
    :class:`DocumentTooLarge` -- all three are things to tell the person who
    uploaded it, so they are exceptions and not a quiet field on the result.
    The bytes are already saved when they are raised.

    ``use_document_title`` lets an HTML page or a PDF supply its own citation
    name. Off by default: a file the customer named ``2024 Pricing.pdf`` should
    be cited as that, not as whatever is in its metadata, which is often the
    name of the template it was made from.
    """
    if not workspace_id or not source_id:
        # Fail closed. A blank workspace here would write passages nothing can
        # scope, and the index's own guard would then refuse to search them.
        raise ValueError("A document needs both a workspace and a source id")

    stored = await documents.put(
        workspace_id=workspace_id,
        source_id=source_id,
        name=name,
        content=content,
        content_type=content_type,
        source_url=source_url,
        approved=approved,
        agent_instance_ids=agent_instance_ids,
        etag=etag,
    )

    return await _index_stored(
        stored=stored,
        content=content,
        documents=documents,
        embeddings=embeddings,
        index=index,
        chunk_characters=chunk_characters,
        overlap_sentences=overlap_sentences,
        max_characters=max_characters,
        use_document_title=use_document_title,
    )


async def reindex(
    *,
    workspace_id: str,
    source_id: str,
    documents: DocumentStore,
    embeddings: EmbeddingModel,
    index: PassageWriter,
    chunk_characters: int = DEFAULT_CHUNK_CHARACTERS,
    overlap_sentences: int = DEFAULT_OVERLAP_SENTENCES,
    max_characters: int = MAX_DOCUMENT_CHARACTERS,
) -> IngestionResult:
    """Index a stored document again, without it being uploaded again.

    This is what makes a change of embedding model survivable, and it is the
    reason the original is kept at all: the index refuses to search vectors
    from another model, so after a provider change a workspace finds nothing
    until this has run over it.

    Raises :class:`~celeris_bridge.ports.documents.MissingDocument` if there is
    nothing stored -- which is also the honest answer for a workspace whose
    passages were loaded before documents were kept.
    """
    stored = await documents.describe(workspace_id=workspace_id, source_id=source_id)
    content = await documents.content(workspace_id=workspace_id, source_id=source_id)
    if stored is None:  # pragma: no cover - a store with bytes and no metadata
        raise ValueError(f"Document {source_id!r} has content but no record")

    return await _index_stored(
        stored=stored,
        content=content,
        documents=documents,
        embeddings=embeddings,
        index=index,
        chunk_characters=chunk_characters,
        overlap_sentences=overlap_sentences,
        max_characters=max_characters,
        # Whatever name was decided the first time is the one already shown in
        # past citations; re-reading the file must not rename it.
        use_document_title=False,
    )


async def reindex_workspace(
    *,
    workspace_id: str,
    documents: DocumentStore,
    embeddings: EmbeddingModel,
    index: PassageWriter,
    limit: int = 0,
) -> tuple[IngestionResult, ...]:
    """Re-index every document not already on the current embedder.

    Only the stale ones, so running it twice costs nothing the second time and
    an interrupted run resumes where it stopped. One document failing does not
    stop the rest: a single locked PDF must not leave the other forty
    unsearchable, and the failures are in the results for the caller to show.

    ``limit`` caps how many are done in one pass, for a job that would rather
    make progress every minute than hold a connection for an hour.
    """
    model = embeddings.identifier
    pending = list(await documents.stale(workspace_id=workspace_id, model=model))
    if limit > 0:
        pending = pending[:limit]

    results: list[IngestionResult] = []
    for document in pending:
        try:
            results.append(
                await reindex(
                    workspace_id=workspace_id,
                    source_id=document.source_id,
                    documents=documents,
                    embeddings=embeddings,
                    index=index,
                )
            )
        except (UnsupportedDocument, UnreadableDocument, DocumentTooLarge) as refused:
            logger.warning(
                "Could not re-index %s/%s (%s): %s",
                workspace_id,
                document.source_id,
                document.name,
                refused,
            )
            results.append(
                IngestionResult(
                    workspace_id=workspace_id,
                    source_id=document.source_id,
                    source_name=document.name,
                    model=model,
                    skipped=str(refused),
                )
            )
    return tuple(results)


async def forget(
    *,
    workspace_id: str,
    source_id: str,
    documents: DocumentStore,
    index: PassageWriter,
) -> int:
    """Delete a document and everything retrieved from it. Returns passages removed.

    The passages go first. The other order would, if interrupted, leave
    passages with no document behind them -- still retrievable, still cited,
    and no longer deletable through any screen.
    """
    removed = await index.remove_source(workspace_id=workspace_id, source_id=source_id)
    await documents.delete(workspace_id=workspace_id, source_id=source_id)
    return removed


async def _index_stored(
    *,
    stored: StoredDocument,
    content: bytes,
    documents: DocumentStore,
    embeddings: EmbeddingModel,
    index: PassageWriter,
    chunk_characters: int,
    overlap_sentences: int,
    max_characters: int,
    use_document_title: bool,
) -> IngestionResult:
    """Steps 2 to 5. Shared by :func:`ingest` and :func:`reindex`."""
    model = embeddings.identifier

    def outcome(**changed: object) -> IngestionResult:
        fields: dict[str, object] = {
            "workspace_id": stored.workspace_id,
            "source_id": stored.source_id,
            "source_name": stored.name,
            "model": model,
        }
        fields.update(changed)
        return IngestionResult(**fields)  # type: ignore[arg-type]

    document = extract(content, name=stored.name, content_type=stored.content_type)
    if len(document.text) > max_characters:
        raise DocumentTooLarge(len(document.text), max_characters)

    chunks = chunk_text(
        document.text,
        characters=chunk_characters,
        overlap_sentences=overlap_sentences,
    )
    if not chunks:
        # A blank page, or a scanned PDF with no text layer. Not an error, but
        # the old passages still go: re-uploading an emptied document must not
        # leave it answering questions.
        await index.remove_source(
            workspace_id=stored.workspace_id, source_id=stored.source_id
        )
        await documents.mark_indexed(
            workspace_id=stored.workspace_id,
            source_id=stored.source_id,
            model=model,
            passages=0,
        )
        return outcome(
            kind=document.kind,
            skipped=(
                "No text could be read from this document. If it is a scan, "
                "it needs to be run through OCR first."
            ),
        )

    name = document.title if (use_document_title and document.title) else stored.name

    # One call for the whole document. The provider batches inside it; what
    # matters here is that it returns one vector per chunk in order, which it
    # checks, because the pairing below is positional.
    vectors = await embeddings.embed_documents([chunk.text for chunk in chunks])

    written = await index.add(
        workspace_id=stored.workspace_id,
        source_id=stored.source_id,
        source_name=name,
        passages=list(zip([chunk.text for chunk in chunks], vectors)),
        source_url=stored.source_url,
        approved=stored.approved,
        agent_instance_ids=stored.agent_instance_ids,
        model=model,
    )
    await documents.mark_indexed(
        workspace_id=stored.workspace_id,
        source_id=stored.source_id,
        model=model,
        passages=written,
    )
    logger.info(
        "Indexed %s/%s (%s, %s) as %d passages with %s",
        stored.workspace_id,
        stored.source_id,
        name,
        document.kind,
        written,
        model,
    )
    return outcome(
        source_name=name,
        passages=written,
        characters=len(document.text),
        kind=document.kind,
    )
