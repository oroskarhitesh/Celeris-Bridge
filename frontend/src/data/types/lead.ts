/**
 * A lead: a conversation that produced some details.
 *
 * A mirror of ``api/routes/leads.py``, which is a read model over two records
 * that already exist. There is no ``POST`` and there is no lead table: a lead
 * is a conversation, so ``conversationId`` is its id and the transcript is
 * one link away.
 *
 * Two things about this shape drive every decision on the screen.
 *
 * **``details`` is a map, not columns.** Which details an agent collects is a
 * setting -- a workspace that asks for a company size and no phone number is
 * configured, not broken -- so a table with "Name" and "Email" headings would
 * be wrong for anyone who changed it. The keys are whatever that workspace
 * asked for, and they are rendered as they arrive.
 *
 * **The verdict was recorded when the visitor said it.** The backend does not
 * recompute it for the list, on purpose: a qualification rule changed last
 * week must not rewrite what a lead was called on Tuesday. So ``status``,
 * ``missing`` and ``reasons`` are history, and nothing on these screens
 * offers to recalculate them.
 */

/**
 * How far a conversation got, as it was judged at the time.
 *
 * Not a progress bar: ``not_a_fit`` is a finished state and the backend's own
 * comment on it is the line worth keeping -- "not someone to sell to, still
 * someone to help".
 */
export type VisitorStatus = "exploring" | "qualifying" | "qualified" | "not_a_fit";

export interface Lead {
  conversationId: string;
  instanceId: string;
  channel: string;
  /** What the visitor gave, under the keys the workspace asked for. */
  details: Record<string, string>;
  /** What the model was told they are: a buyer, a student, a competitor. */
  visitorType: string;
  status: string;
  /** Required details still missing. The follow-up list, in other words. */
  missing: string[];
  /** Why they are not a fit, if they are not. Plain sentences. */
  reasons: string[];
  /** Visitor turns only, which is the number that says whether they talked. */
  turns: number;
  startedAt: string | null;
  lastAt: string | null;
  pageUrl: string;
  /** The first thing the visitor said: why they got in touch, in their own
   *  words. The most useful column on the screen. */
  openingMessage: string;
  /** Everything the workspace asks for was collected. */
  complete: boolean;
}

export interface LeadFilter {
  instanceId?: string;
  channel?: string;
  status?: string;
  /**
   * Only conversations that produced details.
   *
   * The server's default is false and the screen's default is true, which is
   * a deliberate disagreement: a public widget has far more conversations
   * than leads, and a list that includes every passer-by who said "hi"
   * buries the ones worth calling. The screen says which it is showing and
   * the choice is in the URL.
   */
  withDetailsOnly?: boolean;
  limit?: number;
}
