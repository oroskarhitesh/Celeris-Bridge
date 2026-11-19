/**
 * The agent's status, as a visitor would experience it.
 *
 * ``live`` and "unpublished edits" are two different facts and both matter:
 * the second one means what is on the screen is not what the public is being
 * served, which is the single most confusing thing about a system with drafts
 * if it is not said out loud.
 *
 * It is drawn from ``hasUnpublishedChanges``, which the API computes by
 * comparing the draft against the published snapshot -- the same comparison
 * that makes publishing an unchanged draft a ``NOTHING_TO_PUBLISH``. So an
 * edit that is undone stops showing this, and a just-published agent never
 * shows it: a warning that is sometimes wrong is one people learn to ignore.
 */

import type { Agent } from "@/data/types/agent";

const LOOKS: Record<Agent["status"], string> = {
  live: "bg-good/10 text-good border-good/30",
  draft: "bg-canvas text-ink-soft border-line",
  paused: "bg-warn/10 text-warn border-warn/40",
};

const WORDS: Record<Agent["status"], string> = {
  live: "Live",
  draft: "Draft",
  paused: "Paused",
};

export function AgentStatusBadge({ agent }: { agent: Agent }) {
  return (
    <span className="inline-flex items-center gap-2">
      <span
        className={`rounded-full border px-2 py-0.5 text-xs font-medium ${LOOKS[agent.status]}`}
      >
        {WORDS[agent.status]}
        {agent.status === "live" && agent.publishedVersion
          ? ` · v${agent.publishedVersion}`
          : null}
      </span>
      {agent.hasUnpublishedChanges && agent.publishedVersion !== null ? (
        <span
          className="text-xs text-ink-soft"
          title="Edited since it was last published. Visitors are still served the published version."
        >
          unpublished edits
        </span>
      ) : null}
      {agent.status !== "draft" && agent.publishedVersion === null ? (
        // A paused or live agent with no snapshot at all: saying "unpublished
        // edits" would be true but useless, because there is no published
        // version for them to be edits against.
        <span className="text-xs text-ink-soft">never published</span>
      ) : null}
      {agent.pausedReason ? (
        <span className="text-xs text-warn">{agent.pausedReason.replace(/_/g, " ")}</span>
      ) : null}
    </span>
  );
}
