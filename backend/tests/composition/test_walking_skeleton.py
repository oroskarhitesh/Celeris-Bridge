"""
The skeleton, wired the way the script wires it.

Everything real except the model: the shipped definition, the real registry,
the real in-memory adapters, the real prompt builder, the real turn loop. The
model is scripted, because a test that spends money on every run is a test
people stop running.

This is the first test of *composition* rather than of a module — that what
``build_services`` produces is what the abilities actually need, which no
amount of hand-made fakes can establish.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

import pytest

from celeris_bridge.adapters.in_memory import (
    HashingEmbeddings,
    InMemoryCalendar,
    InMemoryPassageIndex,
    InMemoryVisitorRecords,
    SystemClock,
)
from celeris_bridge.composition.build import (
    UnpublishableDefinition,
    build_agent,
    build_documents,
    build_embedder,
    build_services,
    build_stores,
)
from celeris_bridge.composition.settings import Settings
from celeris_bridge.domain.knowledge.ingestion import ingest, reindex_workspace
from celeris_bridge.domain.agent.engine.instructions import build_prompt
from celeris_bridge.domain.agent.engine.runner import TurnContext
from celeris_bridge.domain.agent.engine.turn import Ending, run_turn
from celeris_bridge.ports.calendar import Calendar, CalendarScope, Clock
from celeris_bridge.ports.documents import DocumentStore, PassageWriter
from celeris_bridge.ports.chat import (
    ChatMessage,
    ChatReply,
    TextBlock,
    ToolSpec,
    ToolUseBlock,
    Usage,
)
from celeris_bridge.ports.knowledge import EmbeddingModel, KnowledgeScope, PassageIndex
from celeris_bridge.ports.store import ConversationStore, InstanceStore, TemplateStore
from celeris_bridge.ports.visitor import ConversationRef, VisitorRecord, VisitorRecords

PRICING = (
    "Pricing. Celeris Bridge is sold per seat. The Starter plan is 29 per seat per "
    "month and includes the shared inbox, web chat and email. The Growth plan is 49 "
    "per seat per month and adds the voice add-on. Annual billing saves two months."
)
SECURITY = (
    "Security. Celeris Bridge is SOC 2 Type II certified and audited annually. Data "
    "is encrypted in transit with TLS 1.3 and at rest with AES-256."
)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def options(**changed: Any) -> Settings:
    defaults: dict[str, Any] = {
        "adapters": "memory",
        "definition": "marketing_v1",
        "company": "Acme",
    }
    return Settings(**{**defaults, **changed})


class ScriptedModel:
    def __init__(self, *replies: ChatReply) -> None:
        self.replies = list(replies)
        self.asked: list[dict[str, Any]] = []

    async def chat(
        self,
        *,
        system: str,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        model: str = "",
        max_tokens: int = 1024,
        effort: str = "",
    ) -> ChatReply:
        self.asked.append({"system": system, "tools": list(tools), "model": model, "effort": effort})
        return self.replies.pop(0) if self.replies else ChatReply(
            content=(TextBlock(text="(nothing scripted)"),)
        )


def says(text: str) -> ChatReply:
    return ChatReply(
        content=(TextBlock(text=text),),
        model="claude-opus-5-5",
        usage=Usage(input_tokens=200, output_tokens=40),
    )


class TestWhatGetsBuilt:
    def test_every_port_is_satisfied_by_what_composition_builds(self) -> None:
        services = build_services(options())
        assert isinstance(services.embeddings, EmbeddingModel)
        assert isinstance(services.passages, PassageIndex)
        assert isinstance(services.visitors, VisitorRecords)
        assert isinstance(services.calendar, Calendar)
        assert isinstance(services.clock, Clock)

    def test_the_index_and_the_embedder_are_the_same_pair(self) -> None:
        # Vectors from two different embedders are not comparable, so an index
        # built with one and searched with another silently returns nothing.
        services = build_services(options())
        services.passages.add_document(
            workspace_id="w1", source_id="s1", source_name="pricing.md", text=PRICING
        )
        vector = run(services.embeddings.embed_query("how much per seat"))
        hits = run(
            services.passages.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=vector,
                limit=3,
                min_score=0.01,
            )
        )
        assert hits

    def test_asking_for_mongo_without_a_url_is_refused(self) -> None:
        with pytest.raises(RuntimeError) as caught:
            build_services(options(adapters="mongo"))
        assert "CB_MONGO_URL" in str(caught.value)

    def test_asking_for_mongo_does_not_quietly_fall_back_to_memory(self) -> None:
        # A deployment that thinks it has a database and does not would lose
        # every conversation and report nothing wrong. Connecting is lazy, so
        # this builds without a server; what matters is which adapters it is.
        configured = options(adapters="mongo", mongo_url="mongodb://localhost:27017")
        services = build_services(configured)
        stores = build_stores(configured)
        assert type(services.passages).__name__ == "MongoPassageIndex"
        assert type(services.visitors).__name__ == "MongoVisitorRecords"
        assert type(stores.templates).__name__ == "MongoTemplateStore"
        assert type(stores.instances).__name__ == "MongoInstanceStore"
        assert type(stores.conversations).__name__ == "MongoConversationStore"

    def test_the_memory_build_still_gets_memory_stores(self) -> None:
        stores = build_stores(options())
        assert isinstance(stores.templates, TemplateStore)
        assert isinstance(stores.instances, InstanceStore)
        assert isinstance(stores.conversations, ConversationStore)
        assert stores.database is None

    def test_a_document_store_is_built_too(self) -> None:
        stores = build_stores(options())
        assert isinstance(stores.documents, DocumentStore)
        assert isinstance(build_services(options()).passages, PassageWriter)

    def test_documents_go_to_disk_when_a_root_is_configured(self, tmp_path) -> None:
        store = build_documents(options(document_root=str(tmp_path)))
        assert type(store).__name__ == "LocalDocumentStore"
        assert store.root == tmp_path.resolve()

    def test_no_root_keeps_them_in_the_process(self) -> None:
        assert type(build_documents(options())).__name__ == "InMemoryDocumentStore"


class TestIngestionThroughTheRealWiring:
    """An upload, end to end, with nothing hand-made in the path.

    The pipeline, the embedder and the index all come from ``build_*``, so this
    is the test that would catch the embedder and the index being built as two
    different vector spaces -- which no test of the pipeline alone can see.
    """

    def upload(self, parts, *, name="pricing.md", content=None, **changed):
        services, stores = parts
        return run(
            ingest(
                workspace_id="w1",
                source_id="s1",
                name=name,
                content=content if content is not None else PRICING.encode(),
                documents=stores.documents,
                embeddings=services.embeddings,
                index=services.passages,
                **changed,
            )
        )

    @pytest.fixture()
    def parts(self, tmp_path):
        configured = options(document_root=str(tmp_path))
        return build_services(configured), build_stores(configured)

    def test_an_uploaded_document_is_searchable_through_the_real_index(self, parts) -> None:
        services, _ = parts
        result = self.upload(parts)
        assert result.indexed is True
        vector = run(services.embeddings.embed_query("how much per seat"))
        hits = run(
            services.passages.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=vector,
                limit=3,
                min_score=0.01,
                model=services.embeddings.identifier,
            )
        )
        assert hits
        assert hits[0].source_name == "pricing.md"

    def test_the_original_survives_on_disk(self, parts, tmp_path) -> None:
        self.upload(parts)
        assert (tmp_path / "w1" / "s1" / "content").read_bytes() == PRICING.encode()

    def test_the_agent_answers_from_an_uploaded_document(self, parts) -> None:
        # The whole point of the chunk: a file goes in one end and the agent
        # cites it out of the other, with no test fixture in between.
        services, _ = parts
        self.upload(parts)
        agent = build_agent(options=options())
        prompt = build_prompt(
            definition=agent.definition,
            settings=agent.config.settings,
            locked_rules=agent.config.locked_rules,
            company="Acme",
            abilities=agent.briefs,
            visitor=VisitorRecord(),
        )
        result = run(
            run_turn(
                config=agent.config,
                registry=agent.registry,
                chat=ScriptedModel(says("Our Starter plan is 29 per seat per month.")),
                system=prompt.text,
                company="Acme",
                message="how much is it per seat?",
                context=TurnContext(
                    workspace_id="w1",
                    agent_instance_id="a1",
                    conversation_id="c1",
                    services=services,
                ),
            )
        )
        assert result.ending is Ending.ANSWERED
        assert [source["name"] for source in result.sources] == ["pricing.md"]

    def test_a_change_of_embedder_is_recovered_by_reindexing(self, parts) -> None:
        # Against the real adapters, one index, two embedders: the vectors
        # already in it are not comparable to the new one's, and the stored
        # original is the only way back.
        services, stores = parts
        self.upload(parts)

        changed = build_embedder(options(embedding_dimensions=512))

        def found(embedder):
            return run(
                services.passages.search(
                    scope=KnowledgeScope(workspace_id="w1"),
                    query_vector=run(embedder.embed_query("how much per seat")),
                    limit=3,
                    min_score=0.01,
                    model=embedder.identifier,
                )
            )

        assert found(services.embeddings)
        assert found(changed) == ()

        run(
            reindex_workspace(
                workspace_id="w1",
                documents=stores.documents,
                embeddings=changed,
                index=services.passages,
            )
        )
        assert found(changed)
        # And the old vectors are gone rather than lying around to be found by
        # whichever embedder asks next.
        assert found(services.embeddings) == ()


class TestTheAgentThatGetsBuilt:
    def test_the_shipped_definition_publishes_against_the_real_registry(self) -> None:
        agent = build_agent(options=options())
        assert agent.definition.identity.name == "Marketing"
        assert agent.config.missing_abilities == ()

    def test_a_definition_that_does_not_exist_is_a_clear_failure(self) -> None:
        with pytest.raises(FileNotFoundError):
            build_agent("no_such_definition", options=options())

    def test_the_abilities_that_are_on_by_default_are_on(self) -> None:
        agent = build_agent(options=options())
        enabled = {ability.key for ability in agent.config.enabled_abilities}
        assert enabled == {"search_knowledge", "save_visitor_details", "suggest_replies"}

    def test_a_development_override_goes_through_the_real_validator(self) -> None:
        # No back door: a patch here is refused for exactly the reasons a
        # customer's would be.
        agent = build_agent(
            options=options(), patch={"settings": {"agentName": "Noor"}}
        )
        assert agent.config.settings["agentName"] == "Noor"

        from celeris_bridge.domain.agent.overrides import OverridesRejected

        with pytest.raises(OverridesRejected):
            build_agent(options=options(), patch={"settings": {"chatModel": "something"}})

    def test_turning_the_booking_abilities_on_is_a_patch_away(self) -> None:
        agent = build_agent(
            options=options(),
            patch={"abilities": {"find_slots": {"enabled": True}, "book_slot": {"enabled": True}}},
        )
        enabled = {ability.key for ability in agent.config.enabled_abilities}
        assert {"find_slots", "book_slot"} <= enabled

    def test_the_model_can_be_overridden_for_a_local_run(self) -> None:
        agent = build_agent(options=options(chat_model="claude-haiku-5-5"))
        assert agent.config.settings["chatModel"] == "claude-haiku-5-5"

    def test_the_definitions_own_model_is_used_otherwise(self) -> None:
        agent = build_agent(options=options())
        assert agent.config.settings["chatModel"] == "claude-opus-5-5"

    def test_each_enabled_ability_brings_its_instructions_to_the_prompt(self) -> None:
        agent = build_agent(options=options())
        names = {brief.name for brief in agent.briefs}
        assert names == {"search_knowledge", "save_visitor_details", "suggest_replies"}
        assert all(brief.instructions for brief in agent.briefs)


class TestATurnThroughTheRealWiring:
    @pytest.fixture
    def wired(self):
        agent = build_agent(options=options())
        services = build_services(options())
        services.passages.add_document(
            workspace_id="w1", source_id="pricing.md", source_name="pricing.md", text=PRICING
        )
        services.passages.add_document(
            workspace_id="w1", source_id="security.md", source_name="security.md", text=SECURITY
        )
        context = TurnContext(
            workspace_id="w1",
            agent_instance_id="a1",
            conversation_id="c1",
            services=services,
        )
        return agent, services, context

    def take(self, wired, model: ScriptedModel, message: str, **changed: Any):
        agent, services, context = wired
        visitor = run(services.visitors.load(ConversationRef("w1", "c1"))) or VisitorRecord()
        prompt = build_prompt(
            definition=agent.definition,
            settings=agent.config.settings,
            locked_rules=agent.config.locked_rules,
            company="Acme",
            abilities=agent.briefs,
            visitor=visitor,
        )
        return run(
            run_turn(
                config=agent.config,
                registry=agent.registry,
                chat=model,
                system=prompt.text,
                company="Acme",
                message=message,
                context=context,
                **changed,
            )
        )

    def test_a_question_searches_the_real_index_and_answers(self, wired) -> None:
        model = ScriptedModel(says("The Starter plan is 29 per seat per month."))
        result = self.take(wired, model, "how much is it per seat?")
        assert result.ending is Ending.ANSWERED
        assert [call.key for call in result.calls] == ["search_knowledge"]
        assert result.calls[0].ok is True

    def test_the_passage_it_answered_from_is_cited(self, wired) -> None:
        model = ScriptedModel(says("The Starter plan is 29 per seat per month."))
        result = self.take(wired, model, "how much is it per seat?")
        assert [source["name"] for source in result.sources] == ["pricing.md"]

    def test_a_document_it_did_not_use_is_not_cited(self, wired) -> None:
        model = ScriptedModel(says("The Starter plan is 29 per seat per month."))
        result = self.take(wired, model, "how much is it per seat?")
        assert "security.md" not in [source["name"] for source in result.sources]

    def test_the_model_is_handed_the_tools_the_agent_has(self, wired) -> None:
        model = ScriptedModel(says("Hello."))
        self.take(wired, model, "hello")
        assert {tool.name for tool in model.asked[0]["tools"]} == {
            "search_knowledge",
            "save_visitor_details",
            "suggest_replies",
        }

    def test_the_model_and_effort_come_from_the_definition(self, wired) -> None:
        model = ScriptedModel(says("Hello."))
        self.take(wired, model, "hello")
        assert model.asked[0]["model"] == "claude-opus-5-5"
        assert model.asked[0]["effort"] == "medium"

    def test_details_the_visitor_shares_are_stored_through_the_real_adapter(
        self, wired
    ) -> None:
        agent, services, context = wired
        model = ScriptedModel(
            ChatReply(
                content=(
                    TextBlock(text="Thanks Dan!"),
                    ToolUseBlock(
                        id="u1",
                        name="save_visitor_details",
                        arguments={"full_name": "Dan", "email": "dan@acme.io"},
                    ),
                ),
                model="claude-opus-5-5",
            ),
            says("What would you like to know?"),
        )
        self.take(wired, model, "I'm Dan, dan@acme.io")
        stored = run(services.visitors.load(ConversationRef("w1", "c1")))
        assert stored is not None
        assert stored.details == {"full_name": "Dan", "email": "dan@acme.io"}
        assert stored.status == "qualified"

    def test_the_second_turn_knows_what_the_first_one_learned(self, wired) -> None:
        # The prompt is rebuilt each turn, so the agent stops asking for an
        # email it was given — the complaint visitors actually make.
        agent, services, context = wired
        run(
            services.visitors.save(
                ConversationRef("w1", "c1"),
                VisitorRecord(details={"full_name": "Dan"}, status="qualifying", missing=("email",)),
            )
        )
        model = ScriptedModel(says("Of course."))
        self.take(wired, model, "tell me about security")
        system = model.asked[0]["system"]
        assert "do not ask again" in system
        assert "Who am I speaking with? Dan" in system

    def test_nothing_in_the_workspace_next_door_is_visible(self, wired) -> None:
        agent, services, context = wired
        services.passages.add_document(
            workspace_id="other-workspace",
            source_id="secret.md",
            source_name="secret.md",
            text="The Starter plan is 29 per seat per month for our competitor.",
        )
        model = ScriptedModel(says("The Starter plan is 29 per seat per month."))
        result = self.take(wired, model, "how much is it per seat?")
        assert "secret.md" not in [source["name"] for source in result.sources]

    def test_a_model_outage_is_not_shown_to_the_visitor(self, wired) -> None:
        class Broken:
            async def chat(self, **_: Any) -> ChatReply:
                raise RuntimeError("503 overloaded: api key sk-ant-xyz")

        result = self.take(wired, Broken(), "how much is it?")  # type: ignore[arg-type]
        assert result.ending is Ending.UNAVAILABLE
        assert "sk-ant" not in result.reply


class TestTheBookingFlowThroughTheRealCalendar:
    def test_a_slot_can_be_offered_and_booked_once(self) -> None:
        calendar = InMemoryCalendar(time_zone="Europe/London")
        start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
        availability = run(
            calendar.availability(
                scope=CalendarScope(workspace_id="w1"),
                start=start,
                end=start + timedelta(days=5),
                minutes=30,
            )
        )
        assert availability.slots
        assert availability.time_zone == "Europe/London"

        from celeris_bridge.ports.calendar import BookingRequest, SlotTaken

        slot = availability.slots[0]
        request = BookingRequest(
            host_id=slot.host_ids[0],
            start=slot.start,
            end=slot.end,
            visitor_name="Dan",
            visitor_email="dan@acme.io",
        )
        booking = run(calendar.book(scope=CalendarScope(workspace_id="w1"), request=request))
        assert booking.time_zone == "Europe/London"

        # The calendar is the authority on availability, not the agent.
        with pytest.raises(SlotTaken):
            run(calendar.book(scope=CalendarScope(workspace_id="w1"), request=request))

    def test_a_booked_slot_stops_being_offered(self) -> None:
        calendar = InMemoryCalendar()
        start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
        window = {"start": start, "end": start + timedelta(days=5), "minutes": 30}
        before = run(calendar.availability(scope=CalendarScope(workspace_id="w1"), **window))

        from celeris_bridge.ports.calendar import BookingRequest

        slot = before.slots[0]
        run(
            calendar.book(
                scope=CalendarScope(workspace_id="w1"),
                request=BookingRequest(
                    host_id=slot.host_ids[0],
                    start=slot.start,
                    end=slot.end,
                    visitor_name="Dan",
                    visitor_email="dan@acme.io",
                ),
            )
        )
        after = run(calendar.availability(scope=CalendarScope(workspace_id="w1"), **window))
        assert len(after.slots) == len(before.slots) - 1


class TestTheDevelopmentIndex:
    def test_a_document_becomes_passages(self) -> None:
        index = InMemoryPassageIndex()
        assert index.add_document(
            workspace_id="w1", source_id="s1", source_name="pricing.md", text=PRICING
        ) >= 1

    def test_a_search_with_no_workspace_returns_nothing(self) -> None:
        # The port requires failing closed, and the real adapter has a database
        # filter to get wrong. This one has one line, for the same reason.
        index = InMemoryPassageIndex()
        index.add_document(workspace_id="w1", source_id="s1", source_name="x.md", text=PRICING)
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id=""),
                query_vector=HashingEmbeddings().embed("per seat"),
                limit=5,
                min_score=0.0,
            )
        )
        assert hits == ()

    def test_a_document_bound_to_one_agent_is_invisible_to_another(self) -> None:
        index = InMemoryPassageIndex()
        index.add_document(
            workspace_id="w1",
            source_id="s1",
            source_name="x.md",
            text=PRICING,
            agent_instance_ids=("agent-one",),
        )
        vector = HashingEmbeddings().embed("per seat")
        mine = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1", agent_instance_id="agent-one"),
                query_vector=vector,
                limit=5,
                min_score=0.0,
            )
        )
        theirs = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1", agent_instance_id="agent-two"),
                query_vector=vector,
                limit=5,
                min_score=0.0,
            )
        )
        assert mine and theirs == ()

    def test_passages_from_another_embedder_are_left_out(self) -> None:
        index = InMemoryPassageIndex(HashingEmbeddings(dimensions=256))
        index.add_document(
            workspace_id="w1", source_id="s1", source_name="x.md", text=PRICING
        )
        # Same text, a different vector space. Searching one with the other's
        # vector must find nothing rather than rank nonsense.
        hits = run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=HashingEmbeddings(dimensions=256).embed("per seat"),
                limit=5,
                min_score=0.0,
                model="voyage-3:1024",
            )
        )
        assert hits == ()
        assert run(
            index.search(
                scope=KnowledgeScope(workspace_id="w1"),
                query_vector=HashingEmbeddings(dimensions=256).embed("per seat"),
                limit=5,
                min_score=0.0,
                model="hashing-dev:256",
            )
        )

    def test_retrieval_tells_the_index_which_embedder_it_used(self) -> None:
        from celeris_bridge.domain.knowledge.retrieval import retrieve

        services = build_services(options())
        services.passages.add_document(
            workspace_id="w1", source_id="s1", source_name="x.md", text=PRICING
        )
        found = run(
            retrieve(
                query="starter plan per seat",
                scope=KnowledgeScope(workspace_id="w1", agent_instance_id="a1"),
                embeddings=services.embeddings,
                index=services.passages,
                limit=3,
                min_score=0.01,
                passage_characters=500,
            )
        )
        # It found something, which it only can if the model it passed matches
        # the one the index recorded.
        assert found.passages
        assert found.embedding_model == services.embeddings.identifier

    def test_the_development_embedder_is_deterministic(self) -> None:
        # Which is the point: the retrieval path can be asserted on. It is also
        # why it must never face a customer — it cannot match a synonym.
        embedder = HashingEmbeddings()
        assert embedder.embed("per seat pricing") == embedder.embed("per seat pricing")

    def test_it_says_what_it_is(self) -> None:
        # Visible in telemetry, so a deployment accidentally running on it is
        # noticeable rather than mysterious.
        assert "dev" in HashingEmbeddings().identifier

    def test_the_clock_tells_the_time_in_utc(self) -> None:
        assert SystemClock().now().tzinfo is timezone.utc

    def test_visitor_records_are_per_conversation(self) -> None:
        records = InMemoryVisitorRecords()
        run(records.save(ConversationRef("w1", "c1"), VisitorRecord(details={"a": "1"})))
        assert run(records.load(ConversationRef("w1", "c2"))) is None
        assert run(records.load(ConversationRef("w2", "c1"))) is None
        loaded = run(records.load(ConversationRef("w1", "c1")))
        assert loaded is not None and loaded.details == {"a": "1"}


class TestTheWholeApplication:
    """What ``build_application`` produces, which is what the API layer gets."""

    def test_every_service_is_built(self) -> None:
        from celeris_bridge.composition.build import build_application

        app = build_application(options())
        assert app.templates and app.instances and app.conversations
        assert app.knowledge and app.leads
        assert app.registry

    def test_the_definition_cache_is_shared(self) -> None:
        # A published version is immutable, so two services parsing the same
        # one twice is pure waste.
        from celeris_bridge.composition.build import build_application

        app = build_application(options())
        assert app.instances._cache is app.cache

    def test_the_knowledge_service_has_a_fetcher_so_a_crawl_is_possible(self) -> None:
        from celeris_bridge.composition.build import build_application

        app = build_application(options())
        assert app.knowledge._fetcher is not None

    def test_the_index_the_abilities_read_is_the_one_knowledge_writes(self) -> None:
        # Two different indexes would mean an upload that is never found, with
        # nothing anywhere reporting a problem.
        from celeris_bridge.composition.build import build_application

        app = build_application(options())
        assert app.knowledge._passages is app.services.passages
        assert app.knowledge._embeddings is app.services.embeddings
