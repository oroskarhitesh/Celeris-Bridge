/**
 * Re-embed whatever is not on the current embedder.
 *
 * The recovery path for a change of embedding provider, and the reason the
 * originals are kept at all. It is not a "refresh" button: a document already
 * on the current model is skipped, so running this twice costs nothing the
 * second time.
 *
 * ``limit`` is offered rather than hidden because the honest description of
 * reindexing a thousand documents is "a bill and a very long request". A
 * batch of fifty is a decision someone should be able to make.
 */

import { useState } from "react";

import { useReindexMutation } from "@/data/api/knowledgeApi";
import type { ApiError } from "@/data/client/errors";
import type { Ingested } from "@/data/types/knowledge";
import { Button } from "@/presentation/components/ui/Button";
import { Card } from "@/presentation/components/ui/Card";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { TextInput } from "@/presentation/components/ui/TextInput";
import { useToast } from "@/presentation/hooks/useToast";

/** The API's own ceiling for one batch. */
const MOST = 500;

export function ReindexCard() {
  const [reindex, running] = useReindexMutation();
  const { say } = useToast();
  const [limit, setLimit] = useState("50");
  const [result, setResult] = useState<Ingested[] | null>(null);

  const asked = Number(limit);
  const bad = limit !== "" && (!Number.isInteger(asked) || asked < 0 || asked > MOST);

  async function run(): Promise<void> {
    if (bad) return;
    const { data, error } = await reindex(limit === "" ? {} : { limit: asked });
    if (error || !data) return;
    setResult(data);
    say(
      data.length
        ? `Re-embedded ${data.length} ${data.length === 1 ? "document" : "documents"}.`
        : "Nothing needed re-embedding.",
    );
  }

  const indexed = (result ?? []).filter((one) => one.indexed);
  const passages = indexed.reduce((total, one) => total + one.passages, 0);

  return (
    <Card
      title="Reindex"
      description="Re-embeds only what is not on the current embedding model. A document already on it is left alone."
    >
      <div className="space-y-4">
        <div className="max-w-xs">
          <TextInput
            id="reindex-limit"
            label="How many, at most"
            type="number"
            min={0}
            max={MOST}
            value={limit}
            problem={bad ? `A whole number from 0 to ${MOST}.` : undefined}
            hint="0, or empty, means everything that needs it."
            onChange={(event) => setLimit(event.target.value)}
          />
        </div>

        {running.error ? <ErrorNotice error={running.error as ApiError} /> : null}

        {result ? (
          result.length ? (
            <div className="rounded-lg border border-line bg-canvas px-4 py-3 text-sm">
              <p className="font-medium text-ink">
                {`${indexed.length} of ${result.length} re-embedded, ${passages} passages.`}
              </p>
              <ul className="mt-2 space-y-1 text-xs text-ink-soft">
                {result.map((one) => (
                  <li key={one.sourceId}>
                    <span className="text-ink">{one.sourceName}</span>
                    {one.indexed
                      ? ` — ${one.passages} passages on ${one.model}`
                      : ` — ${one.skipped || "nothing indexed"}`}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm text-ink-soft">
              Everything is already on the current embedding model.
            </p>
          )
        ) : null}

        <Button look="secondary" busy={running.isLoading} disabled={bad} onClick={run}>
          Reindex
        </Button>
      </div>
    </Card>
  );
}
