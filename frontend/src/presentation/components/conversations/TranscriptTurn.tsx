/**
 * One turn, as the people who own the agent may read it.
 *
 * The visitor's side is the text and the time, because that is all there is.
 * The agent's side carries everything the turn cost and did: the model that
 * answered, the tokens in and out, why it stopped, which abilities ran, and
 * the passages behind the answer.
 *
 * That detail sits under the reply rather than beside it, and the passages
 * are collapsed, because a transcript is read as a conversation first and as
 * a diagnosis second. Everything needed for the second reading is on the
 * screen; none of it is in the way of the first.
 */

import type { Turn } from "@/data/types/conversation";
import { clock, tokens } from "@/presentation/format";

import { endingNote } from "./endings";
import { TurnCalls } from "./TurnCalls";
import { TurnSources } from "./TurnSources";

export function TranscriptTurn({ turn }: { turn: Turn }) {
  const visitor = turn.speaker === "visitor";
  const note = endingNote(turn.ending);
  const spend = turn.inputTokens + turn.outputTokens;

  return (
    <li className={`flex flex-col gap-1 ${visitor ? "items-end" : "items-start"}`}>
      <div
        className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm ${
          visitor
            ? "bg-brand text-white"
            : "border border-line bg-surface text-ink"
        }`}
      >
        {turn.text}
      </div>

      <p className="flex flex-wrap items-center gap-x-2 text-xs text-ink-soft">
        <span>{visitor ? "Visitor" : "Agent"}</span>
        {turn.at ? <span>{clock(turn.at)}</span> : null}
        {turn.model ? <span>· {turn.model}</span> : null}
        {/* Only when there was a cost. A visitor's turn has none, and a zero
            against every line would make the ones that cost something harder
            to see rather than easier. */}
        {spend ? (
          <span>
            · {tokens(turn.inputTokens)} in · {tokens(turn.outputTokens)} out
          </span>
        ) : null}
      </p>

      {note ? <p className="max-w-[85%] text-xs text-warn">{note}</p> : null}

      {turn.calls.length ? (
        <div className="max-w-[85%]">
          <TurnCalls calls={turn.calls} />
        </div>
      ) : null}

      {turn.sources.length ? (
        <div className="max-w-[85%]">
          <TurnSources sources={turn.sources} />
        </div>
      ) : null}
    </li>
  );
}
