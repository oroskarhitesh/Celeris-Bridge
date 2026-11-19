/**
 * One conversation, in full.
 *
 * Two queries against two routers, side by side on purpose: the transcript is
 * what was said, the lead record is what came of it, and the pair is how
 * somebody decides whether the agent asked the right questions. Each is its
 * own cache entry keyed by the same conversation id.
 *
 * The header says which versions were live when it started. That is the whole
 * reason the backend stores them: a transcript read against today's settings
 * is a transcript read against settings that may have changed twice since,
 * and "it does not say that any more" is not an explanation of what it said.
 *
 * Nothing on this screen can change anything. The admin API has no write for
 * a conversation and should not: a transcript is a record of model calls that
 * were billed, and an editable one would be a record that can be made to
 * disagree with them.
 */

import { Link, useParams } from "react-router-dom";

import { useAgentQuery } from "@/data/api/agentsApi";
import { useTranscriptQuery } from "@/data/api/conversationsApi";
import { useLeadQuery } from "@/data/api/leadsApi";
import { channelLabel } from "@/presentation/components/conversations/channels";
import { TranscriptTurn } from "@/presentation/components/conversations/TranscriptTurn";
import { LeadPanel } from "@/presentation/components/leads/LeadPanel";
import { Card } from "@/presentation/components/ui/Card";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { Spinner } from "@/presentation/components/ui/Spinner";
import { moment, tokens } from "@/presentation/format";

export function TranscriptPage() {
  const { id = "" } = useParams<{ id: string }>();

  const { data, error, isLoading } = useTranscriptQuery(id, { skip: !id });
  const lead = useLeadQuery(id, { skip: !id });
  // For the agent's name. Skipped until the transcript says which agent, and
  // a failure is ignored: the id is shown instead, which is still a fact.
  const agent = useAgentQuery(data?.instanceId ?? "", { skip: !data?.instanceId });

  if (isLoading) return <Spinner label="Loading transcript" />;
  if (error) return <ErrorNotice error={error} />;
  if (!data) return null;

  const turns = data.turnsDetail;

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <header className="space-y-1">
        <Link to="/conversations" className="text-xs text-ink-soft hover:text-ink">
          ← Conversations
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-lg font-semibold text-ink">
            {data.pageTitle || "Untitled page"}
          </h1>
          {data.preview ? (
            <span
              title="Had against a draft from the admin screen, not by a visitor."
              className="rounded-full border border-line bg-canvas px-2 py-0.5 text-xs font-medium text-ink-soft"
            >
              Preview
            </span>
          ) : null}
        </div>
        {data.pageUrl ? (
          <p className="truncate text-sm">
            <a
              href={data.pageUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="text-brand hover:underline"
            >
              {data.pageUrl}
            </a>
          </p>
        ) : null}
      </header>

      <dl className="grid grid-cols-2 gap-4 rounded-xl border border-line bg-surface px-5 py-4 text-sm sm:grid-cols-4">
        <div>
          <dt className="text-xs text-ink-soft">Agent</dt>
          <dd className="text-ink">{agent.data?.label ?? data.instanceId}</dd>
        </div>
        <div>
          <dt className="text-xs text-ink-soft">Channel</dt>
          <dd className="text-ink">{channelLabel(data.channel)}</dd>
        </div>
        <div>
          <dt className="text-xs text-ink-soft">Started</dt>
          <dd className="text-ink">{moment(data.startedAt)}</dd>
        </div>
        <div>
          <dt className="text-xs text-ink-soft">Tokens</dt>
          <dd className="text-ink">
            {tokens(data.inputTokens)} in · {tokens(data.outputTokens)} out
          </dd>
        </div>
        <div className="col-span-2 sm:col-span-4">
          <dt className="text-xs text-ink-soft">Configuration in force</dt>
          <dd className="text-ink">
            {/* An instance version of null is what makes a preview a preview:
                it was had against the draft, so there is no published
                snapshot to point at. */}
            {data.templateVersion === null
              ? "unknown"
              : `template v${data.templateVersion}`}
            {data.instanceVersion === null
              ? " · the draft at the time"
              : ` · agent v${data.instanceVersion}`}
          </dd>
        </div>
      </dl>

      <Card
        title="Transcript"
        description={`${turns.length} ${turns.length === 1 ? "turn" : "turns"}, with what each one cost and which abilities ran.`}
      >
        {turns.length ? (
          <ol className="space-y-4">
            {turns.map((turn, at) => (
              <TranscriptTurn key={`${turn.speaker}-${at}`} turn={turn} />
            ))}
          </ol>
        ) : (
          <p className="text-sm text-ink-soft">
            This conversation was opened and nothing was said in it.
          </p>
        )}
      </Card>

      {lead.data ? <LeadPanel lead={lead.data} /> : null}
    </div>
  );
}
