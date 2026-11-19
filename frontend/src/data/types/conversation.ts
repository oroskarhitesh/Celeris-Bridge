/**
 * What happened, as the admin API describes it.
 *
 * A mirror of the views in ``api/routes/conversations.py``. The difference
 * between these types and the widget's is the whole reason that router is
 * separate: a visitor's reply carries a citation's name and link, and a
 * transcript carries the passage text, the retrieval score, which abilities
 * ran, how long each took and what the turn cost. None of that may be sent to
 * a visitor, and all of it is needed to answer "why did it say that".
 *
 * Three fields carry more meaning than their type suggests.
 *
 * ``ConversationSummary.preview``
 *     True when the conversation has no instance version: it was had against
 *     a draft from the admin screen rather than by a visitor. The backend
 *     flags these rather than hiding them, because a filtered-out
 *     conversation is one somebody will go looking for -- so the screens
 *     label them and count them separately instead of dropping them.
 * ``Turn.ending``
 *     Why the turn stopped. ``answered`` and ``decorated`` are fine;
 *     ``rounds``, ``empty`` and ``unavailable`` are three different bugs that
 *     look identical from the reply text alone. ``unavailable`` is an outage
 *     that reached a customer, which is the one worth being able to count.
 * ``Turn.inputTokens`` / ``Turn.outputTokens``
 *     What the turn cost, in the only unit this service actually knows.
 *     Nothing here converts them to money: no price list exists in the
 *     backend, and a number with a currency symbol in front of it would be an
 *     invention.
 */

/** Where the conversation happened. Mirrors a definition's channels. */
export type Channel = "web_chat" | "email" | "voice" | "whatsapp";

/**
 * One line of a turn's record of what it did.
 *
 * Stored as plain data by the service rather than as the runner's object, so
 * these are the keys a transcript months old will still have. ``error`` is an
 * ability error code and is empty when ``ok``; ``replayed`` means the result
 * came from the turn's own cache rather than from a second call.
 */
export interface AbilityCall {
  key: string;
  ok: boolean;
  error: string;
  milliseconds: number;
  replayed: boolean;
}

/**
 * A passage the reply drew on, as the transcript stores it.
 *
 * ``text`` is here and deliberately: attribution is decided by comparing the
 * reply against each passage, so the passage text is what makes a citation
 * checkable. A visitor is sent the name and the link and nothing else.
 */
export interface TurnSource {
  sourceId?: string;
  name?: string;
  url?: string | null;
  score?: number;
  approved?: boolean;
  text?: string;
}

export interface Turn {
  speaker: "visitor" | "agent";
  text: string;
  at: string | null;
  ending: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
  calls: AbilityCall[];
  sources: TurnSource[];
}

export interface ConversationSummary {
  id: string;
  /** Which agent. An id, not a label -- the agents query supplies those. */
  instanceId: string;
  channel: string;
  turns: number;
  /** How many of them the visitor said. The measure of a real exchange. */
  visitorTurns: number;
  startedAt: string | null;
  updatedAt: string | null;
  pageUrl: string;
  pageTitle: string;
  /** The versions that were live when it started, so a transcript can be
   *  read against the configuration that produced it. */
  templateVersion: number | null;
  instanceVersion: number | null;
  inputTokens: number;
  outputTokens: number;
  preview: boolean;
}

/** A summary plus every turn in it. */
export interface Transcript extends ConversationSummary {
  turnsDetail: Turn[];
}

/**
 * What the list may be narrowed by.
 *
 * All three are the API's: there is no client-side filtering anywhere in
 * these screens, because ``limit`` is applied by the server and filtering
 * after it would quietly drop rows that were inside the limit.
 */
export interface ConversationFilter {
  instanceId?: string;
  channel?: Channel;
  limit?: number;
}
