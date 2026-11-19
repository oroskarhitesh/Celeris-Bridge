"""
A workspace's knowledge, as an admin screen sees it.

A thin layer, and that is the point: ingestion, crawling and retrieval are
already written and tested without a workspace in sight. What this adds is the
scope -- every method takes a ``workspace_id`` and passes it down, so no
caller is in a position to forget one -- and the one check the pure code cannot
make, which is whether a source belongs to the workspace asking about it.

Fetching robots.txt lives here too, because it is the one piece of I/O the
crawl loop deliberately does not do: it takes the text and the text has to
come through the same fetcher, with the same safety rules, as every other
request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from celeris_bridge.domain.knowledge.crawl import (
    CrawlLimits,
    CrawlReport,
    crawl_site,
    robots_url_for,
)
from celeris_bridge.domain.knowledge.ingestion import (
    IngestionResult,
    forget,
    ingest,
    reindex_workspace,
)
from celeris_bridge.domain.knowledge.urls import UnsafeUrl, validate_url
from celeris_bridge.ports.documents import DocumentStore, PassageWriter, StoredDocument
from celeris_bridge.ports.knowledge import EmbeddingModel
from celeris_bridge.ports.web import FetchFailed, WebFetcher

__all__ = ["KnowledgeService", "SourceNotFound", "SourceSummary"]

logger = logging.getLogger(__name__)


class SourceNotFound(LookupError):
    """No such source in this workspace.

    The same answer for a source that does not exist and one belonging to
    somebody else, for the same reason it is the same answer for an agent.
    """


@dataclass(frozen=True)
class SourceSummary:
    """One source, for a list screen."""

    source_id: str
    name: str
    kind: str
    bytes_stored: int
    passages: int
    #: Empty when nothing has been indexed from it. A document that failed to
    #: parse looks exactly like one that has not been processed yet, which is
    #: why ``status`` says which.
    embedding_model: str
    source_url: str | None = None
    approved: bool = False

    @property
    def status(self) -> str:
        if self.passages:
            return "indexed"
        if self.embedding_model:
            # Processed, and there was nothing in it: a blank page, or a scan
            # with no text layer.
            return "empty"
        return "pending"


class KnowledgeService:
    """Uploads, crawls and the list of what a workspace has given its agent."""

    def __init__(
        self,
        *,
        documents: DocumentStore,
        passages: PassageWriter,
        embeddings: EmbeddingModel,
        fetcher: WebFetcher | None = None,
        limits: CrawlLimits | None = None,
    ) -> None:
        self._documents = documents
        self._passages = passages
        self._embeddings = embeddings
        self._fetcher = fetcher
        self._limits = limits or CrawlLimits()

    @property
    def limits(self) -> CrawlLimits:
        """The caps this build crawls under. Read by the API, which may lower
        them for one crawl and must not be able to raise them."""
        return self._limits

    async def upload(
        self,
        *,
        workspace_id: str,
        source_id: str,
        name: str,
        content: bytes,
        content_type: str = "",
        approved: bool = False,
        agent_instance_ids: Sequence[str] = (),
    ) -> IngestionResult:
        """Store a file and index it, replacing any earlier version."""
        return await ingest(
            workspace_id=workspace_id,
            source_id=source_id,
            name=name,
            content=content,
            content_type=content_type,
            approved=approved,
            agent_instance_ids=agent_instance_ids,
            documents=self._documents,
            embeddings=self._embeddings,
            index=self._passages,
        )

    async def sources(self, workspace_id: str) -> Sequence[SourceSummary]:
        stored = await self._documents.list(workspace_id=workspace_id)
        return tuple(_summary(one) for one in stored)

    async def source(self, workspace_id: str, source_id: str) -> SourceSummary:
        found = await self._documents.describe(
            workspace_id=workspace_id, source_id=source_id
        )
        if found is None:
            raise SourceNotFound(f"No source {source_id!r} in this workspace")
        return _summary(found)

    async def original(self, workspace_id: str, source_id: str) -> bytes:
        """The bytes as they were uploaded.

        What makes a file that would not parse diagnosable: the admin can
        download exactly what the extractor was given.
        """
        await self.source(workspace_id, source_id)
        return await self._documents.content(
            workspace_id=workspace_id, source_id=source_id
        )

    async def delete(self, workspace_id: str, source_id: str) -> int:
        """Remove a source and everything retrieved from it."""
        await self.source(workspace_id, source_id)
        return await forget(
            workspace_id=workspace_id,
            source_id=source_id,
            documents=self._documents,
            index=self._passages,
        )

    async def reindex(
        self, workspace_id: str, *, limit: int = 0
    ) -> tuple[IngestionResult, ...]:
        """Re-embed whatever is not on the current embedder.

        The recovery path for a change of embedding provider, and the reason
        the originals are kept at all.
        """
        return await reindex_workspace(
            workspace_id=workspace_id,
            documents=self._documents,
            embeddings=self._embeddings,
            index=self._passages,
            limit=limit,
        )

    async def crawl(
        self,
        *,
        workspace_id: str,
        start_url: str,
        limits: CrawlLimits | None = None,
        agent_instance_ids: Sequence[str] = (),
    ) -> CrawlReport:
        """
        Crawl a site into this workspace's knowledge.

        The URL is checked before anything else happens, so a workspace
        pointing this at an internal address is refused here rather than
        deep inside the loop.
        """
        if self._fetcher is None:
            raise RuntimeError("This build has no web fetcher configured")
        validate_url(start_url)

        return await crawl_site(
            workspace_id=workspace_id,
            start_url=start_url,
            fetch=self._fetcher,
            documents=self._documents,
            embeddings=self._embeddings,
            index=self._passages,
            limits=limits or self._limits,
            robots_text=await self.robots_for(start_url),
            agent_instance_ids=tuple(agent_instance_ids),
        )

    async def robots_for(self, url: str) -> str | None:
        """A site's robots.txt, through the guarded fetcher.

        ``None`` when it is absent or unreadable, which allows everything --
        see :mod:`celeris_bridge.domain.knowledge.robots` on why that is the
        right reading for a site the customer owns. A failure here must never
        stop the crawl: the most common cause is a CDN returning 403 for a
        file the customer does not know exists.
        """
        if self._fetcher is None:
            return None
        try:
            page = await self._fetcher.fetch(robots_url_for(url))
        except (FetchFailed, UnsafeUrl) as failure:
            logger.info("No robots.txt for %s (%s); allowing everything", url, failure)
            return None
        if not page.ok or not page.content:
            return None
        return page.content.decode("utf-8", errors="replace")


def _summary(document: StoredDocument) -> SourceSummary:
    return SourceSummary(
        source_id=document.source_id,
        name=document.name,
        kind=document.content_type,
        bytes_stored=document.bytes_stored,
        passages=document.indexed_passages,
        embedding_model=document.indexed_model,
        source_url=document.source_url,
        approved=document.approved,
    )
