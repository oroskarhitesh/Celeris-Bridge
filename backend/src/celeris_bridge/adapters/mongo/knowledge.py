"""
The visitor records and the passage index, over Mongo.

The passage index here uses Atlas ``$vectorSearch`` when the deployment has a
search index, and falls back to scoring in Python when it does not — a single
Mongo on a laptop has no vector index, and a development environment that
cannot search its own knowledge is a development environment nobody uses.

Which path ran is reported, never guessed: ``last_search_used_index`` exists so
a deployment that silently fell back to the slow path can be noticed before it
is doing it over a million passages.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Iterable, Sequence

from pymongo import ASCENDING
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure

from celeris_bridge.adapters.mongo.stores import PASSAGES, VISITORS
from celeris_bridge.ports.knowledge import KnowledgeScope, PassageHit
from celeris_bridge.ports.visitor import ConversationRef, VisitorRecord

__all__ = ["MongoPassageIndex", "MongoVisitorRecords", "VECTOR_INDEX"]

#: The Atlas search index this expects. Created outside the application, which
#: is why its absence is handled rather than treated as a bug.
VECTOR_INDEX = "passage_vectors"

logger = logging.getLogger(__name__)


class MongoVisitorRecords:
    """One record per conversation, upserted."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._records = database[VISITORS]

    async def load(self, ref: ConversationRef) -> VisitorRecord | None:
        document = await self._records.find_one(
            {"workspaceId": ref.workspace_id, "conversationId": ref.conversation_id}
        )
        if document is None:
            return None
        return VisitorRecord(
            details={str(k): str(v) for k, v in (document.get("details") or {}).items()},
            visitor_type=str(document.get("visitorType") or "unknown"),
            status=str(document.get("status") or "exploring"),
            missing=tuple(str(key) for key in document.get("missing") or ()),
            reasons=tuple(str(reason) for reason in document.get("reasons") or ()),
        )

    async def save(self, ref: ConversationRef, record: VisitorRecord) -> None:
        await self._records.update_one(
            {"workspaceId": ref.workspace_id, "conversationId": ref.conversation_id},
            {
                "$set": {
                    "details": dict(record.details),
                    "visitorType": record.visitor_type,
                    "status": record.status,
                    "missing": list(record.missing),
                    "reasons": list(record.reasons),
                }
            },
            # Upsert because the first thing a visitor shares arrives without
            # anything having created a record for them first.
            upsert=True,
        )


class MongoPassageIndex:
    """Passages, searched by vector."""

    def __init__(
        self,
        database: AsyncDatabase,
        *,
        index_name: str = VECTOR_INDEX,
        use_vector_search: bool | None = None,
    ) -> None:
        self._passages = database[PASSAGES]
        self._index_name = index_name
        #: ``None`` means "try the index, remember what happened".
        self._vector_search = use_vector_search
        self.last_search_used_index: bool | None = None

    async def add(
        self,
        *,
        workspace_id: str,
        source_id: str,
        source_name: str,
        passages: Iterable[tuple[str, Sequence[float]]],
        source_url: str | None = None,
        approved: bool = False,
        agent_instance_ids: Iterable[str] = (),
        model: str = "",
    ) -> int:
        """Replace a source's passages with these. Returns how many were written.

        Replace rather than append: re-reading a document that has changed must
        not leave yesterday's paragraphs in the index, answering questions with
        text the customer has already deleted.
        """
        agents = [str(agent) for agent in agent_instance_ids]
        documents = [
            {
                "workspaceId": workspace_id,
                "sourceId": source_id,
                "sourceName": source_name,
                "sourceUrl": source_url,
                "approved": approved,
                "agentInstanceIds": agents,
                "index": position,
                "text": text,
                "vector": list(vector),
                # Which embedder made it. Searched on, because a vector from
                # another one is not comparable at all.
                "embeddingModel": model,
            }
            for position, (text, vector) in enumerate(passages)
        ]
        await self._passages.delete_many(
            {"workspaceId": workspace_id, "sourceId": source_id}
        )
        if documents:
            await self._passages.insert_many(documents)
        return len(documents)

    async def remove_source(self, *, workspace_id: str, source_id: str) -> int:
        result = await self._passages.delete_many(
            {"workspaceId": workspace_id, "sourceId": source_id}
        )
        return int(result.deleted_count)

    def _filter(self, scope: KnowledgeScope, model: str = "") -> dict[str, Any]:
        """What this scope may read.

        A passage bound to particular agents is readable by those agents; one
        bound to none is shared across the workspace. Written as a filter so
        the database applies it, not a loop that can be skipped.
        """
        query: dict[str, Any] = {
            "workspaceId": scope.workspace_id,
            "$or": [
                {"agentInstanceIds": {"$size": 0}},
                {"agentInstanceIds": scope.agent_instance_id},
            ],
        }
        if model:
            query["embeddingModel"] = model
        return query

    async def _explain_empty(self, scope: KnowledgeScope, model: str) -> None:
        """Say why a search found nothing, when the reason is a model change.

        Only reached on an empty result, so it costs nothing on the normal
        path -- and it turns the most confusing symptom there is ("the agent
        has forgotten everything") into one line naming the cause.
        """
        if not model:
            return
        stale = await self._passages.count_documents(
            {"workspaceId": scope.workspace_id, "embeddingModel": {"$ne": model}},
            limit=1,
        )
        if stale:
            logger.warning(
                "Workspace %s has passages embedded by another model; nothing "
                "will be found until they are re-indexed with %r.",
                scope.workspace_id,
                model,
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
        # Fail closed, as the port requires. Without this the filter below
        # would match every workspace at once.
        if not scope.workspace_id:
            return ()

        if self._vector_search is not False:
            try:
                hits = await self._by_index(scope, query_vector, limit, min_score, model)
                self.last_search_used_index = True
                self._vector_search = True
                if not hits:
                    await self._explain_empty(scope, model)
                return hits
            except OperationFailure as failure:
                if self._vector_search is True:
                    # Configured to use the index and it broke — that is not
                    # something to quietly work around.
                    raise
                logger.warning(
                    "No usable vector index (%s); scoring in Python instead. "
                    "Create the Atlas index %r before this holds real data.",
                    failure.details.get("codeName", failure.code)
                    if failure.details
                    else failure.code,
                    self._index_name,
                )
                self._vector_search = False

        hits = await self._by_scan(scope, query_vector, limit, min_score, model)
        self.last_search_used_index = False
        if not hits:
            await self._explain_empty(scope, model)
        return hits

    async def _by_index(
        self,
        scope: KnowledgeScope,
        query_vector: Sequence[float],
        limit: int,
        min_score: float,
        model: str = "",
    ) -> Sequence[PassageHit]:
        pipeline: list[dict[str, Any]] = [
            {
                "$vectorSearch": {
                    "index": self._index_name,
                    "path": "vector",
                    "queryVector": list(query_vector),
                    # Atlas wants a wider candidate pool than the number
                    # returned; ten times is the documented rule of thumb.
                    "numCandidates": max(limit * 10, 50),
                    "limit": limit,
                    "filter": self._filter(scope, model),
                }
            },
            {"$set": {"score": {"$meta": "vectorSearchScore"}}},
            {"$match": {"score": {"$gte": min_score}}},
        ]
        cursor = await self._passages.aggregate(pipeline)
        return [_hit(document) async for document in cursor]

    async def _by_scan(
        self,
        scope: KnowledgeScope,
        query_vector: Sequence[float],
        limit: int,
        min_score: float,
        model: str = "",
    ) -> Sequence[PassageHit]:
        """Every eligible passage, scored here. Development only."""
        query = list(query_vector)
        scored: list[PassageHit] = []
        cursor = self._passages.find(self._filter(scope, model))
        async for document in cursor:
            score = _cosine(query, document.get("vector") or ())
            if score < min_score:
                continue
            scored.append(_hit({**document, "score": score}))
        scored.sort(key=lambda hit: -hit.score)
        return tuple(scored[:limit])

    async def ensure_text_index(self) -> None:
        await self._passages.create_index(
            [("workspaceId", ASCENDING), ("sourceId", ASCENDING)]
        )


def _cosine(one: Sequence[float], other: Sequence[float]) -> float:
    if not one or not other or len(one) != len(other):
        return 0.0
    dot = sum(a * b for a, b in zip(one, other))
    left = math.sqrt(sum(a * a for a in one))
    right = math.sqrt(sum(b * b for b in other))
    return dot / (left * right) if left and right else 0.0


def _hit(document: Any) -> PassageHit:
    return PassageHit(
        passage_id=str(document.get("_id") or ""),
        source_id=str(document.get("sourceId") or ""),
        source_name=str(document.get("sourceName") or "Knowledge base"),
        text=str(document.get("text") or ""),
        score=round(float(document.get("score") or 0.0), 4),
        # Stamped from the document, so the domain's cross-tenant check has
        # something real to compare against rather than echoing the request.
        workspace_id=str(document.get("workspaceId") or ""),
        source_url=document.get("sourceUrl") or None,
        approved=bool(document.get("approved")),
    )
