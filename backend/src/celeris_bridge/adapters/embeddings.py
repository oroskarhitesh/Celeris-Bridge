"""
The ``EmbeddingModel`` port, over a real provider.

Anthropic has no embeddings API, so this is the first part of the project that
talks to someone else. Two providers are supported because the choice depends
on which key a deployment already has, and the second one cost sixty lines once
the first existed.

Three things here are not incidental.

**Queries and documents are embedded differently.** Voyage takes an
``input_type``, and using it is worth several points of recall: "how much does
it cost" and the passage that answers it are not the same kind of text. The
port has two methods for exactly this reason, and the OpenAI adapter simply
ignores the distinction because its models do not expose one.

**Batching.** Ingesting a document is hundreds of passages. One request each
turns seconds into minutes and invites a rate limit; both providers take a
list, with different ceilings.

**The identifier is the model and its size.** It is stored with every passage
and checked at search time — a vector written by one model and searched by
another is not merely worse, it is meaningless, and confidently ranked.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Iterator, Sequence

import httpx

__all__ = [
    "EmbeddingFailed",
    "OpenAIEmbeddings",
    "VoyageEmbeddings",
    "build_embeddings",
]

logger = logging.getLogger(__name__)

#: Retried once per attempt after a pause. A rate limit answers quickly, and
#: ingesting a large document will meet one.
_RETRY_ON = frozenset({408, 409, 429, 500, 502, 503, 504})
_ATTEMPTS = 4
_BACKOFF_SECONDS = 1.5


class EmbeddingFailed(RuntimeError):
    """The provider could not be used. The caller decides what that means.

    Raised rather than returning an empty vector: a zero vector is a perfectly
    valid thing to store and search with, and it silently matches nothing,
    which turns an outage into an agent that has quietly forgotten everything.
    """


def _batched(texts: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(texts), size):
        yield texts[start : start + size]


class _HttpEmbeddings:
    """What the two providers have in common: batching, retries, checks."""

    #: Overridden per provider.
    url = ""
    batch_size = 64
    #: How long one request may take. Embedding a batch is not a quick call.
    timeout = 30.0

    def __init__(
        self,
        *,
        model: str,
        dimensions: int,
        api_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise EmbeddingFailed(
                f"No API key for {type(self).__name__}; set it in the environment"
            )
        self._model = model
        self._dimensions = dimensions
        self._api_key = api_key
        self._client = client
        self._owned = client is None

    @property
    def identifier(self) -> str:
        """Model **and** size, because both change the vector space.

        ``voyage-3`` at 1024 and the same model asked for 256 dimensions are
        not interchangeable, and a corpus holding both is a corpus that returns
        nonsense.
        """
        return f"{self._model}:{self._dimensions}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def aclose(self) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _body(self, texts: Sequence[str], *, is_query: bool) -> dict[str, Any]:
        raise NotImplementedError

    def _vectors(self, payload: Any) -> list[list[float]]:
        raise NotImplementedError

    async def _post(self, body: dict[str, Any]) -> list[list[float]]:
        last: Exception | None = None
        for attempt in range(_ATTEMPTS):
            try:
                response = await self._http().post(
                    self.url, headers=self._headers(), json=body
                )
            except httpx.HTTPError as failure:
                last = failure
            else:
                if response.status_code < 300:
                    return self._vectors(response.json())
                if response.status_code not in _RETRY_ON:
                    # A bad key or a bad model is not going to fix itself, and
                    # retrying it three times just delays the error.
                    raise EmbeddingFailed(
                        f"{type(self).__name__} refused the request "
                        f"({response.status_code}): {response.text[:200]}"
                    )
                last = EmbeddingFailed(
                    f"{type(self).__name__} returned {response.status_code}"
                )

            if attempt < _ATTEMPTS - 1:
                pause = _BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "Embedding request failed (%s); retrying in %.1fs", last, pause
                )
                await asyncio.sleep(pause)

        raise EmbeddingFailed(
            f"{type(self).__name__} could not be reached after {_ATTEMPTS} attempts"
        ) from last

    def _checked(self, vectors: Sequence[Sequence[float]], expected: int) -> None:
        if len(vectors) != expected:
            # Pairing is positional: a short batch would shift every vector
            # after it onto the wrong passage, and nothing downstream could
            # tell. Loud, immediately.
            raise EmbeddingFailed(
                f"Asked for {expected} vectors and got {len(vectors)}"
            )
        for vector in vectors:
            if len(vector) != self._dimensions:
                raise EmbeddingFailed(
                    f"{self.identifier} returned {len(vector)} dimensions, "
                    f"expected {self._dimensions}"
                )

    async def embed_query(self, text: str) -> Sequence[float]:
        vectors = await self._post(self._body([text], is_query=True))
        self._checked(vectors, 1)
        return vectors[0]

    async def embed_documents(
        self, texts: Sequence[str]
    ) -> Sequence[Sequence[float]]:
        if not texts:
            return []
        out: list[Sequence[float]] = []
        for batch in _batched(list(texts), self.batch_size):
            vectors = await self._post(self._body(batch, is_query=False))
            self._checked(vectors, len(batch))
            out.extend(vectors)
        return out


class VoyageEmbeddings(_HttpEmbeddings):
    """Voyage AI. Takes an ``input_type``, which is why it is the default."""

    url = "https://api.voyageai.com/v1/embeddings"
    #: Voyage's documented ceiling for one request.
    batch_size = 128

    def __init__(
        self,
        *,
        model: str = "voyage-3",
        dimensions: int = 1024,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            model=model,
            dimensions=dimensions,
            api_key=api_key if api_key is not None else os.environ.get("VOYAGE_API_KEY", ""),
            client=client,
        )

    def _body(self, texts: Sequence[str], *, is_query: bool) -> dict[str, Any]:
        return {
            "model": self._model,
            "input": list(texts),
            # The whole reason to prefer this provider: a question and the
            # passage answering it are embedded for each other rather than
            # as the same kind of text.
            "input_type": "query" if is_query else "document",
            "output_dimension": self._dimensions,
        }

    def _vectors(self, payload: Any) -> list[list[float]]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise EmbeddingFailed("Voyage returned no embeddings")
        # Sorted by index, never trusting the order they arrive in: pairing is
        # positional, so an out-of-order response would mislabel every passage.
        ordered = sorted(data, key=lambda row: row.get("index", 0))
        return [list(row.get("embedding") or ()) for row in ordered]


class OpenAIEmbeddings(_HttpEmbeddings):
    """OpenAI. No input type, so queries and documents are embedded alike."""

    url = "https://api.openai.com/v1/embeddings"
    batch_size = 256

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            model=model,
            dimensions=dimensions,
            api_key=api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", ""),
            client=client,
        )

    def _body(self, texts: Sequence[str], *, is_query: bool) -> dict[str, Any]:
        return {
            "model": self._model,
            "input": list(texts),
            # Supported from text-embedding-3 onwards. Sent always, so the
            # identifier's promise about the vector space is kept.
            "dimensions": self._dimensions,
        }

    def _vectors(self, payload: Any) -> list[list[float]]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise EmbeddingFailed("OpenAI returned no embeddings")
        ordered = sorted(data, key=lambda row: row.get("index", 0))
        return [list(row.get("embedding") or ()) for row in ordered]


def build_embeddings(
    provider: str, *, model: str = "", dimensions: int = 0
) -> Any:
    """The embedder a configuration asks for.

    ``development`` is the word for the hashing embedder, spelled out rather
    than reached by leaving something blank — a deployment should not be able
    to end up on it by omission.
    """
    name = provider.strip().lower()

    if name in ("development", "dev", "hashing"):
        from celeris_bridge.adapters.in_memory import HashingEmbeddings

        return HashingEmbeddings(dimensions=dimensions or 256)

    if name == "voyage":
        return VoyageEmbeddings(
            **({"model": model} if model else {}),
            **({"dimensions": dimensions} if dimensions else {}),
        )

    if name == "openai":
        return OpenAIEmbeddings(
            **({"model": model} if model else {}),
            **({"dimensions": dimensions} if dimensions else {}),
        )

    raise ValueError(
        f'Unknown embeddings provider "{provider}". '
        "Use voyage, openai or development."
    )
