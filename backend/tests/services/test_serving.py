"""
Tests for serving a conversation, and for the two read models over it.

Everything real except the model: the shipped definition, the real registry,
the real stores, the real ingestion pipeline, the real turn loop. The model is
scripted, because a test that spends money on every run is a test people stop
running.

What these pin down is the behaviour around the turn rather than inside it --
what is stored and when, what the model is and is not shown, and what happens
at the edges: an empty message, a conversation that has run out of turns, a
widget on a domain the agent is not deployed to.
"""

from __future__ import annotations

import asyncio
from typing import Any, Sequence

import pytest

from celeris_bridge.adapters.in_memory import (
    HashingEmbeddings,
    InMemoryCalendar,
    InMemoryDocumentStore,
    InMemoryPassageIndex,
    InMemoryVisitorRecords,
    SystemClock,
)
from celeris_bridge.adapters.in_memory_stores import (
    InMemoryConversationStore,
    InMemoryInstanceStore,
    InMemoryTemplateStore,
)
from celeris_bridge.composition.build import Services
from celeris_bridge.definitions import load_definition
from celeris_bridge.domain.agent.abilities.builtins import builtin_abilities
from celeris_bridge.domain.agent.engine.turn import Ending
from celeris_bridge.domain.conversation import Channel, Speaker
from celeris_bridge.domain.agent.records import Deployment
from celeris_bridge.ports.chat import ChatMessage, ChatReply, TextBlock, ToolSpec, Usage
from celeris_bridge.services.conversations import (
    ConversationService,
    ConversationTooLong,
    OriginNotAllowed,
)
from celeris_bridge.services.instances import InstanceService
from celeris_bridge.services.knowledge import KnowledgeService, SourceNotFound
from celeris_bridge.services.leads import LeadsService
from celeris_bridge.services.resolution import DefinitionCache, resolve_live
from celeris_bridge.services.templates import TemplateService

KEY = "marketing_v1"
PRICING = (
    "Our Starter plan is 29 per seat per month, billed annually. The Growth "
    "plan is 49 per seat and adds single sign-on. Every plan includes a 14 day "
    "trial and no card is required to start one."
)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class ScriptedModel:
    def __init__(self, *replies: ChatReply) -> None:
        self.replies = list(replies)
        self.asked: list[dict[str, Any]] = []
        self.fail = False

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
        if self.fail:
            raise RuntimeError("the provider is down")
        self.asked.append({"system": system, "messages": list(messages), "tools": list(tools)})
        return (
            self.replies.pop(0)
            if self.replies
            else ChatReply(
                content=(TextBlock(text="Anything else?"),),
                model="claude-opus-5-5",
                usage=Usage(input_tokens=100, output_tokens=20),
            )
        )


def says(text: str) -> ChatReply:
    return ChatReply(
        content=(TextBlock(text=text),),
        model="claude-opus-5-5",
        usage=Usage(input_tokens=200, output_tokens=40),
    )


@pytest.fixture
def world():
    registry = builtin_abilities()
    templates = InMemoryTemplateStore()
    instances = InMemoryInstanceStore()
    conversations = InMemoryConversationStore()
    visitors = InMemoryVisitorRecords()
    embeddings = HashingEmbeddings()
    passages = InMemoryPassageIndex(embeddings)
    documents = InMemoryDocumentStore()
    model = ScriptedModel()

    services = Services(
        embeddings=embeddings,
        passages=passages,
        visitors=visitors,
        calendar=InMemoryCalendar(),
        clock=SystemClock(),
    )

    types = TemplateService(templates=templates, registry=registry)
    template = run(types.create(key=KEY, draft=load_definition(KEY)))
    run(types.publish(KEY, expect_revision=template.draft_revision))

    agents = InstanceService(
        instances=instances,
        templates=templates,
        registry=registry,
        cache=DefinitionCache(),
    )
    instance = run(agents.create(workspace_id="w1", template_key=KEY, label="Website"))
    run(agents.publish("w1", instance.id, expect_revision=instance.draft_revision))

    chats = ConversationService(
        conversations=conversations, chat=model, visitors=visitors
    )
    knowledge = KnowledgeService(
        documents=documents, passages=passages, embeddings=embeddings
    )
    run(
        knowledge.upload(
            workspace_id="w1",
            source_id="pricing",
            name="pricing.md",
            content=PRICING.encode(),
            content_type="text/markdown",
        )
    )

    return {
        "registry": registry,
        "templates": templates,
        "instances": instances,
        "conversations": conversations,
        "visitors": visitors,
        "documents": documents,
        "services": services,
        "model": model,
        "agents": agents,
        "chats": chats,
        "knowledge": knowledge,
        "leads": LeadsService(conversations=conversations, visitors=visitors),
        "instance_id": instance.id,
    }


def agent(world: dict[str, Any]):
    return run(
        resolve_live(
            workspace_id="w1",
            instance_id=world["instance_id"],
            templates=world["templates"],
            instances=world["instances"],
            registry=world["registry"],
        )
    )


def open_chat(world: dict[str, Any], **changed: Any):
    return run(world["chats"].start(agent=agent(world), **changed))


def say(world: dict[str, Any], conversation_id: str, message: str, **changed: Any):
    return run(
        world["chats"].say(
            agent=agent(world),
            conversation_id=conversation_id,
            message=message,
            services=world["services"],
            company="Acme",
            **changed,
        )
    )


class TestOneMessage:
    def test_a_question_gets_an_answer(self, world) -> None:
        world["model"].replies.append(says("The Starter plan is 29 per seat per month."))
        conversation = open_chat(world)
        reply = say(world, conversation.id, "how much per seat?")
        assert reply.text == "The Starter plan is 29 per seat per month."
        assert reply.ending == str(Ending.ANSWERED)

    def test_it_answers_from_the_uploaded_document(self, world) -> None:
        world["model"].replies.append(says("The Starter plan is 29 per seat per month."))
        conversation = open_chat(world)
        reply = say(world, conversation.id, "how much per seat?")
        assert [source["name"] for source in reply.sources] == ["pricing.md"]

    def test_both_turns_are_stored(self, world) -> None:
        world["model"].replies.append(says("29 per seat."))
        conversation = open_chat(world)
        say(world, conversation.id, "how much?")
        stored = run(world["chats"].transcript("w1", conversation.id))
        assert [turn.speaker for turn in stored.turns] == [Speaker.VISITOR, Speaker.AGENT]
        assert stored.turns[0].text == "how much?"

    def test_what_a_turn_cost_is_stored(self, world) -> None:
        world["model"].replies.append(says("29 per seat."))
        conversation = open_chat(world)
        say(world, conversation.id, "how much?")
        stored = run(world["chats"].transcript("w1", conversation.id))
        assert stored.tokens == (200, 40)
        assert stored.turns[1].model == "claude-opus-5-5"

    def test_which_abilities_ran_is_stored(self, world) -> None:
        # The most useful thing to have when an agent said something odd, and
        # the easiest to forget to store.
        world["model"].replies.append(says("29 per seat."))
        conversation = open_chat(world)
        say(world, conversation.id, "how much?")
        stored = run(world["chats"].transcript("w1", conversation.id))
        assert [call["key"] for call in stored.turns[1].calls] == ["search_knowledge"]
        assert stored.turns[1].calls[0]["ok"] is True

    def test_the_conversation_records_the_versions_it_started_under(self, world) -> None:
        # A transcript read in six months should be read against the
        # configuration that produced it, not against today's.
        conversation = open_chat(world)
        assert conversation.template_version == 1
        assert conversation.instance_version == 1

    def test_where_the_visitor_was_is_recorded(self, world) -> None:
        conversation = open_chat(
            world, page_url="https://acme.example/pricing", page_title="Pricing"
        )
        assert conversation.page_url == "https://acme.example/pricing"


class TestWhatTheModelIsShown:
    def test_the_prompt_comes_from_the_resolved_settings(self, world) -> None:
        conversation = open_chat(world)
        say(world, conversation.id, "hello")
        assert "Acme" in world["model"].asked[0]["system"]

    def test_an_edited_draft_does_not_reach_the_live_prompt(self, world) -> None:
        # The separation the whole four-level hierarchy exists for, seen from
        # the place it matters.
        instance = run(world["agents"].get("w1", world["instance_id"]))
        run(
            world["agents"].edit(
                "w1",
                world["instance_id"],
                {"settings": {"agentName": "Draft Name"}},
                expect_revision=instance.draft_revision,
            )
        )
        conversation = open_chat(world)
        say(world, conversation.id, "hello")
        assert "Draft Name" not in world["model"].asked[0]["system"]

    def test_the_earlier_turns_are_sent_back(self, world) -> None:
        conversation = open_chat(world)
        say(world, conversation.id, "first question")
        say(world, conversation.id, "and a second")
        messages = world["model"].asked[1]["messages"]
        assert [one.content for one in messages][:2] == ["first question", "Anything else?"]

    def test_but_only_the_tail_of_a_long_one(self, world) -> None:
        # A long chat costs on every turn and its oldest exchanges rarely
        # matter. Asserted on the history rather than on everything sent,
        # because the tool-use blocks of the turn in progress are also in
        # there and belong there.
        world["chats"]._history_turns = 2
        conversation = open_chat(world)
        for number in range(4):
            say(world, conversation.id, f"question {number}")
        stored = run(world["chats"].transcript("w1", conversation.id))
        history = world["chats"]._history(stored)
        assert len(history) == 2
        assert not any("question 0" in one.content for one in history)

    def test_the_ability_calls_of_past_turns_are_not_sent_back(self, world) -> None:
        # The reason a stored turn and a chat message are two types: a
        # transcript carries the calls, the citations and what the turn cost,
        # and none of that goes back to the model on a later message.
        conversation = open_chat(world)
        say(world, conversation.id, "how much?")
        stored = run(world["chats"].transcript("w1", conversation.id))
        assert stored.turns[1].calls  # it is in the transcript
        history = world["chats"]._history(stored)
        assert all(isinstance(one.content, str) for one in history)
        assert not any("search_knowledge" in one.content for one in history)

    def test_the_model_is_handed_the_abilities_the_agent_has(self, world) -> None:
        conversation = open_chat(world)
        say(world, conversation.id, "hello")
        assert {tool.name for tool in world["model"].asked[0]["tools"]} == {
            "search_knowledge",
            "save_visitor_details",
            "suggest_replies",
        }


class TestTheEdges:
    def test_an_empty_message_is_refused_before_anything_is_spent(self, world) -> None:
        conversation = open_chat(world)
        with pytest.raises(ValueError):
            say(world, conversation.id, "   ")
        assert world["model"].asked == []

    def test_a_question_is_stored_even_when_the_model_cannot_be_reached(
        self, world
    ) -> None:
        # Storing it before the call is what makes this true, and a transcript
        # that drops the question reads as though they never asked.
        world["model"].fail = True
        conversation = open_chat(world)
        reply = say(world, conversation.id, "how much per seat?")
        stored = run(world["chats"].transcript("w1", conversation.id))
        assert stored.turns[0].text == "how much per seat?"
        assert reply.ending == str(Ending.UNAVAILABLE)

    def test_an_outage_that_reached_a_visitor_is_recorded_as_one(self, world) -> None:
        # Worth being able to count, which means it has to be in the record
        # rather than only in a log line.
        world["model"].fail = True
        conversation = open_chat(world)
        say(world, conversation.id, "hello")
        stored = run(world["chats"].transcript("w1", conversation.id))
        assert stored.turns[1].ending == str(Ending.UNAVAILABLE)

    def test_the_turn_limit_is_enforced_before_the_model_is_called(self, world) -> None:
        # A setting that asks the model to stop after N turns is a suggestion.
        instance = run(world["agents"].get("w1", world["instance_id"]))
        run(
            world["agents"].edit(
                "w1", world["instance_id"], {"settings": {"maxTurns": 4}},
                expect_revision=instance.draft_revision,
            )
        )
        current = run(world["agents"].get("w1", world["instance_id"]))
        run(world["agents"].publish("w1", world["instance_id"], expect_revision=current.draft_revision))

        conversation = open_chat(world)
        for word in ("one", "two", "three", "four"):
            say(world, conversation.id, word)
        calls_before = len(world["model"].asked)
        with pytest.raises(ConversationTooLong) as caught:
            say(world, conversation.id, "five")
        assert caught.value.limit == 4
        assert len(world["model"].asked) == calls_before

    def test_the_last_message_is_still_kept_when_the_limit_bites(self, world) -> None:
        instance = run(world["agents"].get("w1", world["instance_id"]))
        run(
            world["agents"].edit(
                "w1", world["instance_id"], {"settings": {"maxTurns": 4}},
                expect_revision=instance.draft_revision,
            )
        )
        current = run(world["agents"].get("w1", world["instance_id"]))
        run(world["agents"].publish("w1", world["instance_id"], expect_revision=current.draft_revision))

        conversation = open_chat(world)
        for word in ("one", "two", "three", "four"):
            say(world, conversation.id, word)
        with pytest.raises(ConversationTooLong):
            say(world, conversation.id, "five")
        stored = run(world["chats"].transcript("w1", conversation.id))
        assert stored.turns[-1].text == "five"
        assert stored.turns[-1].ending == "too_long"

    def test_the_reply_says_when_the_conversation_is_finished(self, world) -> None:
        # So the widget stops offering an input box rather than letting
        # someone type into a conversation that will refuse them.
        instance = run(world["agents"].get("w1", world["instance_id"]))
        run(
            world["agents"].edit(
                "w1", world["instance_id"], {"settings": {"maxTurns": 4}},
                expect_revision=instance.draft_revision,
            )
        )
        current = run(world["agents"].get("w1", world["instance_id"]))
        run(world["agents"].publish("w1", world["instance_id"], expect_revision=current.draft_revision))

        conversation = open_chat(world)
        assert say(world, conversation.id, "one").open is True
        assert say(world, conversation.id, "two").open is True
        assert say(world, conversation.id, "three").open is True
        assert say(world, conversation.id, "four").open is False

    def test_a_conversation_from_another_workspace_is_not_found(self, world) -> None:
        conversation = open_chat(world)
        with pytest.raises(LookupError):
            run(world["chats"].transcript("w2", conversation.id))


class TestWhereTheWidgetMayRun:
    def test_nothing_configured_allows_anywhere(self, world) -> None:
        # A workspace that has not set up its domains should still be able to
        # try the agent; the list is a deployment control, not authentication.
        assert open_chat(world, origin="https://anywhere.example")

    def test_a_configured_list_is_enforced(self, world) -> None:
        instance = run(world["agents"].get("w1", world["instance_id"]))
        run(
            world["agents"].set_deployment(
                "w1",
                world["instance_id"],
                Deployment(allowed_origins=("https://acme.example",)),
                expect_revision=instance.draft_revision,
            )
        )
        assert open_chat(world, origin="https://acme.example")
        with pytest.raises(OriginNotAllowed):
            open_chat(world, origin="https://evil.example")

    def test_and_on_every_message_not_only_on_the_first(self, world) -> None:
        # Otherwise taking a domain off the list leaves open conversations
        # running on it.
        conversation = open_chat(world)
        instance = run(world["agents"].get("w1", world["instance_id"]))
        run(
            world["agents"].set_deployment(
                "w1",
                world["instance_id"],
                Deployment(allowed_origins=("https://acme.example",)),
                expect_revision=instance.draft_revision,
            )
        )
        with pytest.raises(OriginNotAllowed):
            say(world, conversation.id, "still here?", origin="https://evil.example")


class TestLeads:
    def details(self, world) -> Any:
        """A conversation in which the visitor shared their details."""
        from celeris_bridge.ports.chat import ToolUseBlock

        world["model"].replies.append(
            ChatReply(
                content=(
                    ToolUseBlock(
                        id="t1",
                        name="save_visitor_details",
                        arguments={"full_name": "Priya Raman", "email": "priya@acme.io"},
                    ),
                ),
                model="claude-opus-5-5",
                usage=Usage(input_tokens=100, output_tokens=10),
            )
        )
        world["model"].replies.append(says("Thanks Priya, I will pass that on."))
        conversation = open_chat(world, page_url="https://acme.example/pricing")
        say(world, conversation.id, "I am Priya Raman, priya@acme.io, we need 20 seats")
        return conversation

    def test_a_conversation_with_details_is_a_lead(self, world) -> None:
        conversation = self.details(world)
        leads = run(world["leads"].list("w1", with_details_only=True))
        assert [one.conversation_id for one in leads] == [conversation.id]
        assert leads[0].details["email"] == "priya@acme.io"

    def test_the_opening_message_is_carried(self, world) -> None:
        # The single most useful column: why they got in touch, in their words.
        self.details(world)
        lead = run(world["leads"].list("w1", with_details_only=True))[0]
        assert lead.opening_message.startswith("I am Priya Raman")

    def test_the_verdict_is_the_one_that_was_recorded(self, world) -> None:
        # Not recomputed: a qualification rule changed last week must not
        # silently rewrite what a lead was called on Tuesday.
        self.details(world)
        lead = run(world["leads"].list("w1", with_details_only=True))[0]
        stored = run(world["visitors"].load(_ref(world, lead)))
        assert lead.status == stored.status
        assert lead.visitor_type == stored.visitor_type

    def test_a_passer_by_is_not_a_lead_but_is_still_counted(self, world) -> None:
        # "Forty conversations, three leads" is the number a workspace needs.
        conversation = open_chat(world)
        say(world, conversation.id, "just looking, thanks")
        assert run(world["leads"].list("w1", with_details_only=True)) == ()
        assert len(run(world["leads"].list("w1"))) == 1

    def test_the_page_they_were_on_is_carried(self, world) -> None:
        self.details(world)
        lead = run(world["leads"].list("w1", with_details_only=True))[0]
        assert lead.page_url == "https://acme.example/pricing"

    def test_one_workspace_sees_only_its_own(self, world) -> None:
        self.details(world)
        assert run(world["leads"].list("w2")) == ()

    def test_a_lead_can_be_fetched_on_its_own(self, world) -> None:
        conversation = self.details(world)
        lead = run(world["leads"].get("w1", conversation.id))
        assert lead.conversation_id == conversation.id

    def test_and_not_from_another_workspace(self, world) -> None:
        conversation = self.details(world)
        with pytest.raises(LookupError):
            run(world["leads"].get("w2", conversation.id))


def _ref(world: dict[str, Any], lead: Any):
    from celeris_bridge.ports.visitor import ConversationRef

    return ConversationRef("w1", lead.conversation_id)


class TestKnowledgeAdmin:
    def test_the_list_says_what_is_indexed(self, world) -> None:
        sources = run(world["knowledge"].sources("w1"))
        assert [one.name for one in sources] == ["pricing.md"]
        assert sources[0].status == "indexed"
        assert sources[0].passages >= 1

    def test_a_document_with_no_text_reads_as_empty_not_pending(self, world) -> None:
        # Processed, and there was nothing in it. Different from not yet done,
        # and the difference is what an admin needs to act.
        run(
            world["knowledge"].upload(
                workspace_id="w1", source_id="blank", name="blank.txt", content=b"   "
            )
        )
        found = run(world["knowledge"].source("w1", "blank"))
        assert found.status == "empty"

    def test_the_original_can_be_downloaded(self, world) -> None:
        # What makes a file that would not parse diagnosable.
        assert run(world["knowledge"].original("w1", "pricing")) == PRICING.encode()

    def test_deleting_a_source_removes_its_passages(self, world) -> None:
        removed = run(world["knowledge"].delete("w1", "pricing"))
        assert removed >= 1
        assert run(world["knowledge"].sources("w1")) == ()

    def test_another_workspace_cannot_see_a_source(self, world) -> None:
        assert run(world["knowledge"].sources("w2")) == ()
        with pytest.raises(SourceNotFound):
            run(world["knowledge"].source("w2", "pricing"))

    def test_or_download_it(self, world) -> None:
        with pytest.raises(SourceNotFound):
            run(world["knowledge"].original("w2", "pricing"))

    def test_or_delete_it(self, world) -> None:
        with pytest.raises(SourceNotFound):
            run(world["knowledge"].delete("w2", "pricing"))
        assert run(world["knowledge"].sources("w1"))

    def test_a_crawl_needs_a_fetcher(self, world) -> None:
        # Rather than silently doing nothing, which is how a workspace ends up
        # believing its website is indexed.
        with pytest.raises(RuntimeError):
            run(
                world["knowledge"].crawl(
                    workspace_id="w1", start_url="https://acme.example/"
                )
            )
