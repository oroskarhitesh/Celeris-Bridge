"""
The Mongo visitor records and passage index, against a real server.

Skipped when none is running. The interesting case here is the one a laptop
actually produces: plain Mongo has no ``$vectorSearch``, so the adapter has to
notice, say so, and fall back — and it must not pretend afterwards that it used
an index.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Sequence

import pytest

from celeris_bridge.adapters.in_memory import HashingEmbeddings
from celeris_bridge.ports.knowledge import KnowledgeScope
from celeris_bridge.ports.visitor import ConversationRef, VisitorRecord

MONGO_URL = os.environ.get("CB_TEST_MONGO_URL", "mongodb://127.0.0.1:27017")

_LOOP = asyncio.new_event_loop()


def run(coroutine: Any) -> Any:
    return _LOOP.run_until_complete(coroutine)


def mongo_is_reachable() -> bool:
    try:
        from pymongo import MongoClient

        MongoClient(MONGO_URL, serverSelectionTimeoutMS=400).admin.command("ping")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not mongo_is_reachable(), reason=f"no Mongo answering at {MONGO_URL}"
)

PRICING = "The Starter plan is 29 per seat per month and includes the shared inbox."
SECURITY = "Celeris Bridge is SOC 2 Type II certified and encrypts data at rest."


@pytest.fixture
def database():
    from celeris_bridge.adapters.mongo import connect, ensure_indexes

    name = f"celeris_test_{uuid.uuid4().hex[:12]}"
    client, database = connect(MONGO_URL, name)
    run(ensure_indexes(database))
    try:
        yield database
    finally:
        run(client.drop_database(name))
        run(client.close())


@pytest.fixture
def visitors(database):
    from celeris_bridge.adapters.mongo import MongoVisitorRecords

    return MongoVisitorRecords(database)


@pytest.fixture
def index(database):
    from celeris_bridge.adapters.mongo import MongoPassageIndex

    return MongoPassageIndex(database)


def embedded(text: str) -> Sequence[float]:
    return HashingEmbeddings().embed(text)


def seed(index, *, workspace_id: str = "w1", source_id: str = "pricing.md", text: str = PRICING, **changed: Any) -> int:
    return run(
        index.add(
            workspace_id=workspace_id,
            source_id=source_id,
            source_name=source_id,
            passages=[(text, embedded(text))],
            **changed,
        )
    )


class TestVisitorRecords:
    def test_a_conversation_with_no_record_reads_as_none(self, visitors) -> None:
        # Different from an empty record: the first means they have shared
        # nothing, the second that something was saved and came to nothing.
        assert run(visitors.load(ConversationRef("w1", "c1"))) is None

    def test_a_record_survives_a_round_trip(self, visitors) -> None:
        record = VisitorRecord(
            details={"full_name": "Priya", "email": "priya@acme.io"},
            visitor_type="prospect",
            status="qualified",
            missing=(),
            reasons=(),
        )
        run(visitors.save(ConversationRef("w1", "c1"), record))
        assert run(visitors.load(ConversationRef("w1", "c1"))) == record

    def test_the_first_thing_a_visitor_shares_creates_the_record(self, visitors) -> None:
        # Upserted, because nothing creates it in advance.
        run(visitors.save(ConversationRef("w1", "c9"), VisitorRecord(details={"a": "1"})))
        assert run(visitors.load(ConversationRef("w1", "c9"))) is not None

    def test_saving_again_replaces_rather_than_duplicates(self, visitors) -> None:
        reference = ConversationRef("w1", "c1")
        run(visitors.save(reference, VisitorRecord(details={"full_name": "Priya"})))
        run(
            visitors.save(
                reference,
                VisitorRecord(details={"full_name": "Priya", "email": "p@acme.io"}, status="qualified"),
            )
        )
        loaded = run(visitors.load(reference))
        assert loaded is not None
        assert loaded.details == {"full_name": "Priya", "email": "p@acme.io"}
        assert loaded.status == "qualified"

    def test_another_workspace_does_not_see_it(self, visitors) -> None:
        run(visitors.save(ConversationRef("w1", "c1"), VisitorRecord(details={"a": "1"})))
        assert run(visitors.load(ConversationRef("w2", "c1"))) is None

    def test_the_derived_verdict_is_stored_with_the_facts(self, visitors) -> None:
        # So a leads list can filter on status without re-running the rules.
        run(
            visitors.save(
                ConversationRef("w1", "c1"),
                VisitorRecord(status="not_a_fit", reasons=("The visitor is a competitor",)),
            )
        )
        loaded = run(visitors.load(ConversationRef("w1", "c1")))
        assert loaded is not None
        assert loaded.status == "not_a_fit"
        assert loaded.reasons == ("The visitor is a competitor",)


class TestThePassageIndex:
    def test_a_document_becomes_searchable(self, index) -> None:
        assert seed(index) == 1
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1", agent_instance_id="a1"),
                query_vector=embedded("how much per seat"),
                limit=5,
                min_score=0.01,
            )
        )
        assert [hit.source_name for hit in hits] == ["pricing.md"]
        assert hits[0].text == PRICING

    def test_a_plain_mongo_has_no_vector_index_and_says_so(
        self, index, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The case a laptop actually produces. Falling back is right; doing it
        # silently is not — over a million passages it is a different product.
        seed(index)
        with caplog.at_level(logging.WARNING):
            run(
                index.search(
                    scope=KnowledgeScope(workspace_id="w1"),
                    query_vector=embedded("per seat"),
                    limit=5,
                    min_score=0.01,
                )
            )
        assert index.last_search_used_index is False
        assert any("vector index" in record.message for record in caplog.records)

    def test_it_only_complains_once(self, index, caplog: pytest.LogCaptureFixture) -> None:
        seed(index)
        with caplog.at_level(logging.WARNING):
            for _ in range(3):
                run(
                    index.search(
                        scope=KnowledgeScope(workspace_id="w1"),
                        query_vector=embedded("per seat"),
                        limit=5,
                        min_score=0.01,
                    )
                )
        assert sum("vector index" in record.message for record in caplog.records) == 1

    def test_configured_to_use_the_index_it_does_not_quietly_scan(self, database) -> None:
        # A deployment that asked for Atlas and got a table scan should fail
        # loudly rather than get slower every month.
        from celeris_bridge.adapters.mongo import MongoPassageIndex
        from pymongo.errors import OperationFailure

        strict = MongoPassageIndex(database, use_vector_search=True)
        seed(strict)
        with pytest.raises(OperationFailure):
            run(
                strict.search(
                    scope=KnowledgeScope(workspace_id="w1"),
                    query_vector=embedded("per seat"),
                    limit=5,
                    min_score=0.01,
                )
            )

    def test_a_search_with_no_workspace_returns_nothing(self, index) -> None:
        # Fail closed, as the port requires. Without it the filter would match
        # every workspace at once.
        seed(index)
        assert (
            run(
                index.search(
                    scope=KnowledgeScope(workspace_id=""),
                    query_vector=embedded("per seat"),
                    limit=5,
                    min_score=0.0,
                )
            )
            == ()
        )

    def test_another_workspaces_passages_are_invisible(self, index) -> None:
        seed(index, workspace_id="w1", source_id="ours.md")
        seed(index, workspace_id="w2", source_id="theirs.md")
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("per seat"),
                limit=10,
                min_score=0.0,
            )
        )
        assert {hit.source_name for hit in hits} == {"ours.md"}

    def test_every_hit_is_stamped_with_the_workspace_it_came_from(self, index) -> None:
        # So the domain's cross-tenant check compares against the document
        # rather than echoing the request back at itself.
        seed(index)
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("per seat"),
                limit=5,
                min_score=0.0,
            )
        )
        assert all(hit.workspace_id == "w1" for hit in hits)

    def test_a_document_bound_to_one_agent_is_invisible_to_another(self, index) -> None:
        seed(index, source_id="bound.md", agent_instance_ids=("agent-one",))
        seed(index, source_id="shared.md")
        mine = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1", agent_instance_id="agent-one"),
                query_vector=embedded("per seat"),
                limit=10,
                min_score=0.0,
            )
        )
        theirs = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1", agent_instance_id="agent-two"),
                query_vector=embedded("per seat"),
                limit=10,
                min_score=0.0,
            )
        )
        assert {hit.source_name for hit in mine} == {"bound.md", "shared.md"}
        assert {hit.source_name for hit in theirs} == {"shared.md"}

    def test_re_reading_a_document_replaces_its_passages(self, index) -> None:
        # Otherwise yesterday's paragraphs keep answering questions with text
        # the customer has already deleted.
        seed(index, text="The Starter plan is 19 per seat.")
        seed(index, text="The Starter plan is 29 per seat.")
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("starter plan per seat"),
                limit=10,
                min_score=0.0,
            )
        )
        assert len(hits) == 1
        assert "29" in hits[0].text

    def test_a_source_can_be_removed(self, index) -> None:
        seed(index)
        assert run(index.remove_source(workspace_id="w1", source_id="pricing.md")) == 1
        assert (
            run(
                index.search(
                    scope=KnowledgeScope(workspace_id="w1"),
                    query_vector=embedded("per seat"),
                    limit=5,
                    min_score=0.0,
                )
            )
            == ()
        )

    def test_the_threshold_leaves_out_what_is_not_relevant(self, index) -> None:
        seed(index, source_id="pricing.md", text=PRICING)
        seed(index, source_id="security.md", text=SECURITY)
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("how much does a seat cost per month"),
                limit=10,
                min_score=0.2,
            )
        )
        assert [hit.source_name for hit in hits] == ["pricing.md"]

    def test_the_best_match_comes_first(self, index) -> None:
        seed(index, source_id="pricing.md", text=PRICING)
        seed(index, source_id="security.md", text=SECURITY)
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("seat per month shared inbox"),
                limit=10,
                min_score=0.0,
            )
        )
        assert hits[0].source_name == "pricing.md"
        assert hits[0].score >= hits[-1].score

    def test_an_approved_passage_says_so(self, index) -> None:
        seed(index, source_id="faq.md", approved=True, source_url="https://acme.test/faq")
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("per seat"),
                limit=5,
                min_score=0.0,
            )
        )
        assert hits[0].approved is True
        assert hits[0].source_url == "https://acme.test/faq"

    def test_passages_from_another_embedder_are_left_out(self, index) -> None:
        # Two vector spaces have nothing to do with each other, so comparing
        # across them does not give worse results — it gives meaningless ones,
        # ranked confidently.
        seed(index, source_id="old.md", model="openai:1536")
        seed(index, source_id="new.md", model="voyage-3:1024")
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("per seat"),
                limit=10,
                min_score=0.0,
                model="voyage-3:1024",
            )
        )
        assert {hit.source_name for hit in hits} == {"new.md"}

    def test_changing_the_model_says_a_re_index_is_due(
        self, index, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The most confusing symptom there is — "the agent has forgotten
        # everything" — turned into one line naming the cause.
        seed(index, source_id="old.md", model="openai:1536")
        with caplog.at_level(logging.WARNING):
            hits = run(
                index.search(
                    scope=KnowledgeScope(workspace_id="w1"),
                    query_vector=embedded("per seat"),
                    limit=10,
                    min_score=0.0,
                    model="voyage-3:1024",
                )
            )
        assert hits == ()
        assert any("re-indexed" in record.message for record in caplog.records)

    def test_an_empty_result_with_no_model_change_says_nothing(
        self, index, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Finding nothing is normal. Only a model mismatch is worth a warning.
        seed(index, source_id="pricing.md", model="voyage-3:1024")
        with caplog.at_level(logging.WARNING):
            run(
                index.search(
                    scope=KnowledgeScope(workspace_id="w1"),
                    query_vector=embedded("completely unrelated subject"),
                    limit=10,
                    min_score=0.99,
                    model="voyage-3:1024",
                )
            )
        assert not any("re-indexed" in record.message for record in caplog.records)

    def test_not_naming_a_model_searches_everything(self, index) -> None:
        # For a store with one embedder, which is most of them.
        seed(index, source_id="old.md", model="openai:1536")
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("per seat"),
                limit=10,
                min_score=0.0,
            )
        )
        assert len(hits) == 1

    def test_a_document_with_nothing_in_it_indexes_nothing(self, index) -> None:
        assert run(
            index.add(
                workspace_id="w1", source_id="empty.md", source_name="empty.md", passages=[]
            )
        ) == 0


class TestIngestionAgainstTheRealIndex:
    """The pipeline, writing into Mongo.

    The pipeline's own tests use the in-memory index, so this is what shows
    the two indexes are the same contract -- and the only place the ``model``
    stamped at write time is seen to be the one the filter matches at read
    time, which is the property that keeps a provider change recoverable
    rather than silent.
    """

    @pytest.fixture
    def documents(self, tmp_path):
        from celeris_bridge.adapters.local_documents import LocalDocumentStore

        return LocalDocumentStore(tmp_path)

    def upload(self, documents, index, *, name="pricing.md", content=None, **changed):
        from celeris_bridge.domain.knowledge.ingestion import ingest

        return run(
            ingest(
                workspace_id="w1",
                source_id="s1",
                name=name,
                content=content if content is not None else PRICING.encode(),
                documents=documents,
                embeddings=_Embedder(),
                index=index,
                **changed,
            )
        )

    def test_the_mongo_index_is_a_passage_writer(self, index) -> None:
        from celeris_bridge.ports.documents import PassageWriter

        assert isinstance(index, PassageWriter)

    def test_an_upload_becomes_rows_in_mongo(self, documents, index) -> None:
        result = self.upload(documents, index)
        assert result.indexed is True
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("how much per seat"),
                limit=10,
                min_score=0.0,
                model=_Embedder().identifier,
            )
        )
        assert len(hits) == result.passages
        assert hits[0].source_name == "pricing.md"

    def test_the_model_written_is_the_model_searched(self, documents, index) -> None:
        self.upload(documents, index)
        other = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("how much per seat"),
                limit=10,
                min_score=0.0,
                model="voyage-3:1024",
            )
        )
        assert other == ()

    def test_re_uploading_replaces_the_rows(self, documents, index) -> None:
        first = self.upload(documents, index, content=(PRICING + " " + SECURITY).encode())
        again = self.upload(documents, index, content=PRICING.encode())
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("per seat"),
                limit=50,
                min_score=0.0,
                model=_Embedder().identifier,
            )
        )
        assert len(hits) == again.passages
        assert "SOC 2" not in " ".join(hit.text for hit in hits)
        assert first.passages >= again.passages

    def test_a_reindex_moves_the_corpus_to_another_embedder(self, documents, index) -> None:
        # The recovery path for the model guard, end to end and on a real
        # database: the old vectors stop matching and the stored original is
        # the only thing that can replace them.
        from celeris_bridge.domain.knowledge.ingestion import reindex_workspace

        self.upload(documents, index)
        changed = _Embedder(dimensions=512)

        def found(embedder):
            return run(
                index.search(
                    scope=KnowledgeScope(workspace_id="w1"),
                    query_vector=embedder.embed_query_sync("per seat"),
                    limit=10,
                    min_score=0.0,
                    model=embedder.identifier,
                )
            )

        assert found(changed) == ()
        run(
            reindex_workspace(
                workspace_id="w1",
                documents=documents,
                embeddings=changed,
                index=index,
            )
        )
        assert found(changed)

    def test_forgetting_a_document_clears_its_rows(self, documents, index) -> None:
        from celeris_bridge.domain.knowledge.ingestion import forget

        result = self.upload(documents, index)
        removed = run(
            forget(workspace_id="w1", source_id="s1", documents=documents, index=index)
        )
        assert removed == result.passages
        assert run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=embedded("per seat"),
                limit=10,
                min_score=0.0,
            )
        ) == ()


class _Embedder:
    """The development embedder, with the async port in front of it."""

    def __init__(self, *, dimensions: int = 256) -> None:
        self._inner = HashingEmbeddings(dimensions=dimensions)

    @property
    def identifier(self) -> str:
        return self._inner.identifier

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def embed_query_sync(self, text: str) -> Sequence[float]:
        return self._inner.embed(text)

    async def embed_query(self, text: str) -> Sequence[float]:
        return self._inner.embed(text)

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return [self._inner.embed(text) for text in texts]
