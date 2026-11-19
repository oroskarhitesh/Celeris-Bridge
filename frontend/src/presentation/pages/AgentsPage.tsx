/**
 * The workspace's agents.
 *
 * One query, filtered by the URL, and three states. The filter is read from
 * the search parameters rather than from state so that the query argument and
 * the address bar cannot disagree -- and because RTK Query keys its cache by
 * that argument, each filter keeps its own entry and switching back is
 * instant.
 */

import { Link, useSearchParams } from "react-router-dom";

import { useAgentsQuery } from "@/data/api/agentsApi";
import type { AgentStatus } from "@/data/types/agent";
import { AgentFilters } from "@/presentation/components/agents/AgentFilters";
import { AgentList } from "@/presentation/components/agents/AgentList";
import { Button } from "@/presentation/components/ui/Button";
import { EmptyState } from "@/presentation/components/ui/EmptyState";

const STATUSES: AgentStatus[] = ["draft", "live", "paused"];

export function AgentsPage() {
  const [params] = useSearchParams();
  const asked = params.get("status");
  const status = STATUSES.find((one) => one === asked);

  const { data, error, isLoading } = useAgentsQuery(status ? { status } : undefined);
  const nothing = !isLoading && !error && !data?.length;

  return (
    <div className="mx-auto max-w-4xl space-y-5">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold text-ink">Agents</h1>
          <p className="text-sm text-ink-soft">
            Everything this workspace has built, live or not.
          </p>
        </div>
        <Link to="/agents/new">
          <Button look="primary">New agent</Button>
        </Link>
      </header>

      <AgentFilters />

      {nothing ? (
        <EmptyState
          title={status ? "Nothing with that status" : "No agents yet"}
          description={
            status
              ? "Clear the filter to see the rest."
              : "An agent starts as a draft. It answers nobody until it is published."
          }
          action={
            status ? null : (
              <Link to="/agents/new">
                <Button look="primary">Create the first one</Button>
              </Link>
            )
          }
        />
      ) : (
        <AgentList agents={data} loading={isLoading} error={error} />
      )}
    </div>
  );
}
