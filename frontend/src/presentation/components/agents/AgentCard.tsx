/**
 * One agent in the list.
 *
 * It links to the agent's own page by id. That page arrives in chunk three;
 * the link exists now because the id in the URL is what every later screen is
 * addressed by.
 */

import { Link } from "react-router-dom";

import type { Agent } from "@/data/types/agent";

import { AgentActions } from "./AgentActions";
import { AgentStatusBadge } from "./AgentStatusBadge";

function when(value: string | null): string {
  if (!value) return "never";
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function AgentCard({ agent }: { agent: Agent }) {
  return (
    <article className="flex items-start justify-between gap-4 rounded-xl border border-line bg-surface px-5 py-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-3">
          <Link
            to={"/agents/" + agent.id}
            className="truncate text-sm font-semibold text-ink hover:text-brand"
          >
            {agent.label}
          </Link>
          <AgentStatusBadge agent={agent} />
        </div>
        <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-ink-soft">
          <div className="flex gap-1">
            <dt>Type</dt>
            <dd className="text-ink">
              {agent.templateKey}
              {/* The pinned template version, which does not move on its own:
                  the platform publishing a newer one is an upgrade this agent
                  has to accept. */}
              <span className="text-ink-soft">
                {" · pinned v"}
                {agent.draftTemplateVersion}
              </span>
            </dd>
          </div>
          <div className="flex gap-1">
            <dt>Published</dt>
            <dd className="text-ink">{when(agent.lastPublishedAt)}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Origins</dt>
            <dd className="text-ink">
              {agent.deployment.allowedOrigins.length || "none set"}
            </dd>
          </div>
        </dl>
      </div>
      <AgentActions agent={agent} />
    </article>
  );
}
