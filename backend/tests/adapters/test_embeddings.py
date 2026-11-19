"""
Tests for the embeddings adapters.

No network and no key: httpx takes a ``MockTransport``, so the request each
adapter builds and the response it parses are both real, and the only thing
replaced is the wire.

Three things carry the weight here: that a question is embedded differently
from a passage, that batching returns one vector per text *in order*, and that
a short or reordered response is caught rather than quietly pairing vectors
with the wrong chunks.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Sequence

import httpx
import pytest

from celeris_bridge.adapters.embeddings import (
    EmbeddingFailed,
    OpenAIEmbeddings,
    VoyageEmbeddings,
    build_embeddings,
)
from celeris_bridge.adapters.in_memory import HashingEmbeddings
from celeris_bridge.ports.knowledge import EmbeddingModel

DIMENSIONS = 4


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def vector(seed: float = 0.1) -> list[float]:
    return [seed] * DIMENSIONS


class Recorder:
    """A stub provider. Records requests, answers with what it is told to."""

    def __init__(
        self,
        responder: Callable[[dict[str, Any]], httpx.Response] | None = None,
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self._responder = responder

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.requests.append({"url": str(request.url), "headers": dict(request.headers), **body})
            if self._responder is not None:
                return self._responder(body)
            texts = body.get("input") or []
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": position, "embedding": vector(0.1 * (position + 1))}
                        for position in range(len(texts))
                    ]
                },
            )

        return httpx.MockTransport(handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())


def voyage(recorder: Recorder, **changed: Any) -> VoyageEmbeddings:
    return VoyageEmbeddings(
        model=changed.pop("model", "voyage-3"),
        dimensions=changed.pop("dimensions", DIMENSIONS),
        api_key="test-key",
        client=recorder.client(),
        **changed,
    )


def openai(recorder: Recorder, **changed: Any) -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=changed.pop("model", "text-embedding-3-small"),
        dimensions=changed.pop("dimensions", DIMENSIONS),
        api_key="test-key",
        client=recorder.client(),
        **changed,
    )


class TestTheContract:
    def test_both_adapters_satisfy_the_port(self) -> None:
        recorder = Recorder()
        assert isinstance(voyage(recorder), EmbeddingModel)
        assert isinstance(openai(recorder), EmbeddingModel)

    def test_the_development_embedder_still_does_too(self) -> None:
        # It gained embed_documents with this change.
        assert isinstance(HashingEmbeddings(), EmbeddingModel)

    def test_the_identifier_names_the_model_and_its_size(self) -> None:
        # Both change the vector space, so both have to be in the thing that
        # is stored with each passage and compared at search time.
        assert voyage(Recorder()).identifier == "voyage-3:4"
        assert openai(Recorder()).identifier == "text-embedding-3-small:4"
        assert HashingEmbeddings(dimensions=256).identifier == "hashing-dev:256"

    def test_the_same_model_at_two_sizes_is_two_identifiers(self) -> None:
        recorder = Recorder()
        assert voyage(recorder, dimensions=256).identifier != voyage(
            recorder, dimensions=1024
        ).identifier

    def test_no_key_is_refused_at_construction(self) -> None:
        # Not on the first call, half way through ingesting a document.
        with pytest.raises(EmbeddingFailed):
            VoyageEmbeddings(api_key="")


class TestWhatIsSent:
    def test_a_query_is_embedded_as_a_query(self) -> None:
        # The reason Voyage is the default: a question and the passage that
        # answers it are not the same kind of text.
        recorder = Recorder()
        run(voyage(recorder).embed_query("how much per seat?"))
        assert recorder.requests[0]["input_type"] == "query"
        assert recorder.requests[0]["input"] == ["how much per seat?"]

    def test_a_passage_is_embedded_as_a_document(self) -> None:
        recorder = Recorder()
        run(voyage(recorder).embed_documents(["The Starter plan is 29 per seat."]))
        assert recorder.requests[0]["input_type"] == "document"

    def test_the_size_is_asked_for_explicitly(self) -> None:
        # Otherwise the provider's default decides, and the identifier's
        # promise about the vector space is a guess.
        recorder = Recorder()
        run(voyage(recorder).embed_query("x"))
        assert recorder.requests[0]["output_dimension"] == DIMENSIONS

        recorder = Recorder()
        run(openai(recorder).embed_query("x"))
        assert recorder.requests[0]["dimensions"] == DIMENSIONS

    def test_openai_has_no_input_type_to_send(self) -> None:
        recorder = Recorder()
        run(openai(recorder).embed_query("x"))
        assert "input_type" not in recorder.requests[0]

    def test_the_key_goes_in_the_header_and_not_the_body(self) -> None:
        recorder = Recorder()
        run(voyage(recorder).embed_query("x"))
        request = recorder.requests[0]
        assert request["headers"]["authorization"] == "Bearer test-key"
        assert "test-key" not in json.dumps({k: v for k, v in request.items() if k != "headers"})

    def test_each_adapter_calls_its_own_provider(self) -> None:
        recorder = Recorder()
        run(voyage(recorder).embed_query("x"))
        assert "voyageai.com" in recorder.requests[0]["url"]

        recorder = Recorder()
        run(openai(recorder).embed_query("x"))
        assert "openai.com" in recorder.requests[0]["url"]


class TestBatching:
    def test_a_long_list_is_split_into_several_requests(self) -> None:
        # Hundreds of one-text requests is the difference between seconds and
        # minutes, and an invitation to a rate limit.
        recorder = Recorder()
        adapter = voyage(recorder)
        adapter.batch_size = 3
        vectors = run(adapter.embed_documents([f"passage {n}" for n in range(7)]))
        assert len(recorder.requests) == 3
        assert [len(request["input"]) for request in recorder.requests] == [3, 3, 1]
        assert len(vectors) == 7

    def test_every_text_gets_exactly_one_vector(self) -> None:
        recorder = Recorder()
        vectors = run(voyage(recorder).embed_documents(["a", "b", "c"]))
        assert len(vectors) == 3
        assert all(len(one) == DIMENSIONS for one in vectors)

    def test_the_order_is_the_order_the_texts_were_given_in(self) -> None:
        # Pairing with chunks is positional: a reordered result mislabels every
        # passage, and nothing downstream could tell.
        def shuffled(body: dict[str, Any]) -> httpx.Response:
            rows = [
                {"index": position, "embedding": vector(0.1 * (position + 1))}
                for position in range(len(body["input"]))
            ]
            return httpx.Response(200, json={"data": list(reversed(rows))})

        recorder = Recorder(shuffled)
        vectors = run(voyage(recorder).embed_documents(["a", "b", "c"]))
        assert vectors[0][0] == pytest.approx(0.1)
        assert vectors[2][0] == pytest.approx(0.3)

    def test_nothing_in_means_no_request_at_all(self) -> None:
        recorder = Recorder()
        assert run(voyage(recorder).embed_documents([])) == []
        assert recorder.requests == []

    def test_a_short_answer_is_refused_rather_than_misaligned(self) -> None:
        # Two vectors for three passages would shift every pairing after the
        # gap. There is no safe way to carry on.
        def short(body: dict[str, Any]) -> httpx.Response:
            return httpx.Response(
                200, json={"data": [{"index": 0, "embedding": vector()}]}
            )

        with pytest.raises(EmbeddingFailed) as caught:
            run(voyage(Recorder(short)).embed_documents(["a", "b", "c"]))
        assert "3" in str(caught.value)

    def test_a_vector_of_the_wrong_size_is_refused(self) -> None:
        # It would be stored, searched against, and match nothing — with no
        # error anywhere.
        def wrong(body: dict[str, Any]) -> httpx.Response:
            return httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
            )

        with pytest.raises(EmbeddingFailed) as caught:
            run(voyage(Recorder(wrong)).embed_query("x"))
        assert "dimensions" in str(caught.value)


class TestWhenTheProviderMisbehaves:
    def test_a_bad_key_is_not_retried(self) -> None:
        # It is not going to fix itself, and three retries only delay the
        # error by several seconds.
        attempts: list[int] = []

        def refuse(body: dict[str, Any]) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(401, text="invalid api key")

        with pytest.raises(EmbeddingFailed) as caught:
            run(voyage(Recorder(refuse)).embed_query("x"))
        assert len(attempts) == 1
        assert "401" in str(caught.value)

    def test_a_rate_limit_is_retried(self) -> None:
        attempts: list[int] = []

        def limited(body: dict[str, Any]) -> httpx.Response:
            attempts.append(1)
            if len(attempts) < 2:
                return httpx.Response(429, text="slow down")
            return httpx.Response(
                200, json={"data": [{"index": 0, "embedding": vector()}]}
            )

        adapter = voyage(Recorder(limited))
        # Patched so the test does not actually wait out the backoff.
        import celeris_bridge.adapters.embeddings as module

        original = module._BACKOFF_SECONDS
        module._BACKOFF_SECONDS = 0.0
        try:
            assert len(run(adapter.embed_query("x"))) == DIMENSIONS
        finally:
            module._BACKOFF_SECONDS = original
        assert len(attempts) == 2

    def test_an_answer_with_no_embeddings_in_it_is_a_failure(self) -> None:
        def empty(body: dict[str, Any]) -> httpx.Response:
            return httpx.Response(200, json={"detail": "something else"})

        with pytest.raises(EmbeddingFailed):
            run(voyage(Recorder(empty)).embed_query("x"))

    def test_a_failure_never_returns_a_zero_vector(self) -> None:
        # A zero vector is perfectly valid to store and silently matches
        # nothing, which turns an outage into an agent that has quietly
        # forgotten everything.
        def refuse(body: dict[str, Any]) -> httpx.Response:
            return httpx.Response(400, text="no")

        with pytest.raises(EmbeddingFailed):
            run(voyage(Recorder(refuse)).embed_query("x"))


class TestChoosingAProvider:
    def test_each_provider_can_be_named(self) -> None:
        import os

        os.environ["VOYAGE_API_KEY"] = "test"
        os.environ["OPENAI_API_KEY"] = "test"
        try:
            assert isinstance(build_embeddings("voyage"), VoyageEmbeddings)
            assert isinstance(build_embeddings("openai"), OpenAIEmbeddings)
            assert isinstance(build_embeddings("development"), HashingEmbeddings)
        finally:
            os.environ.pop("VOYAGE_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)

    def test_the_development_one_has_to_be_asked_for_by_name(self) -> None:
        # Not reachable by leaving something blank: a deployment should not be
        # able to end up on a word-hash by omission.
        with pytest.raises(ValueError):
            build_embeddings("")

    def test_an_unknown_provider_says_what_the_options_are(self) -> None:
        with pytest.raises(ValueError) as caught:
            build_embeddings("cohere")
        assert "voyage" in str(caught.value)

    def test_the_defaults_are_the_current_models(self) -> None:
        import os

        os.environ["VOYAGE_API_KEY"] = "test"
        try:
            assert build_embeddings("voyage").identifier == "voyage-3:1024"
        finally:
            os.environ.pop("VOYAGE_API_KEY", None)
