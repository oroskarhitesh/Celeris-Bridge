/**
 * The few ways this interface writes a number or a date.
 *
 * Shared rather than local to a component, unlike the one-line ``when()`` in
 * the agents list: these screens show the same timestamp in three places --
 * a row, a turn, a lead -- and a transcript whose times are formatted
 * differently from the list they were opened from reads as two systems.
 *
 * Everything here uses the viewer's own locale, with no timezone argument. A
 * conversation happened at a moment; which clock a person reads it on is
 * theirs, and the API sends UTC instants precisely so that this is a display
 * decision.
 */

/** A date, for a row read down a column. */
export function day(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** A date and a time, for one thing that happened once. */
export function moment(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Just the clock time, for a turn inside a conversation whose date is
 *  already on the screen above it. */
export function clock(value: string | null | undefined): string {
  if (!value) return "";
  return new Date(value).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * A token count, grouped.
 *
 * Tokens and not money, everywhere: this service holds no price list, and a
 * currency symbol in front of a number nobody here can derive would be an
 * invention dressed as an invoice. What it can say truthfully is how much was
 * sent and how much came back.
 */
export function tokens(count: number): string {
  return count.toLocaleString();
}
