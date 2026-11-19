/**
 * The list, and the states a list has.
 *
 * Given its data, like every other list here: the page owns the query, this
 * owns the markup. ``labels`` is the agent-id-to-label map the page built
 * from its own agents query -- passed as a plain record so this component
 * needs no second query and a test can hand it one entry.
 */

import type { ApiError } from "@/data/client/errors";
import type { ConversationSummary } from "@/data/types/conversation";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { Spinner } from "@/presentation/components/ui/Spinner";

import { ConversationRow } from "./ConversationRow";

interface ConversationListProps {
  conversations: ConversationSummary[] | undefined;
  labels: Record<string, string>;
  loading: boolean;
  error: ApiError | undefined;
}

export function ConversationList({
  conversations,
  labels,
  loading,
  error,
}: ConversationListProps) {
  if (loading) return <Spinner label="Loading conversations" />;
  if (error) return <ErrorNotice error={error} />;
  return (
    <div className="space-y-3">
      {(conversations ?? []).map((one) => (
        <ConversationRow
          key={one.id}
          conversation={one}
          {...(labels[one.instanceId] ? { agentLabel: labels[one.instanceId] } : {})}
        />
      ))}
    </div>
  );
}
