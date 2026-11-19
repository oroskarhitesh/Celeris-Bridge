/**
 * The list, and the states a list has.
 *
 * It is given its data rather than fetching it: the page owns one query, this
 * owns the markup, and a test can render it with a fixed array.
 */

import type { ApiError } from "@/data/client/errors";
import type { Agent } from "@/data/types/agent";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { Spinner } from "@/presentation/components/ui/Spinner";

import { AgentCard } from "./AgentCard";

interface AgentListProps {
  agents: Agent[] | undefined;
  loading: boolean;
  error: ApiError | undefined;
}

export function AgentList({ agents, loading, error }: AgentListProps) {
  if (loading) return <Spinner label="Loading agents" />;
  if (error) return <ErrorNotice error={error} />;
  return (
    <div className="space-y-3">
      {(agents ?? []).map((agent) => (
        <AgentCard key={agent.id} agent={agent} />
      ))}
    </div>
  );
}
