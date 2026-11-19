"""
Talk to the agent from a terminal.

The walking skeleton: a real definition, the real prompt, the real abilities,
the real model. No database, no HTTP, no React. Conversations live for as long
as the script does.

    python scripts/chat.py                     talk to it
    python scripts/chat.py --prompt            print the system prompt and stop
    python scripts/chat.py --say "how much?"   one message, then stop

Needs ``ANTHROPIC_API_KEY`` in the environment, or a signed-in profile.
Commands during a conversation: ``/prompt``, ``/visitor``, ``/calls``, ``/quit``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

if __package__ is None:  # running the file directly
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from celeris_bridge.composition.build import (
    Agent,
    UnpublishableDefinition,
    build_agent,
    build_chat_model,
    build_services,
    build_stores,
)
from celeris_bridge.composition.settings import Settings, settings
from celeris_bridge.domain.agent.engine.instructions import build_prompt
from celeris_bridge.domain.knowledge.ingestion import ingest
from celeris_bridge.domain.agent.engine.runner import TurnContext
from celeris_bridge.domain.agent.engine.turn import TurnResult, run_turn
from celeris_bridge.ports.chat import ChatMessage
from celeris_bridge.ports.visitor import ConversationRef, VisitorRecord

WORKSPACE = "dev-workspace"
INSTANCE = "dev-instance"
CONVERSATION = "dev-conversation"

#: Enough for the agent to have something to find. It goes in through the
#: real ingestion pipeline, so this script exercises the same path an upload
#: does -- extraction, chunking, one batched embedding call, then the index.
SEED_KNOWLEDGE = [
    (
        "pricing.md",
        """
        Pricing. Celeris Bridge is sold per seat. The Starter plan is 29 per seat
        per month and includes the shared inbox, web chat and email. The Growth
        plan is 49 per seat per month and adds the voice add-on, routing rules and
        the reporting dashboard. Annual billing saves two months on either plan.
        There is no setup fee. A 14 day trial is available on both plans and does
        not require a card.
        """,
    ),
    (
        "security.md",
        """
        Security and data handling. Celeris Bridge is SOC 2 Type II certified and
        audited annually. All data is encrypted in transit with TLS 1.3 and at
        rest with AES-256. Customer data is held in the region the workspace
        chooses: the EU, the UK or the US. Conversation transcripts are retained
        for 24 months by default and the retention period is configurable down to
        30 days. Single sign-on through SAML and SCIM provisioning are included on
        the Growth plan.
        """,
    ),
    (
        "integrations.md",
        """
        Integrations. Celeris Bridge connects to Slack, Microsoft Teams, Zendesk,
        Salesforce and HubSpot. Calendar booking works with Google Calendar and
        Microsoft 365. There is a REST API and outbound webhooks on every plan.
        A Zapier app covers everything else.
        """,
    ),
]


async def seed(services, stores) -> int:
    """Put the sample knowledge in, through the real ingestion pipeline.

    Through ``ingest`` rather than the index's own shortcut, because the
    shortcut embeds with the development embedder whatever is configured --
    so with CB_EMBEDDINGS=voyage the corpus and the queries would be in two
    different vector spaces and this script would find nothing, with the
    index's model filter correctly refusing to compare them.
    """
    passages = 0
    for name, text in SEED_KNOWLEDGE:
        result = await ingest(
            workspace_id=WORKSPACE,
            source_id=name.replace(".", "_"),
            name=name,
            content=" ".join(text.split()).encode("utf-8"),
            content_type="text/markdown",
            documents=stores.documents,
            embeddings=services.embeddings,
            index=services.passages,
        )
        passages += result.passages
    return passages


def prompt_for(agent: Agent, options: Settings, visitor: VisitorRecord | None):
    return build_prompt(
        definition=agent.definition,
        settings=agent.config.settings,
        locked_rules=agent.config.locked_rules,
        company=options.company,
        abilities=agent.briefs,
        visitor=visitor,
    )


def report(result: TurnResult) -> None:
    calls = ", ".join(
        f"{call.key}{'' if call.ok else ' (' + str(call.error) + ')'}"
        for call in result.calls
    ) or "none"
    print(f"\n  agent  {result.reply}\n")
    if result.sources:
        print(f"  sources  {', '.join(str(s['name']) for s in result.sources)}")
    if result.quick_replies:
        print(f"  buttons  {' | '.join(result.quick_replies)}")
    print(
        f"  [{result.ending} · {result.rounds} round(s) · tools: {calls} · "
        f"{result.usage.input_tokens} in / {result.usage.output_tokens} out · "
        f"{result.model}]\n"
    )


async def converse(options: Settings, one_message: str | None, show_prompt: bool) -> int:
    try:
        agent = build_agent(options=options)
    except UnpublishableDefinition as refused:
        print(refused, file=sys.stderr)
        return 1

    services = build_services(options)
    stores = build_stores(options)
    chat = build_chat_model(options)
    reference = ConversationRef(workspace_id=WORKSPACE, conversation_id=CONVERSATION)
    context = TurnContext(
        workspace_id=WORKSPACE,
        agent_instance_id=INSTANCE,
        conversation_id=CONVERSATION,
        services=services,
    )
    passages = await seed(services, stores)

    if show_prompt:
        built = prompt_for(agent, options, VisitorRecord())
        print(built.text)
        print(
            f"\n--- {len(built.text)} characters · blocks used: "
            f"{', '.join(built.used)} · skipped: {', '.join(built.skipped) or 'none'} ---"
        )
        return 0

    print(f"\n{agent.definition.identity.name} agent · {options.company}")
    print(
        f"  model {agent.config.settings.get('chatModel')} · effort "
        f"{agent.config.settings.get('replyEffort')} · "
        f"{len(agent.config.enabled_abilities)} abilities · {passages} passages indexed"
    )
    if agent.config.missing_abilities:
        print(f"  missing abilities: {', '.join(agent.config.missing_abilities)}")
    print("  /prompt  /visitor  /calls  /quit\n")

    greeting = agent.config.settings.get("greeting")
    if greeting and not one_message:
        print(f"  agent  {greeting}\n")

    history: list[ChatMessage] = []
    last: TurnResult | None = None

    while True:
        if one_message is not None:
            message = one_message
            print(f"  you    {message}")
        else:
            try:
                message = input("  you    ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

        if not message:
            continue
        if message in ("/quit", "/exit"):
            return 0
        if message == "/prompt":
            visitor = await services.visitors.load(reference)
            print("\n" + prompt_for(agent, options, visitor or VisitorRecord()).text + "\n")
            continue
        if message == "/visitor":
            visitor = await services.visitors.load(reference)
            print(f"\n  {visitor or 'nothing known yet'}\n")
            continue
        if message == "/calls":
            print(f"\n  {last.calls if last else 'no turns yet'}\n")
            continue

        # Rebuilt every turn: what the agent knows about the visitor changes as
        # the conversation goes on, and a prompt built once would keep asking
        # for details it has already been given.
        visitor = await services.visitors.load(reference)
        built = prompt_for(agent, options, visitor or VisitorRecord())

        try:
            last = await run_turn(
                config=agent.config,
                registry=agent.registry,
                chat=chat,
                system=built.text,
                company=options.company,
                message=message,
                context=context,
                history=history,
            )
        except Exception as failure:  # noqa: BLE001 - a script, and it reports
            print(f"\n  ! {type(failure).__name__}: {failure}\n", file=sys.stderr)
            return 1

        report(last)
        history.append(ChatMessage(role="user", content=message))
        history.append(ChatMessage(role="assistant", content=last.reply))

        if one_message is not None:
            return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Talk to a celeris_bridge agent.")
    parser.add_argument("--definition", help="which definition to serve")
    parser.add_argument("--company", help="the company name the agent uses")
    parser.add_argument("--model", help="override the definition's model")
    parser.add_argument("--say", help="send one message and stop")
    parser.add_argument(
        "--prompt", action="store_true", help="print the system prompt and stop"
    )
    parser.add_argument("--debug", action="store_true", help="log everything")
    arguments = parser.parse_args()

    # So a failure the visitor is protected from is still visible to whoever
    # is running this. A missing API key and a provider outage produce the same
    # reply; only the log tells them apart.
    logging.basicConfig(
        level=logging.DEBUG if arguments.debug else logging.WARNING,
        format="  | %(levelname)s %(name)s: %(message)s",
    )

    options = settings()
    if arguments.definition:
        options.definition = arguments.definition
    if arguments.company:
        options.company = arguments.company
    if arguments.model:
        options.chat_model = arguments.model

    return asyncio.run(converse(options, arguments.say, arguments.prompt))


if __name__ == "__main__":
    raise SystemExit(main())
