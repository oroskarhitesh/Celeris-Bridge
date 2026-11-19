"""
Where the wires meet.

The only place that both knows which adapters exist and is allowed to choose
between them. Everything else takes what it needs through a port, which is why
nothing above this line has imported ``anthropic`` or ``motor``.

It is also where a definition becomes a runnable agent: validated against the
abilities this build actually has, resolved against a workspace's overrides,
and refused if it does not publish. A definition that fails here would fail on
a visitor instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from celeris_bridge.adapters.anthropic_chat import AnthropicChatModel
from celeris_bridge.adapters.embeddings import build_embeddings
from celeris_bridge.adapters.in_memory_stores import (
    InMemoryConversationStore,
    InMemoryInstanceStore,
    InMemoryTemplateStore,
)
from celeris_bridge.adapters.in_memory import (
    HashingEmbeddings,
    InMemoryCalendar,
    InMemoryDocumentStore,
    InMemoryPassageIndex,
    InMemoryVisitorRecords,
    SystemClock,
)
from celeris_bridge.composition.settings import Settings, settings
from celeris_bridge.definitions import load_definition
from celeris_bridge.domain.agent.abilities.builtins import builtin_abilities
from celeris_bridge.domain.agent.abilities.registry import AbilityRegistry
from celeris_bridge.domain.agent.definition_schema import AgentDefinition, validate_definition
from celeris_bridge.domain.agent.engine.instructions import AbilityBrief
from celeris_bridge.domain.agent.engine.runner import enabled_tools
from celeris_bridge.domain.knowledge.crawl import CrawlLimits
from celeris_bridge.domain.knowledge.robots import DEFAULT_USER_AGENT
from celeris_bridge.domain.agent.overrides import (
    Overrides,
    ResolvedConfig,
    apply_override_patch,
    resolve_config,
)
from celeris_bridge.adapters.local_documents import LocalDocumentStore
from celeris_bridge.ports.chat import ChatModel
from celeris_bridge.ports.documents import DocumentStore
from celeris_bridge.ports.web import WebFetcher
from celeris_bridge.services.conversations import ConversationService
from celeris_bridge.services.instances import InstanceService
from celeris_bridge.services.knowledge import KnowledgeService
from celeris_bridge.services.leads import LeadsService
from celeris_bridge.services.resolution import DefinitionCache
from celeris_bridge.services.templates import TemplateService
from celeris_bridge.ports.store import ConversationStore, InstanceStore, TemplateStore

__all__ = [
    "Agent",
    "Application",
    "Services",
    "Stores",
    "UnpublishableDefinition",
    "build_agent",
    "build_application",
    "build_crawl_limits",
    "build_documents",
    "build_fetcher",
    "build_embedder",
    "build_services",
    "build_stores",
]


logger = logging.getLogger(__name__)


def connect_mongo(options: Settings):
    """One place that opens a connection, so one place to change the options."""
    from celeris_bridge.adapters.mongo import connect

    return connect(options.require_mongo(), options.mongo_database)


class UnpublishableDefinition(RuntimeError):
    """A definition this build cannot serve, with every reason listed."""

    def __init__(self, key: str, problems: list[str]) -> None:
        self.key = key
        self.problems = problems
        listed = "".join(f"\n  - {problem}" for problem in problems)
        super().__init__(f'The definition "{key}" cannot be served:{listed}')


@dataclass(frozen=True)
class Services:
    """
    Every port, in one object.

    Each ability names the narrow slice it needs as its own Protocol, and this
    satisfies all of them structurally — so an ability depends on two ports
    rather than on this.
    """

    embeddings: Any
    passages: Any
    visitors: Any
    calendar: Any
    clock: Any


def build_services(options: Settings | None = None) -> Services:
    """The adapters this configuration asks for."""
    options = options or settings()

    if options.adapters == "mongo":
        from celeris_bridge.adapters.mongo import MongoPassageIndex, MongoVisitorRecords

        _, database = connect_mongo(options)
        return Services(
            embeddings=build_embedder(options),
            passages=MongoPassageIndex(database),
            visitors=MongoVisitorRecords(database),
            calendar=InMemoryCalendar(time_zone=options.time_zone),
            clock=SystemClock(),
        )

    embeddings = build_embedder(options)
    return Services(
        embeddings=embeddings,
        # The index is given the same embedder, not another instance of one:
        # two embedders mean two vector spaces and a corpus that matches
        # nothing.
        passages=InMemoryPassageIndex(embeddings)
        if isinstance(embeddings, HashingEmbeddings)
        else InMemoryPassageIndex(),
        visitors=InMemoryVisitorRecords(),
        calendar=InMemoryCalendar(time_zone=options.time_zone),
        clock=SystemClock(),
    )


@dataclass(frozen=True)
class Stores:
    """The records the platform keeps.

    Separate from :class:`Services` on purpose. Services are what an *ability*
    is handed; these are what an application service uses. Putting a template
    store on the ability context would let a booking handler rewrite an agent
    type, which is not a capability anything should have by accident.
    """

    templates: TemplateStore
    instances: InstanceStore
    conversations: ConversationStore
    #: The originals of everything in the knowledge index. Here rather than on
    #: :class:`Services` because an ability has no business reading a
    #: customer's uploaded files -- it reads the passages, which have been
    #: chunked, scoped and stripped of prompt injections on the way.
    documents: DocumentStore
    #: Only present for Mongo, and only so a start-up can create its indexes.
    database: Any = None


def build_stores(options: Settings | None = None) -> Stores:
    """The repositories this configuration asks for."""
    options = options or settings()

    if options.adapters == "mongo":
        # Imported here rather than at module scope: the memory build must not
        # need pymongo installed to start.
        from celeris_bridge.adapters.mongo import (
            MongoConversationStore,
            MongoInstanceStore,
            MongoTemplateStore,
            connect,
        )

        _, database = connect(options.require_mongo(), options.mongo_database)
        return Stores(
            templates=MongoTemplateStore(database),
            instances=MongoInstanceStore(database),
            conversations=MongoConversationStore(database),
            documents=build_documents(options),
            database=database,
        )

    return Stores(
        templates=InMemoryTemplateStore(),
        instances=InMemoryInstanceStore(),
        conversations=InMemoryConversationStore(),
        documents=build_documents(options),
    )


def build_documents(options: Settings | None = None) -> DocumentStore:
    """Where originals are kept.

    Disk when a root is configured, memory when none is -- including under
    Mongo, which is a real combination and not a mistake: the records belong in
    a database and a hundred megabytes of PDFs do not. It is warned about
    anyway, because losing the originals means a change of embedding model can
    only be fixed by asking every customer to upload their files again.
    """
    options = options or settings()
    if options.document_root:
        return LocalDocumentStore(options.document_root)
    if options.adapters == "mongo":
        logger.warning(
            "No CB_DOCUMENT_ROOT: uploaded documents are kept in memory and "
            "lost on restart, and the knowledge index cannot be rebuilt "
            "without them."
        )
    return InMemoryDocumentStore()


def build_embedder(options: Settings | None = None) -> Any:
    """The embedder this configuration asks for.

    Warns when a real database is paired with the development embedder: it
    cannot match a synonym, so retrieval will answer badly and the symptom
    ("the agent does not know things it was told") points nowhere near the
    cause.
    """
    options = options or settings()
    if options.adapters == "mongo" and options.embeddings == "development":
        logger.warning(
            "Mongo with the development embedder: retrieval is word overlap "
            "only and cannot match a synonym. Set CB_EMBEDDINGS=voyage or "
            "openai before this serves anyone."
        )
    return build_embeddings(
        options.embeddings,
        model=options.embedding_model,
        dimensions=options.embedding_dimensions,
    )


def build_fetcher(options: Settings | None = None) -> WebFetcher:
    """The web fetcher, with its safety rules and its caps.

    Imported here rather than at module scope only for symmetry with the rest;
    there is one implementation and there is deliberately no in-memory
    alternative. A fake fetcher belongs in a test, not in a build: a
    development mode that quietly did not make real requests would hide
    exactly the failures this adapter exists to handle.
    """
    from celeris_bridge.adapters.http_fetch import HttpFetcher

    options = options or settings()
    return HttpFetcher(
        user_agent=options.user_agent or DEFAULT_USER_AGENT,
        max_bytes=options.crawl_max_bytes,
    )


def build_crawl_limits(options: Settings | None = None) -> CrawlLimits:
    """The caps one crawl runs under."""
    options = options or settings()
    return CrawlLimits(
        max_pages=options.crawl_max_pages,
        max_depth=options.crawl_max_depth,
        delay_seconds=options.crawl_delay_seconds,
        max_bytes=options.crawl_max_bytes,
        user_agent=options.user_agent or DEFAULT_USER_AGENT,
    )


def build_chat_model(options: Settings | None = None) -> ChatModel:
    """The model client. One for the process; the model is chosen per call."""
    options = options or settings()
    return AnthropicChatModel()


@dataclass(frozen=True)
class Application:
    """Every application service, built once for the process.

    One object because the API layer needs all of them and should not be
    assembling adapters itself -- composition is the only place allowed to
    know which adapters exist. The ability-facing :class:`Services` bundle is
    here too, because serving a turn needs it and nothing else does.

    The definition cache is shared deliberately: a published version is
    immutable, so two services parsing the same one twice is pure waste.
    """

    services: Services
    stores: Stores
    templates: TemplateService
    instances: InstanceService
    conversations: ConversationService
    knowledge: KnowledgeService
    leads: LeadsService
    registry: AbilityRegistry
    cache: DefinitionCache
    company: str = ""


def build_application(options: Settings | None = None) -> Application:
    """Wire everything up.

    Called once at start-up. Under a web server that has to happen inside the
    event loop that will serve requests, because the Mongo client binds to the
    loop it is first awaited on -- see ``adapters/mongo/client.py``.
    """
    options = options or settings()
    registry = builtin_abilities()
    cache = DefinitionCache()

    services = build_services(options)
    stores = build_stores(options)
    chat = build_chat_model(options)

    return Application(
        services=services,
        stores=stores,
        templates=TemplateService(templates=stores.templates, registry=registry),
        instances=InstanceService(
            instances=stores.instances,
            templates=stores.templates,
            registry=registry,
            cache=cache,
        ),
        conversations=ConversationService(
            conversations=stores.conversations,
            chat=chat,
            visitors=services.visitors,
        ),
        knowledge=KnowledgeService(
            documents=stores.documents,
            passages=services.passages,
            embeddings=services.embeddings,
            fetcher=build_fetcher(options),
            limits=build_crawl_limits(options),
        ),
        leads=LeadsService(
            conversations=stores.conversations, visitors=services.visitors
        ),
        registry=registry,
        cache=cache,
        company=options.company,
    )


@dataclass(frozen=True)
class Agent:
    """A definition, published and resolved, ready to take a turn."""

    key: str
    definition: AgentDefinition
    config: ResolvedConfig
    registry: AbilityRegistry
    overrides: Overrides

    @property
    def briefs(self) -> tuple[AbilityBrief, ...]:
        """Each enabled ability's instructions, for the prompt."""
        return tuple(
            AbilityBrief(name=tool.name, instructions=tool.instructions)
            for tool in enabled_tools(self.config, self.registry)
        )


def build_agent(
    key: str | None = None,
    *,
    patch: Mapping[str, Any] | None = None,
    options: Settings | None = None,
    registry: AbilityRegistry | None = None,
) -> Agent:
    """
    Load a definition, check it, and resolve it with a workspace's overrides.

    ``patch`` stands in for what an instance record will hold. It goes through
    ``apply_override_patch``, so a development override is refused for exactly
    the reasons a customer's would be — there is no back door that accepts a
    value the product would not.
    """
    options = options or settings()
    key = key or options.definition
    registry = registry if registry is not None else builtin_abilities()

    report = validate_definition(load_definition(key), abilities=registry)
    if not report.ok or report.definition is None:
        raise UnpublishableDefinition(
            key, [f"{problem.path}: {problem.message}" for problem in report.problems]
        )
    definition = report.definition

    overrides = apply_override_patch(definition, None, patch, abilities=registry)
    config = resolve_config(definition, overrides, abilities=registry)

    if options.chat_model:
        # An admin-only setting, so it cannot be overridden through the patch
        # above. Replacing it here is explicit, local, and obvious in a diff.
        config = ResolvedConfig(
            settings={**config.settings, "chatModel": options.chat_model},
            abilities=config.abilities,
            locked_rules=config.locked_rules,
            missing_abilities=config.missing_abilities,
        )

    return Agent(
        key=key,
        definition=definition,
        config=config,
        registry=registry,
        overrides=overrides,
    )
