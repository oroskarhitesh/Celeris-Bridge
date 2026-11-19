/**
 * Which abilities ran in one turn, and how that went.
 *
 * The single most useful thing to have when an agent said something odd, and
 * the reason the service flattens these into plain data before storing them.
 * Three fields earn their place on the screen:
 *
 * **``ok`` and ``error``.** A failed ability does not fail the turn -- the
 * model is told and carries on -- so a reply can look fine and be an answer
 * given without the calendar. The error code is the backend's own, shown as
 * it arrived: it is a code an engineer greps for, not a sentence.
 *
 * **``milliseconds``.** A reply that took nine seconds took them somewhere,
 * and this is where.
 *
 * **``replayed``.** The result came from this turn's own cache rather than
 * from a second call, which is why a transcript can show two calls to the
 * same ability and one slow one.
 */

import type { AbilityCall } from "@/data/types/conversation";

export function TurnCalls({ calls }: { calls: AbilityCall[] }) {
  if (!calls.length) return null;

  return (
    <ul className="space-y-1">
      {calls.map((call, at) => (
        <li
          key={`${call.key}-${at}`}
          className="flex flex-wrap items-center gap-x-2 text-xs"
        >
          <code className={call.ok ? "text-ink" : "text-bad"}>{call.key}</code>
          {call.ok ? null : (
            <span className="text-bad">
              failed{call.error ? ` — ${call.error}` : ""}
            </span>
          )}
          <span className="text-ink-soft">{call.milliseconds} ms</span>
          {call.replayed ? (
            <span
              title="The result came from this turn's own cache, not a second call."
              className="text-ink-soft"
            >
              · replayed
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
