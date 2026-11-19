/**
 * One source: what it is, whether it answers anything, and the two things
 * that can be done to it.
 *
 * **The original downloads through the data layer, not through a link.** The
 * endpoint needs the bearer token, and a plain anchor sends none, so the
 * bytes are fetched and handed to the browser as a temporary object URL. That
 * URL is revoked immediately afterwards: it is a handle to memory, and one
 * left behind per click is a leak that only shows up on a long afternoon.
 *
 * **Deleting is two clicks and says what goes.** Unlike deleting an agent,
 * this really does remove things -- the document and every passage retrieved
 * from it -- so the confirmation says the number it is about to remove.
 */

import {
  useDeleteSourceMutation,
  useOriginalOfMutation,
} from "@/data/api/knowledgeApi";
import type { ApiError } from "@/data/client/errors";
import type { Source } from "@/data/types/knowledge";
import { Button } from "@/presentation/components/ui/Button";
import { ConfirmButton } from "@/presentation/components/ui/ConfirmButton";
import { useToast } from "@/presentation/hooks/useToast";

import { SourceStatusBadge } from "./SourceStatusBadge";

/** Bytes, for a person. A source list is read down a column, so the unit
 *  matters more than the precision. */
function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Hand a blob to the browser as a download, then let go of it. */
function save(blob: Blob, name: string): void {
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(href);
}

export function SourceRow({ source }: { source: Source }) {
  const [remove, removing] = useDeleteSourceMutation();
  const [original, downloading] = useOriginalOfMutation();
  const { say, complain, warn } = useToast();

  return (
    <article className="flex items-start justify-between gap-4 rounded-xl border border-line bg-surface px-5 py-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="truncate text-sm font-semibold text-ink">{source.name}</h3>
          <SourceStatusBadge status={source.status} />
          {source.approved ? (
            <span
              title="An official answer. It outranks ordinary passages at retrieval."
              className="rounded-full border border-brand/30 bg-brand-soft px-2 py-0.5 text-xs font-medium text-brand"
            >
              Approved
            </span>
          ) : null}
        </div>

        <dl className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-ink-soft">
          <div className="flex gap-1">
            <dt>Passages</dt>
            <dd className="text-ink">{source.passages}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Size</dt>
            <dd className="text-ink">{size(source.bytesStored)}</dd>
          </div>
          <div className="flex gap-1">
            <dt>Type</dt>
            <dd className="text-ink">{source.kind || "unknown"}</dd>
          </div>
          {source.embeddingModel ? (
            <div className="flex gap-1">
              <dt>Embedder</dt>
              {/* Worth a column: a source on an older embedder is what
                  reindexing is for, and this is the only way to see one. */}
              <dd className="text-ink">{source.embeddingModel}</dd>
            </div>
          ) : null}
        </dl>

        {source.sourceUrl ? (
          <p className="mt-1 truncate text-xs text-ink-soft">
            Crawled from{" "}
            <a
              href={source.sourceUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="text-brand hover:underline"
            >
              {source.sourceUrl}
            </a>
          </p>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <Button
          look="ghost"
          busy={downloading.isLoading}
          title="The bytes as they were uploaded, for working out what the extractor was given"
          onClick={async () => {
            const result = await original(source.sourceId);
            if ("data" in result && result.data) {
              save(result.data, source.name);
              return;
            }
            // Our own words, and necessarily: the response to a failed
            // download is bytes, so there is no envelope to quote.
            warn(`Could not download ${source.name}.`);
          }}
        >
          Original
        </Button>
        <ConfirmButton
          label="Delete"
          confirmLabel={
            source.passages
              ? `Delete and drop ${source.passages} passages`
              : "Delete for good"
          }
          busy={removing.isLoading}
          onConfirm={async () => {
            const { error, data } = await remove(source.sourceId);
            if (error) {
              complain(error as ApiError);
              return;
            }
            say(
              data?.passagesRemoved
                ? `${source.name} is gone, with ${data.passagesRemoved} passages.`
                : `${source.name} is gone.`,
            );
          }}
        />
      </div>
    </article>
  );
}
