/**
 * How far a conversation got, as it was judged at the time.
 *
 * Not a progress bar, for two separate reasons. ``not_a_fit`` is a finished
 * state and a perfectly good outcome -- the backend's own line about it is
 * the one worth keeping, "not someone to sell to, still someone to help" --
 * and none of these four is recomputed: the verdict was recorded when the
 * visitor said the thing that produced it, so this badge is history and the
 * screen never offers to re-run the rules.
 *
 * A status this build has not heard of is shown as it arrived. The backend
 * owns that vocabulary and a new word in it is not a reason to print nothing.
 */

const LOOKS: Record<string, string> = {
  qualified: "bg-good/10 text-good border-good/30",
  qualifying: "bg-warn/10 text-warn border-warn/40",
  not_a_fit: "bg-canvas text-ink-soft border-line",
  exploring: "bg-canvas text-ink-soft border-line",
};

const WORDS: Record<string, string> = {
  qualified: "Qualified",
  qualifying: "Qualifying",
  not_a_fit: "Not a fit",
  exploring: "Exploring",
};

const WHY: Record<string, string> = {
  qualified: "Everything the workspace asks for is in hand.",
  qualifying: "Something was shared and something required is still missing.",
  not_a_fit: "Not someone to sell to. Still someone to help.",
  exploring: "Nothing has been shared yet.",
};

export function LeadStatusBadge({ status }: { status: string }) {
  return (
    <span
      {...(WHY[status] ? { title: WHY[status] } : {})}
      className={`whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${
        LOOKS[status] ?? "bg-canvas text-ink-soft border-line"
      }`}
    >
      {WORDS[status] ?? status}
    </span>
  );
}
