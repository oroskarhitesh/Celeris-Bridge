/**
 * Why a turn stopped, said in words.
 *
 * The backend records five endings and three of them are bugs that look
 * identical from the reply text alone: ``rounds`` means the agent kept
 * calling abilities until it ran out, ``empty`` means it said nothing and the
 * visitor got fallback wording, ``unavailable`` means the model could not be
 * reached -- an outage that reached a customer. ``answered`` and
 * ``decorated`` both went fine and are not worth a line on the screen.
 *
 * One map, two screens: the draft preview and the stored transcript show the
 * same three sentences, because they are the same three facts. Two copies of
 * this prose would be two copies that have to agree.
 */

export const WORRYING_ENDINGS: Record<string, string> = {
  rounds: "It used up its ability calls before finishing this answer.",
  empty: "It returned nothing. The reply above is the fallback wording.",
  unavailable: "The model could not be reached. This is the fallback wording.",
};

/** The sentence for an ending, or nothing when the turn was fine. */
export function endingNote(ending: string | undefined): string | undefined {
  return ending ? WORRYING_ENDINGS[ending] : undefined;
}
