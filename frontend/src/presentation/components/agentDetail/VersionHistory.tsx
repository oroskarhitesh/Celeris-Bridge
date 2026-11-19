/**
 * Every version this agent has had, and the way back to one.
 *
 * Newest first, as the API sends it. The live one is marked rather than
 * inferred from position, because after a rollback the newest version is the
 * live one and the version it *copied* is still sitting further down the
 * list: a reader needs both facts to make sense of the history.
 *
 * **A rollback is a publish.** It does not edit or delete anything -- it
 * makes a new version carrying the old overrides and records where they came
 * from. That is why this list grows when someone goes back, and why the
 * button says "publish" rather than "restore": nothing is lost, and the thing
 * that happens is a new live version.
 *
 * It takes two clicks, through the same confirmation as a delete, because it
 * changes what visitors are being served right now.
 */

import { useRollbackAgentMutation } from "@/data/api/agentVersionsApi";
import { codeOf } from "@/data/client/errors";
import type { ApiError } from "@/data/client/errors";
import type { Agent } from "@/data/types/agent";
import type { AgentVersion } from "@/data/types/settings";
import { Card } from "@/presentation/components/ui/Card";
import { ConfirmButton } from "@/presentation/components/ui/ConfirmButton";
import { EmptyState } from "@/presentation/components/ui/EmptyState";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { Spinner } from "@/presentation/components/ui/Spinner";
import { useToast } from "@/presentation/hooks/useToast";

interface VersionHistoryProps {
  agent: Agent;
  versions: AgentVersion[] | undefined;
  loading: boolean;
  error: ApiError | undefined;
}

function when(value: string | null): string {
  if (!value) return "";
  return new Date(value).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function VersionHistory({
  agent,
  versions,
  loading,
  error,
}: VersionHistoryProps) {
  const [rollback, rollingBack] = useRollbackAgentMutation();
  const { say, complain } = useToast();

  async function goBackTo(version: number) {
    const result = await rollback({
      id: agent.id,
      expectRevision: agent.draftRevision,
      toVersion: version,
    });
    if ("error" in result) {
      if (codeOf(result.error) === "REVISION_CONFLICT") {
        say("This agent changed somewhere else. Reloaded it — try again.");
        return;
      }
      complain(result.error);
      return;
    }
    say(
      `Version ${result.data.version.version} is live, carrying what version ${version} had.`,
    );
  }

  if (loading) return <Spinner label="Loading versions" />;
  if (error) return <ErrorNotice error={error} />;
  if (!versions?.length) {
    return (
      <EmptyState
        title="No versions yet"
        description="A version is made when the draft is published. Until then there is nothing for a visitor to be served."
      />
    );
  }

  return (
    <Card title="Versions" description="Newest first. Nothing here is ever edited.">
      <ol className="divide-y divide-line">
        {versions.map((version) => {
          const live = version.version === agent.publishedVersion;
          return (
            <li
              key={version.version}
              className="flex items-start justify-between gap-4 py-3 first:pt-0 last:pb-0"
            >
              <div className="min-w-0">
                <p className="flex flex-wrap items-center gap-2 text-sm text-ink">
                  <span className="font-semibold">Version {version.version}</span>
                  {live ? (
                    <span className="rounded-full border border-good/30 bg-good/10 px-2 py-0.5 text-xs font-medium text-good">
                      Live
                    </span>
                  ) : null}
                  <span className="text-xs text-ink-soft">
                    agent type v{version.templateVersion}
                  </span>
                </p>
                <p className="mt-0.5 text-sm text-ink-soft">
                  {version.changeSummary || "No summary was written."}
                </p>
                <p className="mt-0.5 text-xs text-ink-soft">
                  {when(version.publishedAt)}
                  {version.publishedBy ? ` · ${version.publishedBy}` : ""}
                  {version.rolledBackFrom !== null
                    ? ` · carried over from version ${version.rolledBackFrom}`
                    : ""}
                </p>
              </div>
              {live ? null : (
                <ConfirmButton
                  label="Go back to this"
                  confirmLabel={`Publish version ${version.version} again`}
                  busy={rollingBack.isLoading}
                  onConfirm={() => void goBackTo(version.version)}
                />
              )}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
