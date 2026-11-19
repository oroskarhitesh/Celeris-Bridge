/**
 * The widget's whole data layer: three fetches and an error reader.
 *
 * It shares nothing with ``src/data`` on purpose, and the reason is the
 * bundle. This file ships to a customer's website, inside their page, on
 * every page view -- so it carries no store, no router, no query cache and no
 * Redux. Importing ``data/client/errors`` would pull in ``@reduxjs/toolkit``
 * types and the habit of reaching for the rest; copying twenty lines of
 * envelope reading is cheaper than that, and the envelope is the backend's
 * one shape, which does not move.
 *
 * There is no caching and there must not be. A reply is the result of a model
 * call that was billed and appended to a stored transcript; a cache that
 * could replay a request would be a cache that can say something twice.
 *
 * **No workspace is ever read from anywhere but the embed.** The ids are in
 * the script tag, which is the public address of a chat widget -- the same
 * kind of identifier as the script's own URL. The public router exposes
 * exactly three things and each refuses unless the agent is live, which is
 * what makes that safe.
 */

import type { AgentFace, Reply, StartedConversation } from "./types";

export interface WidgetConfig {
  /** Where the API is. No trailing slash. */
  api: string;
  workspaceId: string;
  instanceId: string;
}

/**
 * A refusal, read from the backend's one envelope.
 *
 * ``code`` is what the widget branches on -- ``WIDGET_DISABLED`` and
 * ``ORIGIN_NOT_ALLOWED`` are a customer's deployment being wrong, not a
 * visitor doing anything -- and ``message`` is only shown when it is safe to
 * show: a 5xx carries no words written for a stranger.
 */
export class WidgetError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "WidgetError";
    this.code = code;
    this.status = status;
  }
}

interface Envelope {
  error?: { code?: unknown; message?: unknown };
}

async function refusalOf(response: Response): Promise<WidgetError> {
  let code = "HTTP_ERROR";
  let message = "";
  try {
    const body = (await response.json()) as Envelope;
    if (typeof body?.error?.code === "string") code = body.error.code;
    if (typeof body?.error?.message === "string") message = body.error.message;
  } catch {
    // Not JSON: a proxy, a gateway, a captive portal. The status is all
    // there is, and that is enough to decide what to say.
  }
  return new WidgetError(code, message, response.status);
}

async function send<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: { "content-type": "application/json", ...init?.headers },
    });
  } catch {
    // A visitor's network, a blocked request, a site served over a tunnel
    // that went away. Not a refusal: there is no status to read.
    throw new WidgetError("OFFLINE", "", 0);
  }
  if (!response.ok) throw await refusalOf(response);
  return (await response.json()) as T;
}

/** Where this agent lives, as the public API addresses it. */
function base(config: WidgetConfig): string {
  return `${config.api}/v1/widget/${encodeURIComponent(config.workspaceId)}/${encodeURIComponent(config.instanceId)}`;
}

/**
 * The agent's public face.
 *
 * Origin-checked like everything else here, so a widget embedded where it
 * does not belong fails before it draws a launcher rather than on the
 * visitor's first message.
 */
export function faceOf(config: WidgetConfig): Promise<AgentFace> {
  return send<AgentFace>(base(config));
}

/** Open a conversation. No model call, so it costs nothing. */
export function startConversation(
  config: WidgetConfig,
  page: { pageUrl: string; pageTitle: string },
): Promise<StartedConversation> {
  return send<StartedConversation>(`${base(config)}/conversations`, {
    method: "POST",
    body: JSON.stringify(page),
  });
}

/** Say one thing. This is the request that costs money. */
export function sayToAgent(
  config: WidgetConfig,
  conversationId: string,
  message: string,
): Promise<Reply> {
  return send<Reply>(
    `${base(config)}/conversations/${encodeURIComponent(conversationId)}/messages`,
    { method: "POST", body: JSON.stringify({ message }) },
  );
}

/**
 * What to say to a visitor about a failure.
 *
 * Three kinds, and they are different sentences on purpose.
 *
 * A **deployment** problem -- the agent is paused, the widget is switched
 * off, this page is not in the allowed origins -- is not the visitor's
 * problem and not something they can retry. It says the chat is unavailable
 * and stops offering a box.
 *
 * A **turn limit** is the agent's own setting working. The backend's sentence
 * is written for a visitor to read, so it is quoted.
 *
 * Everything else is "try again", because everything else might work.
 */
export function wordsFor(error: unknown): string {
  if (!(error instanceof WidgetError)) return "Something went wrong. Please try again.";
  if (error.code === "CONVERSATION_TOO_LONG") {
    return error.message || "This conversation has reached its limit.";
  }
  if (error.status === 404 || error.status === 403 || error.status === 409) {
    return "This chat is not available right now.";
  }
  if (error.status === 0) return "You appear to be offline. Please try again.";
  // Deliberately not the server's words: a 5xx here carries a message
  // written for whoever runs the service, not for a stranger on a website.
  return "Something went wrong. Please try again.";
}

/** Whether a failure is one that ends the conversation rather than a retry. */
export function isFinal(error: unknown): boolean {
  if (!(error instanceof WidgetError)) return false;
  if (error.code === "CONVERSATION_TOO_LONG") return true;
  return error.status === 404 || error.status === 403 || error.status === 409;
}
