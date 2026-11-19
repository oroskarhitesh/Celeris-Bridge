"""
Turning a question into passages an agent may quote.

Everything here is policy, and all of it is pure: the two ports do the I/O and
are handed in. That means the interesting decisions — what counts as relevant,
what beats what, how much text a model is given, what happens when nothing is
found — are testable with two small fakes and no infrastructure at all.

Retrieved text is **untrusted**. It comes from a crawled page or an uploaded
document, which means anyone who can get a sentence onto a customer's website
can get that sentence into the prompt. :func:`neutralise` takes the teeth out
of the obvious attempts and the tool result says plainly that passages are
quotes rather than orders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from celeris_bridge.ports.knowledge import (
    EmbeddingModel,
    KnowledgeScope,
    PassageHit,
    PassageIndex,
)

__all__ = [
    "CrossTenantPassage",
    "MAX_QUERY_CHARACTERS",
    "Passage",
    "Retrieval",
    "looks_like_injection",
    "neutralise",
    "retrieve",
    "tidy_query",
]

#: Long enough for any real question, short enough that a wall of text cannot
#: be smuggled into an embedding call as a "query".
MAX_QUERY_CHARACTERS = 500

#: Patterns that only ever appear in an attempt to redirect the model. Matched
#: against retrieved text, where they have no business being, and against the
#: query, where they are worth recording.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(all\s+|any\s+)?(previous|prior|earlier|above)\s+(instructions?|prompts?|rules?)",
        r"disregard\s+(the\s+)?(system|previous|above)\s+(prompt|instructions?|rules?)",
        r"forget\s+(everything|all|what)\b",
        r"you\s+are\s+now\s+(a|an|in|no\s+longer)\b",
        r"(reveal|repeat|print|show)\s+(me\s+)?(your\s+)?(system\s+)?(prompt|instructions)",
        r"system\s*prompt\s*[:=]",
        r"act\s+as\s+(if\s+you\s+are\s+)?(a\s+)?(different|another)\b",
        r"\bdo\s+anything\s+now\b",
        r"new\s+instructions?\s*[:=]",
    )
)

_REDACTION = "[removed]"


class CrossTenantPassage(RuntimeError):
    """An index returned a passage belonging to another workspace.

    Raised, not filtered. One passage from the wrong tenant means the query that
    produced it is wrong, so the rest of the result cannot be trusted either —
    and quietly serving the remainder would hide the bug for as long as it takes
    someone to notice their competitor's pricing in a chat transcript.
    """

    def __init__(self, expected: str, found: str) -> None:
        super().__init__(
            f"The passage index returned a passage from workspace {found!r} "
            f"for a search scoped to {expected!r}"
        )


@dataclass(frozen=True)
class Passage:
    """One passage, ready to hand to a model."""

    source_name: str
    text: str
    score: float
    source_id: str
    source_url: str | None = None
    approved: bool = False


@dataclass(frozen=True)
class Retrieval:
    """What one search found, and what was notable about it."""

    query: str
    passages: tuple[Passage, ...] = ()
    #: The best score before any truncation, for telemetry. 0.0 when empty.
    top_score: float = 0.0
    #: The query itself tried to redirect the model. Recorded rather than
    #: refused: the visitor still gets an answer, and a pattern of these in one
    #: conversation is the signal worth acting on.
    injection_suspected: bool = False
    #: Which embedding model produced the query vector.
    embedding_model: str = ""

    @property
    def found(self) -> bool:
        return bool(self.passages)


def looks_like_injection(text: str) -> bool:
    """Whether ``text`` contains a known attempt to redirect the model."""
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def neutralise(text: str) -> str:
    """Strip instruction-shaped phrases out of untrusted text.

    Not a security boundary on its own — no pattern list is. It is the cheap
    half of the defence; the other half is that passages are presented to the
    model as quoted material, and that an ability can only ever be called with
    arguments :func:`validate_input` has already checked.
    """
    out = text
    for pattern in _INJECTION_PATTERNS:
        out = pattern.sub(_REDACTION, out)
    return out


def tidy_query(text: str) -> str:
    """Collapse a query's whitespace and cap its length."""
    return " ".join(text.split())[:MAX_QUERY_CHARACTERS]


def _ordered(hits: Sequence[PassageHit]) -> list[PassageHit]:
    """Approved answers first, then by score.

    An approved answer is one a person at the company wrote and signed off. It
    outranks a better-scoring sentence scraped from a page, because it is the
    sentence they want said.
    """
    return sorted(hits, key=lambda hit: (not hit.approved, -hit.score))


async def retrieve(
    *,
    query: str,
    scope: KnowledgeScope,
    embeddings: EmbeddingModel,
    index: PassageIndex,
    limit: int,
    min_score: float,
    passage_characters: int,
) -> Retrieval:
    """
    Search a workspace's knowledge.

    Asks the index for more than ``limit`` so that an approved answer sitting
    just outside the top few can still be promoted; trims to ``limit`` after
    ordering.
    """
    tidied = tidy_query(query)
    suspected = looks_like_injection(tidied)
    if not tidied:
        return Retrieval(query="", injection_suspected=suspected)

    vector = await embeddings.embed_query(tidied)
    hits = await index.search(
        scope=scope,
        query_vector=vector,
        limit=max(limit * 2, limit + 2),
        min_score=min_score,
        # Which embedder made this vector. An index holding passages from
        # another one has to leave them out rather than compare across two
        # unrelated vector spaces.
        model=embeddings.identifier,
    )

    for hit in hits:
        if hit.workspace_id != scope.workspace_id:
            raise CrossTenantPassage(scope.workspace_id, hit.workspace_id)

    kept = _ordered(hits)[:limit]
    return Retrieval(
        query=tidied,
        passages=tuple(
            Passage(
                source_name=hit.source_name,
                text=neutralise(hit.text)[:passage_characters],
                score=hit.score,
                source_id=hit.source_id,
                source_url=hit.source_url,
                approved=hit.approved,
            )
            for hit in kept
        ),
        top_score=max((hit.score for hit in hits), default=0.0),
        injection_suspected=suspected,
        embedding_model=embeddings.identifier,
    )
