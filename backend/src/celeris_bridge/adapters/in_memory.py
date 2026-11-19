"""
In-memory adapters: the knowledge index, the embedder, the visitor records and
a calendar.

Not scaffolding to be thrown away. These stay, for three jobs:

* **Local development**, so the project runs with an API key and nothing else —
  no Mongo, no Atlas index, no calendar account.
* **Tests of the real wiring**, as opposed to tests of a hand-made fake.
* **A reference for the real adapters.** Each one here is the smallest thing
  that satisfies its port honestly, which makes it the clearest statement of
  what the port actually requires.

The embedder is the exception and says so: it is a deterministic hash, not a
language model, and it must not be used for anything a person will see.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from dataclasses import replace

from celeris_bridge.domain.knowledge.chunking import chunk_text
from celeris_bridge.domain.knowledge.ingestion import checksum
from celeris_bridge.ports.documents import MissingDocument, StoredDocument
from celeris_bridge.ports.calendar import (
    Availability,
    Booking,
    BookingRequest,
    CalendarScope,
    Host,
    Slot,
    SlotTaken,
)
from celeris_bridge.ports.knowledge import KnowledgeScope, PassageHit
from celeris_bridge.ports.visitor import ConversationRef, VisitorRecord

__all__ = [
    "HashingEmbeddings",
    "InMemoryCalendar",
    "InMemoryDocumentStore",
    "InMemoryPassageIndex",
    "InMemoryVisitorRecords",
    "SystemClock",
]

_WORD_RE = re.compile(r"[a-z0-9]+")


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class HashingEmbeddings:
    """
    A deterministic bag-of-words embedder. **Not a language model.**

    Every word is hashed to a dimension and the vector is normalised, so cosine
    similarity reduces to weighted word overlap. That is enough to exercise the
    whole retrieval path — thresholds, ordering, the approved-answer rule — with
    no network and no key, and it is repeatable, which a real embedder is not.

    What it cannot do is match "how much does it cost" to a passage about
    pricing that never says "cost". Synonyms, paraphrase and meaning are the
    entire point of a real embedder. Never put this in front of a customer.
    """

    def __init__(self, dimensions: int = 256) -> None:
        self._dimensions = dimensions

    @property
    def identifier(self) -> str:
        # Carries the size, like the real adapters: a corpus embedded at 256
        # dimensions cannot be searched with a 512-dimension vector.
        return f"hashing-dev:{self._dimensions}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_query(self, text: str) -> Sequence[float]:
        return self.embed(text)

    async def embed_documents(
        self, texts: Sequence[str]
    ) -> Sequence[Sequence[float]]:
        # No batching to do and nothing to wait for, but the same signature --
        # so ingestion written against this runs unchanged against a real
        # provider.
        return [self.embed(text) for text in texts]

    def embed(self, text: str) -> list[float]:
        """Synchronous, so indexing a document does not need an event loop."""
        vector = [0.0] * self._dimensions
        for word in _WORD_RE.findall(text.lower()):
            if len(word) < 3:
                continue
            digest = hashlib.blake2b(word.encode("utf-8"), digest_size=4).digest()
            vector[int.from_bytes(digest, "big") % self._dimensions] += 1.0
        length = math.sqrt(sum(value * value for value in vector))
        return [value / length for value in vector] if length else vector


def _cosine(one: Sequence[float], other: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(one, other))


class InMemoryPassageIndex:
    """Passages in a list, searched by cosine similarity.

    Fine for a few thousand passages, which is more than a development
    workspace has. The Atlas adapter replaces it with the same port.
    """

    def __init__(self, embeddings: HashingEmbeddings | None = None) -> None:
        self._embeddings = embeddings or HashingEmbeddings()
        self._rows: list[tuple[PassageHit, Sequence[float]]] = []
        # Which agents may read each passage. Empty means every agent in the
        # workspace, which is how a shared document behaves.
        self._agents: dict[str, tuple[str, ...]] = {}
        # Which embedder produced each vector, so a search with another one
        # leaves them out instead of comparing unrelated vector spaces.
        self._models: dict[str, str] = {}

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
        """Replace a source's passages with these. Returns how many were written.

        Takes vectors rather than making them: the ingestion pipeline embeds,
        in one batch, through whichever provider is configured. An index that
        embedded for itself could only ever use the embedder it was built
        with, which is how a store ends up with two vector spaces in it.
        """
        await self.remove_source(workspace_id=workspace_id, source_id=source_id)
        return self._write(
            workspace_id=workspace_id,
            source_id=source_id,
            source_name=source_name,
            passages=list(passages),
            source_url=source_url,
            approved=approved,
            agent_instance_ids=agent_instance_ids,
            model=model or self._embeddings.identifier,
        )

    def _write(
        self,
        *,
        workspace_id: str,
        source_id: str,
        source_name: str,
        passages: Sequence[tuple[str, Sequence[float]]],
        source_url: str | None,
        approved: bool,
        agent_instance_ids: Iterable[str],
        model: str,
    ) -> int:
        """The one place rows are appended. Shared with :meth:`add_document`."""
        agents = tuple(str(one) for one in agent_instance_ids)
        for position, (text, vector) in enumerate(passages):
            hit = PassageHit(
                passage_id=f"{source_id}:{position}",
                source_id=source_id,
                source_name=source_name,
                text=text,
                score=0.0,
                workspace_id=workspace_id,
                source_url=source_url,
                approved=approved,
            )
            self._rows.append((hit, list(vector)))
            self._agents[hit.passage_id] = agents
            self._models[hit.passage_id] = model
        return len(passages)

    async def remove_source(self, *, workspace_id: str, source_id: str) -> int:
        going = [
            hit.passage_id
            for hit, _ in self._rows
            if hit.workspace_id == workspace_id and hit.source_id == source_id
        ]
        self._rows = [
            row for row in self._rows if row[0].passage_id not in set(going)
        ]
        for passage_id in going:
            self._agents.pop(passage_id, None)
            self._models.pop(passage_id, None)
        return len(going)

    def add_document(
        self,
        *,
        workspace_id: str,
        source_id: str,
        source_name: str,
        text: str,
        source_url: str | None = None,
        approved: bool = False,
        agent_instance_ids: Iterable[str] = (),
    ) -> int:
        """Chunk text and index it, synchronously. Returns how many passages.

        A shortcut for a development script or a test that wants a known
        passage in the index in one line. It skips extraction and embeds with
        the hashing embedder, so it is not the ingestion path -- that is
        :func:`celeris_bridge.domain.knowledge.ingestion.ingest`, which is
        what a real upload goes through. The rows themselves are written by
        the same method, so the two cannot drift apart.
        """
        chunks = chunk_text(text)
        return self._write(
            workspace_id=workspace_id,
            source_id=source_id,
            source_name=source_name,
            passages=[(chunk.text, self._embeddings.embed(chunk.text)) for chunk in chunks],
            source_url=source_url,
            approved=approved,
            agent_instance_ids=agent_instance_ids,
            model=self._embeddings.identifier,
        )

    async def search(
        self,
        *,
        scope: KnowledgeScope,
        query_vector: Sequence[float],
        limit: int,
        min_score: float,
        model: str = "",
    ) -> Sequence[PassageHit]:
        # Fail closed, as the port requires: no workspace, no results. The
        # real adapter has a database filter to get wrong; this one has this
        # line, and it is here for the same reason.
        if not scope.workspace_id:
            return ()

        scored: list[PassageHit] = []
        for hit, vector in self._rows:
            if hit.workspace_id != scope.workspace_id:
                continue
            if model and self._models.get(hit.passage_id, model) != model:
                continue
            allowed = self._agents.get(hit.passage_id, ())
            if allowed and scope.agent_instance_id not in allowed:
                continue
            score = _cosine(query_vector, vector)
            if score < min_score:
                continue
            scored.append(
                PassageHit(
                    passage_id=hit.passage_id,
                    source_id=hit.source_id,
                    source_name=hit.source_name,
                    text=hit.text,
                    score=round(score, 4),
                    workspace_id=hit.workspace_id,
                    source_url=hit.source_url,
                    approved=hit.approved,
                )
            )
        scored.sort(key=lambda hit: -hit.score)
        return tuple(scored[:limit])


class InMemoryVisitorRecords:
    """One record per conversation, in a dict."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], VisitorRecord] = {}

    async def load(self, ref: ConversationRef) -> VisitorRecord | None:
        return self._records.get((ref.workspace_id, ref.conversation_id))

    async def save(self, ref: ConversationRef, record: VisitorRecord) -> None:
        self._records[(ref.workspace_id, ref.conversation_id)] = record

    def all(self) -> dict[tuple[str, str], VisitorRecord]:
        """Every record, for a development screen or an assertion."""
        return dict(self._records)


class InMemoryCalendar:
    """
    Hourly slots on working days, with whatever is booked removed.

    Enough to walk the booking flow end to end, including the case that
    matters: a slot booked twice raises :class:`SlotTaken`, because the
    calendar is the authority on availability and not the agent.
    """

    def __init__(
        self,
        *,
        time_zone: str = "Europe/London",
        hosts: Sequence[Host] = (),
        opens_at: int = 9,
        closes_at: int = 17,
    ) -> None:
        self.time_zone = time_zone
        self.hosts = tuple(hosts) or (
            Host(host_id="h1", name="Priya Raman", team="Sales", role="Account executive"),
        )
        self.opens_at = opens_at
        self.closes_at = closes_at
        self.bookings: list[Booking] = []
        self._taken: set[tuple[str, datetime]] = set()

    async def availability(
        self,
        *,
        scope: CalendarScope,
        start: datetime,
        end: datetime,
        minutes: int,
    ) -> Availability:
        slots: list[Slot] = []
        moment = start.replace(minute=0, second=0, microsecond=0)
        if moment < start:
            moment += timedelta(hours=1)
        while moment < end:
            if moment.weekday() < 5 and self.opens_at <= moment.hour < self.closes_at:
                free = tuple(
                    host.host_id
                    for host in self.hosts
                    if (host.host_id, moment) not in self._taken
                )
                if free:
                    slots.append(
                        Slot(start=moment, end=moment + timedelta(minutes=minutes), host_ids=free)
                    )
            moment += timedelta(hours=1)
        return Availability(
            time_zone=self.time_zone,
            slots=tuple(slots),
            hosts={host.host_id: host for host in self.hosts},
        )

    async def book(
        self, *, scope: CalendarScope, request: BookingRequest
    ) -> Booking:
        key = (request.host_id, request.start)
        if key in self._taken:
            raise SlotTaken()
        self._taken.add(key)
        booking = Booking(
            booking_id=f"bk-{len(self.bookings) + 1}",
            host_id=request.host_id,
            start=request.start,
            end=request.end,
            time_zone=self.time_zone,
        )
        self.bookings.append(booking)
        return booking


class InMemoryDocumentStore:
    """Documents in a dict.

    For tests and for a run that should leave nothing behind. It keeps the
    bytes, which means a workspace's whole knowledge base sits in the process
    -- fine for the handful of files a test or a demo uses, and the reason
    :class:`~celeris_bridge.adapters.local_documents.LocalDocumentStore`
    exists for anything else.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], StoredDocument] = {}
        self._content: dict[tuple[str, str], bytes] = {}

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
        key = (workspace_id, source_id)
        document = StoredDocument(
            workspace_id=workspace_id,
            source_id=source_id,
            name=name,
            content_type=content_type,
            bytes_stored=len(content),
            source_url=source_url,
            checksum=checksum(content),
            etag=etag,
            approved=approved,
            agent_instance_ids=tuple(str(one) for one in agent_instance_ids),
        )
        self._content[key] = bytes(content)
        self._records[key] = document
        return document

    async def content(self, *, workspace_id: str, source_id: str) -> bytes:
        try:
            return self._content[(workspace_id, source_id)]
        except KeyError as missing:
            raise MissingDocument(
                f"No stored document {source_id!r} in workspace {workspace_id!r}"
            ) from missing

    async def describe(
        self, *, workspace_id: str, source_id: str
    ) -> StoredDocument | None:
        return self._records.get((workspace_id, source_id))

    async def list(self, *, workspace_id: str) -> Sequence[StoredDocument]:
        return tuple(
            document
            for (owner, _), document in sorted(self._records.items())
            if owner == workspace_id
        )

    async def stale(
        self, *, workspace_id: str, model: str
    ) -> Sequence[StoredDocument]:
        documents = await self.list(workspace_id=workspace_id)
        return tuple(
            document for document in documents if document.indexed_model != model
        )

    async def mark_indexed(
        self, *, workspace_id: str, source_id: str, model: str, passages: int
    ) -> None:
        key = (workspace_id, source_id)
        document = self._records.get(key)
        if document is None:
            raise MissingDocument(
                f"No stored document {source_id!r} in workspace {workspace_id!r}"
            )
        self._records[key] = replace(
            document, indexed_model=model, indexed_passages=passages
        )

    async def delete(self, *, workspace_id: str, source_id: str) -> bool:
        key = (workspace_id, source_id)
        self._content.pop(key, None)
        return self._records.pop(key, None) is not None
