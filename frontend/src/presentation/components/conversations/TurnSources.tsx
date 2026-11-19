/**
 * The passages a reply drew on, with the text.
 *
 * This is the one thing the admin transcript has that a visitor's reply never
 * does. A citation sent to a visitor is a name and a link; what is stored is
 * the passage itself, its retrieval score and whether it was an approved
 * answer. With the text, "why did it say that" is a question somebody can
 * answer by reading -- without it, a citation is a claim about a document.
 *
 * Collapsed by default. A turn can carry several hundred words of passage and
 * a transcript is read for the conversation first.
 */

import type { TurnSource } from "@/data/types/conversation";

export function TurnSources({ sources }: { sources: TurnSource[] }) {
  if (!sources.length) return null;

  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-ink-soft hover:text-ink">
        {sources.length} {sources.length === 1 ? "passage" : "passages"} behind this
        answer
      </summary>
      <ul className="mt-2 space-y-2">
        {sources.map((source, at) => (
          <li
            key={`${source.sourceId ?? source.name ?? "source"}-${at}`}
            className="rounded-lg border border-line bg-canvas px-3 py-2"
          >
            <p className="flex flex-wrap items-center gap-x-2">
              {source.url ? (
                <a
                  href={source.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="font-medium text-brand hover:underline"
                >
                  {source.name || source.url}
                </a>
              ) : (
                <span className="font-medium text-ink">{source.name || "Unnamed"}</span>
              )}
              {source.approved ? (
                <span
                  title="An approved answer. It outranks ordinary passages at retrieval."
                  className="rounded-full border border-brand/30 bg-brand-soft px-1.5 text-xs text-brand"
                >
                  Approved
                </span>
              ) : null}
              {typeof source.score === "number" ? (
                <span
                  title="How closely this passage matched the question it was retrieved for."
                  className="text-ink-soft"
                >
                  score {source.score}
                </span>
              ) : null}
            </p>
            {source.text ? (
              <p className="mt-1 whitespace-pre-wrap text-ink-soft">{source.text}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </details>
  );
}
