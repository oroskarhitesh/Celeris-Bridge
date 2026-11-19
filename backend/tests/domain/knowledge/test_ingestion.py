"""
Tests for the ingestion pipeline.

The real in-memory document store and the real in-memory index, with a
recording embedder in front of them — so what is tested is the order of the
five steps and what happens when one of them fails, not a restatement of the
fakes.

Four properties carry the weight:

* a document that cannot be read is still **stored**,
* an embedding failure leaves **yesterday's passages** serving traffic,
* re-ingesting **replaces** rather than adds,
* a change of embedding model is recoverable by re-indexing and nothing else.
"""

from __future__ import annotations

import asyncio
from typing import Any, Sequence

import pytest

from celeris_bridge.adapters.embeddings import EmbeddingFailed
from celeris_bridge.adapters.in_memory import (
    HashingEmbeddings,
    InMemoryDocumentStore,
    InMemoryPassageIndex,
)
from celeris_bridge.domain.knowledge.extraction import (
    UnreadableDocument,
    UnsupportedDocument,
)
from celeris_bridge.domain.knowledge.ingestion import (
    DocumentTooLarge,
    forget,
    ingest,
    reindex,
    reindex_workspace,
)
from celeris_bridge.ports.documents import MissingDocument
from celeris_bridge.ports.knowledge import EmbeddingModel, KnowledgeScope

from tests.domain.knowledge.test_extraction import minimal_pdf


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


HANDBOOK = (
    "Our Starter plan is 29 per seat per month, billed annually. "
    "The Teams plan is 49 per seat and adds single sign-on. "
    "Every plan includes a 14 day trial with no card required. "
    "Support answers within one business day on all plans."
)


class Recorder:
    """A real embedder with a counter and a switch on it."""

    def __init__(self, *, identifier: str = "test-embed:8", dimensions: int = 8) -> None:
        self._inner = HashingEmbeddings(dimensions=dimensions)
        self._identifier = identifier
        self.batches: list[Sequence[str]] = []
        self.fail = False

    @property
    def identifier(self) -> str:
        return self._identifier

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    async def embed_query(self, text: str) -> Sequence[float]:
        return self._inner.embed(text)

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if self.fail:
            raise EmbeddingFailed("the provider is down")
        self.batches.append(list(texts))
        return [self._inner.embed(text) for text in texts]


def world(**changed: Any) -> dict[str, Any]:
    return {
        "documents": InMemoryDocumentStore(),
        "index": InMemoryPassageIndex(),
        "embeddings": Recorder(),
        **changed,
    }


def upload(
    parts: dict[str, Any],
    *,
    name: str = "handbook.txt",
    content: bytes | None = None,
    source_id: str = "s1",
    workspace_id: str = "w1",
    **changed: Any,
) -> Any:
    return run(
        ingest(
            workspace_id=workspace_id,
            source_id=source_id,
            name=name,
            content=content if content is not None else HANDBOOK.encode(),
            documents=parts["documents"],
            embeddings=parts["embeddings"],
            index=parts["index"],
            **changed,
        )
    )


def passages(parts: dict[str, Any], *, workspace_id: str = "w1", query: str = "price") -> Any:
    """Whatever the index will return for a query, through the real search."""
    embedder = parts["embeddings"]
    vector = run(embedder.embed_query(query))
    return run(
        parts["index"].search(
            scope=KnowledgeScope(workspace_id=workspace_id, agent_instance_id="a1"),
            query_vector=vector,
            limit=50,
            min_score=0.0,
            model=embedder.identifier,
        )
    )


class TestTheHappyPath:
    def test_a_document_becomes_searchable_passages(self) -> None:
        parts = world()
        result = upload(parts)
        assert result.indexed is True
        assert result.passages >= 1
        assert len(passages(parts)) == result.passages

    def test_the_result_says_what_was_done(self) -> None:
        parts = world()
        result = upload(parts)
        assert result.source_id == "s1"
        assert result.source_name == "handbook.txt"
        assert result.kind == "text"
        assert result.characters == len(HANDBOOK)
        # The embedder is on the result because it is the thing to check first
        # when a workspace stops finding anything.
        assert result.model == "test-embed:8"

    def test_the_original_is_kept(self) -> None:
        # Not a nicety: the index refuses vectors from another model, so
        # without the bytes a change of provider is unrecoverable.
        parts = world()
        upload(parts)
        stored = run(parts["documents"].content(workspace_id="w1", source_id="s1"))
        assert stored == HANDBOOK.encode()

    def test_the_whole_document_is_embedded_in_one_call(self) -> None:
        # The provider batches inside that call. Hundreds of one-chunk
        # requests is the difference between seconds and minutes.
        parts = world()
        result = upload(parts, content=(HANDBOOK * 40).encode())
        assert result.passages > 1
        assert len(parts["embeddings"].batches) == 1

    def test_what_was_done_is_recorded_against_the_document(self) -> None:
        # What makes the admin list ("14 sources, 402 passages") and the
        # re-index work list answerable without reading every file.
        parts = world()
        result = upload(parts)
        stored = run(parts["documents"].describe(workspace_id="w1", source_id="s1"))
        assert stored.indexed_model == "test-embed:8"
        assert stored.indexed_passages == result.passages

    def test_a_pdf_goes_through_the_same_path(self) -> None:
        parts = world()
        result = upload(parts, name="pricing.pdf", content=minimal_pdf(HANDBOOK))
        assert result.kind == "pdf"
        assert result.indexed is True

    def test_the_file_name_is_the_citation_by_default(self) -> None:
        # A file the customer named "2024 Pricing.pdf" is cited as that, not
        # as whatever is in its metadata -- usually the template it came from.
        parts = world()
        page = b"<html><head><title>Plans and pricing</title></head><body><p>" + HANDBOOK.encode() + b"</p></body></html>"
        result = upload(parts, name="pricing.html", content=page)
        assert result.source_name == "pricing.html"
        assert passages(parts)[0].source_name == "pricing.html"

    def test_a_page_can_name_itself_when_asked(self) -> None:
        # Which is what the crawler wants: a URL is not a citation.
        parts = world()
        page = b"<html><head><title>Plans and pricing</title></head><body><p>" + HANDBOOK.encode() + b"</p></body></html>"
        result = upload(parts, name="pricing.html", content=page, use_document_title=True)
        assert result.source_name == "Plans and pricing"

    def test_an_approved_document_keeps_being_approved(self) -> None:
        parts = world()
        upload(parts, approved=True)
        assert all(hit.approved for hit in passages(parts))

    def test_a_document_bound_to_one_agent_is_not_shared(self) -> None:
        parts = world()
        upload(parts, agent_instance_ids=["a9"])
        assert passages(parts) == ()


class TestReplacing:
    def test_re_ingesting_replaces_rather_than_adds(self) -> None:
        parts = world()
        first = upload(parts)
        again = upload(parts)
        assert len(passages(parts)) == again.passages == first.passages

    def test_deleted_text_stops_being_retrievable(self) -> None:
        # The reason replace is the rule: an edited document must not keep
        # answering with the paragraph the customer removed.
        parts = world()
        upload(parts)
        upload(parts, content=b"The Starter plan is 29 per seat per month, billed annually.")
        texts = " ".join(hit.text for hit in passages(parts))
        assert "single sign-on" not in texts

    def test_a_document_emptied_on_purpose_stops_answering(self) -> None:
        parts = world()
        upload(parts)
        result = upload(parts, content=b"   ")
        assert result.indexed is False
        assert result.skipped
        assert passages(parts) == ()

    def test_a_scan_with_no_text_is_reported_not_raised(self) -> None:
        # Nothing went wrong -- there is simply nothing to index, and the only
        # useful thing to say is that it needs OCR.
        parts = world()
        result = upload(parts, name="scan.pdf", content=minimal_pdf(" "))
        assert result.indexed is False
        assert "OCR" in result.skipped


class TestWhenSomethingFails:
    def test_a_file_that_cannot_be_read_is_still_stored(self) -> None:
        # So the admin can download what they uploaded and see for themselves,
        # and so a bug in extraction never loses a customer's file.
        parts = world()
        with pytest.raises(UnsupportedDocument):
            upload(parts, name="deck.pptx", content=b"PK\x03\x04 nope")
        stored = run(parts["documents"].describe(workspace_id="w1", source_id="s1"))
        assert stored is not None
        assert stored.indexed_model == ""

    def test_a_pdf_that_is_not_one_is_refused(self) -> None:
        parts = world()
        with pytest.raises(UnreadableDocument):
            upload(parts, name="prices.pdf", content=b"PK\x03\x04 a zip")

    def test_a_document_past_the_limit_is_refused_and_not_truncated(self) -> None:
        # Answering from the first two-thirds of a contract without saying so
        # is worse than refusing it.
        parts = world()
        with pytest.raises(DocumentTooLarge) as caught:
            upload(parts, content=b"word word. " * 400, max_characters=1_000)
        assert "1,000" in str(caught.value)

    def test_an_embedding_failure_leaves_the_old_passages_in_place(self) -> None:
        # The whole reason embedding happens before writing. A provider outage
        # must not be the moment a workspace's knowledge disappears.
        parts = world()
        first = upload(parts)
        parts["embeddings"].fail = True
        with pytest.raises(EmbeddingFailed):
            upload(parts, content=b"A completely different handbook about pricing.")
        assert len(passages(parts)) == first.passages

    def test_a_failed_ingest_does_not_claim_to_be_indexed(self) -> None:
        parts = world()
        upload(parts)
        parts["embeddings"].fail = True
        with pytest.raises(EmbeddingFailed):
            upload(parts, content=b"Something else entirely about plans.")
        stored = run(parts["documents"].describe(workspace_id="w1", source_id="s1"))
        # put() reset it, and nothing has re-indexed: the document is pending,
        # which is exactly what it is.
        assert stored.indexed_model == ""

    def test_a_document_with_no_workspace_is_refused(self) -> None:
        # Fail closed. Passages with no workspace could never be scoped, and
        # the index's own guard would then refuse to search them.
        parts = world()
        with pytest.raises(ValueError):
            upload(parts, workspace_id="")


class TestReindexing:
    def test_a_stored_document_can_be_indexed_again(self) -> None:
        parts = world()
        first = upload(parts)
        result = run(
            reindex(
                workspace_id="w1",
                source_id="s1",
                documents=parts["documents"],
                embeddings=parts["embeddings"],
                index=parts["index"],
            )
        )
        assert result.passages == first.passages

    def test_changing_the_embedder_is_survivable(self) -> None:
        # This is the recovery path for the index's model guard: vectors from
        # the old embedder are ignored, and the only way back is the text.
        parts = world()
        upload(parts)

        parts["embeddings"] = Recorder(identifier="other-embed:8")
        assert passages(parts) == ()

        run(
            reindex_workspace(
                workspace_id="w1",
                documents=parts["documents"],
                embeddings=parts["embeddings"],
                index=parts["index"],
            )
        )
        assert len(passages(parts)) > 0

    def test_only_the_stale_documents_are_done(self) -> None:
        # So running it twice costs nothing the second time, and an
        # interrupted run resumes instead of starting over.
        parts = world()
        upload(parts, source_id="s1")
        upload(parts, source_id="s2")
        parts["embeddings"].batches.clear()

        again = run(
            reindex_workspace(
                workspace_id="w1",
                documents=parts["documents"],
                embeddings=parts["embeddings"],
                index=parts["index"],
            )
        )
        assert again == ()
        assert parts["embeddings"].batches == []

    def test_one_broken_document_does_not_stop_the_others(self) -> None:
        # A single locked PDF must not leave the other forty unsearchable.
        parts = world()
        upload(parts, source_id="s1")
        with pytest.raises(UnsupportedDocument):
            upload(parts, source_id="s2", name="deck.pptx", content=b"PK\x03\x04")

        parts["embeddings"] = Recorder(identifier="other-embed:8")
        results = run(
            reindex_workspace(
                workspace_id="w1",
                documents=parts["documents"],
                embeddings=parts["embeddings"],
                index=parts["index"],
            )
        )
        done = {result.source_id: result for result in results}
        assert done["s1"].indexed is True
        assert done["s2"].indexed is False
        # The failure is reported rather than raised, so a screen can list it.
        assert "pptx" in done["s2"].skipped

    def test_a_pass_can_be_capped(self) -> None:
        # A job that would rather make progress every minute than hold a
        # connection for an hour.
        parts = world()
        for number in range(4):
            upload(parts, source_id=f"s{number}")
        parts["embeddings"] = Recorder(identifier="other-embed:8")
        assert len(
            run(
                reindex_workspace(
                    workspace_id="w1",
                    documents=parts["documents"],
                    embeddings=parts["embeddings"],
                    index=parts["index"],
                    limit=2,
                )
            )
        ) == 2

    def test_re_indexing_does_not_rename_the_source(self) -> None:
        # Past conversations cite it by the name it had; re-reading the file
        # must not change what those citations said.
        parts = world()
        page = b"<html><head><title>Plans</title></head><body><p>" + HANDBOOK.encode() + b"</p></body></html>"
        upload(parts, name="pricing.html", content=page, content_type="text/html")
        result = run(
            reindex(
                workspace_id="w1",
                source_id="s1",
                documents=parts["documents"],
                embeddings=parts["embeddings"],
                index=parts["index"],
            )
        )
        assert result.source_name == "pricing.html"

    def test_nothing_stored_says_so(self) -> None:
        parts = world()
        with pytest.raises(MissingDocument):
            run(
                reindex(
                    workspace_id="w1",
                    source_id="never-uploaded",
                    documents=parts["documents"],
                    embeddings=parts["embeddings"],
                    index=parts["index"],
                )
            )

    def test_one_workspace_cannot_reindex_another(self) -> None:
        parts = world()
        upload(parts, workspace_id="w1")
        results = run(
            reindex_workspace(
                workspace_id="w2",
                documents=parts["documents"],
                embeddings=parts["embeddings"],
                index=parts["index"],
            )
        )
        assert results == ()


class TestForgetting:
    def test_the_passages_go_with_the_document(self) -> None:
        parts = world()
        first = upload(parts)
        removed = run(
            forget(
                workspace_id="w1",
                source_id="s1",
                documents=parts["documents"],
                index=parts["index"],
            )
        )
        assert removed == first.passages
        assert passages(parts) == ()
        assert run(parts["documents"].describe(workspace_id="w1", source_id="s1")) is None

    def test_forgetting_something_that_is_not_there_is_not_an_error(self) -> None:
        # A delete pressed twice, or a retried request.
        parts = world()
        assert (
            run(
                forget(
                    workspace_id="w1",
                    source_id="s1",
                    documents=parts["documents"],
                    index=parts["index"],
                )
            )
            == 0
        )

    def test_one_workspace_cannot_delete_another_ones_document(self) -> None:
        parts = world()
        upload(parts, workspace_id="w1")
        run(
            forget(
                workspace_id="w2",
                source_id="s1",
                documents=parts["documents"],
                index=parts["index"],
            )
        )
        assert len(passages(parts, workspace_id="w1")) > 0


class TestThePortsAreSatisfied:
    def test_the_recorder_is_an_embedding_model(self) -> None:
        assert isinstance(Recorder(), EmbeddingModel)
