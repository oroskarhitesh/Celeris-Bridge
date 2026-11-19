/**
 * One conversation in the list.
 *
 * What it shows is chosen to answer the question somebody opens this screen
 * with -- "is this thing working?" -- before they open anything:
 *
 * **Visitor turns, not total turns.** A conversation of two turns where the
 * visitor said one thing is a greeting and a question. The total counts the
 * agent's own replies and so flatters every abandoned chat.
 *
 * **The page they were on, as a link.** "They were on the pricing page" is
 * most of a qualification on its own, which is why the backend stores it.
 *
 * **Tokens, in and out.** What the conversation cost in the only unit this
 * service knows. See ``presentation/format``: no money is invented here.
 *
 * A preview is labelled rather than hidden. It was had against a draft from
 * the admin screen, so it is not traffic -- but a conversation filtered out
 * of a list is one somebody goes looking for, and the backend flags them for
 * exactly this reason.
 */

import { Link } from "react-router-dom";

import type { ConversationSummary } from "@/data/types/conversation";
import { moment, tokens } from "@/presentation/format";

import { channelLabel } from "./channels";

interface ConversationRowProps {
  conversation: ConversationSummary;
  /** The agent's label, when the page knows it. An id otherwise: a row that
   *  says nothing where the name goes is worse than one that says the id. */
  agentLabel?: string;
}

export function ConversationRow({ conversation, agentLabel }: ConversationRowProps) {
  const spoken = conversation.visitorTurns;

  return (
    <article className="flex items-start justify-between gap-4 rounded-xl border border-line bg-surface px-5 py-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-3">
          <Link
            to={"/conversations/" + conversation.id}
            className="truncate text-sm font-semibold text-ink hover:text-brand"
          >
            {conversation.pageTitle || "Untitled page"}
          </Link>
          {conversation.preview ? (
            <span
              title="Had against a draft from the admin screen, not by a visitor."
              className="rounded-full border border-line bg-canvas px-2 py-0.5 text-xs font-medium text-ink-soft"
            >
              Preview
            </span>
          ) : null}
          {spoken === 0 ? (
            <span
              title="The agent greeted somebody and nobody answered."
              className="rounded-full border border-warn/40 bg-warn/10 px-2 py-0.5 text-xs font-medium text-warn"
            >
              Nobody replied
            </span>
          ) : null}
        </div>

        <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-ink-soft">
          <div className="flex gap-1">
            <dt>Agent</dt>
            <dd className="text-ink">{agentLabel ?? conversation.instanceId}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Channel</dt>
            <dd className="text-ink">{channelLabel(conversation.channel)}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Visitor turns</dt>
            <dd className="text-ink">{spoken}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Tokens</dt>
            <dd className="text-ink">
              {tokens(conversation.inputTokens)} in · {tokens(conversation.outputTokens)}{" "}
              out
            </dd>
          </div>
          <div className="flex gap-1">
            <dt>Started</dt>
            <dd className="text-ink">{moment(conversation.startedAt)}</dd>
          </div>
        </dl>

        {conversation.pageUrl ? (
          <p className="mt-1 truncate text-xs text-ink-soft">
            <a
              href={conversation.pageUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="text-brand hover:underline"
            >
              {conversation.pageUrl}
            </a>
          </p>
        ) : null}
      </div>

      <Link
        to={"/conversations/" + conversation.id}
        className="shrink-0 rounded-lg border border-line px-3 py-1.5 text-sm font-medium text-ink-soft transition hover:border-brand hover:text-ink"
      >
        Transcript
      </Link>
    </article>
  );
}
