/**
 * One turn in the preview transcript.
 *
 * The agent's turns carry what a visitor would also get -- its citations and
 * its suggested replies -- plus one thing a visitor never sees: why the turn
 * stopped. ``rounds`` means it ran out of ability calls, ``empty`` means it
 * said nothing, ``unavailable`` means the model could not be reached. Each is
 * a different problem and each looks identical from the text alone, which is
 * exactly what an admin testing a draft needs told.
 */

import type { Citation } from "@/data/types/settings";
import { endingNote } from "@/presentation/components/conversations/endings";

export interface PreviewTurn {
  id: string;
  from: "admin" | "agent";
  text: string;
  ending?: string;
  sources?: Citation[];
  quickReplies?: string[];
}


export function PreviewMessage({
  turn,
  onQuickReply,
}: {
  turn: PreviewTurn;
  onQuickReply?: (text: string) => void;
}) {
  const mine = turn.from === "admin";
  // The same three sentences the stored transcript shows: they are the same
  // three facts, and two copies of the prose would be two to keep in step.
  const note = endingNote(turn.ending);

  return (
    <li className={`flex flex-col gap-1 ${mine ? "items-end" : "items-start"}`}>
      <div
        className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm ${
          mine ? "bg-brand text-white" : "border border-line bg-surface text-ink"
        }`}
      >
        {turn.text}
      </div>

      {turn.sources?.length ? (
        <p className="max-w-[85%] text-xs text-ink-soft">
          Sources:{" "}
          {turn.sources.map((one, at) => (
            <span key={`${one.name}-${at}`}>
              {at > 0 ? ", " : ""}
              {one.url ? (
                <a
                  href={one.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-brand hover:underline"
                >
                  {one.name || one.url}
                </a>
              ) : (
                one.name
              )}
            </span>
          ))}
        </p>
      ) : null}

      {turn.quickReplies?.length ? (
        <div className="flex max-w-[85%] flex-wrap gap-1.5">
          {turn.quickReplies.map((reply) => (
            <button
              key={reply}
              type="button"
              onClick={() => onQuickReply?.(reply)}
              className="rounded-full border border-line bg-surface px-2.5 py-1 text-xs text-ink-soft transition hover:border-brand hover:text-ink"
            >
              {reply}
            </button>
          ))}
        </div>
      ) : null}

      {note ? <p className="max-w-[85%] text-xs text-warn">{note}</p> : null}
    </li>
  );
}
