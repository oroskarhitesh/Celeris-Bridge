/**
 * The backend's one error shape, read once.
 *
 * Every failure from the API -- a domain refusal, a bad body, a route that
 * does not exist -- arrives as ``{error: {code, message, details?}}`` with a
 * stable code. RTK Query hands that back as an untyped ``unknown`` body
 * alongside transport failures that have no body at all, so this module is
 * the single place that tells those apart. A component asks for a message, a
 * code, or the per-field problems; it never reaches into ``error.data``.
 */

import type { FetchBaseQueryError } from "@reduxjs/toolkit/query";
import type { SerializedError } from "@reduxjs/toolkit";

/** Anything a query or mutation can fail with. */
export type ApiError = FetchBaseQueryError | SerializedError;

export interface Problem {
  /** Where the problem is: a settings key, or a dotted body path. */
  path: string;
  code?: string;
  message: string;
}

interface Envelope {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

function envelopeOf(error: ApiError | undefined): Envelope | undefined {
  if (!error || !("data" in error)) return undefined;
  const data = error.data as { error?: unknown } | undefined;
  const inner = data?.error as Partial<Envelope> | undefined;
  if (!inner || typeof inner.code !== "string") return undefined;
  return {
    code: inner.code,
    message: typeof inner.message === "string" ? inner.message : "",
    ...(inner.details ? { details: inner.details } : {}),
  };
}

/** The HTTP status, when there was one. A network failure has none. */
export function statusOf(error: ApiError | undefined): number | undefined {
  if (!error || !("status" in error)) return undefined;
  return typeof error.status === "number" ? error.status : undefined;
}

/** The stable machine code, for a component that must branch on one. */
export function codeOf(error: ApiError | undefined): string | undefined {
  return envelopeOf(error)?.code;
}

/**
 * The 5xx codes whose message is still worth quoting.
 *
 * ``FETCH_FAILED`` is a 502 and it is *about* somebody else's server: the
 * customer's site refused the crawler, or timed out, and the backend says
 * which. That is the whole explanation for a crawl that indexed nothing, so
 * swallowing it would leave an admin with "something went wrong on the
 * server" about a server that is working.
 *
 * Nothing else belongs here. A 500 from this service carries no words on
 * purpose, and a code added to this set would have to be one whose message is
 * written for a stranger to read.
 */
const QUOTABLE_5XX = new Set(["FETCH_FAILED"]);

/**
 * Something to show a person.
 *
 * A 5xx deliberately does not quote the server: the backend sends no words on
 * a 500, and inventing some here would be putting a bug in a sentence. The
 * exception is a failure that is explicitly about a third party -- see
 * ``QUOTABLE_5XX``.
 */
export function messageOf(error: ApiError | undefined): string {
  if (!error) return "";
  const envelope = envelopeOf(error);
  const status = statusOf(error);
  if (
    envelope?.message &&
    (status === undefined || status < 500 || QUOTABLE_5XX.has(envelope.code))
  ) {
    return envelope.message;
  }
  if (status === undefined) return "Could not reach the server.";
  if (status === 401) return "That credential was not accepted.";
  if (status >= 500) return "Something went wrong on the server.";
  return "That request was refused.";
}

/**
 * The per-field problems, for a form that should mark its own fields.
 *
 * Two keys because the backend says two different things: a rejected body or
 * an unpublishable definition lists ``details.problems``, and rejected
 * settings overrides list ``details.rejections``. Both are a path, a message
 * and sometimes a code, so a form should not have to care which it got.
 */
export function problemsOf(error: ApiError | undefined): Problem[] {
  const details = envelopeOf(error)?.details;
  const raw = details?.["problems"] ?? details?.["rejections"];
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((one) => {
    if (!one || typeof one !== "object") return [];
    const problem = one as Partial<Problem>;
    if (typeof problem.path !== "string") return [];
    return [
      {
        path: problem.path,
        message: typeof problem.message === "string" ? problem.message : "",
        ...(typeof problem.code === "string" ? { code: problem.code } : {}),
      },
    ];
  });
}

/**
 * The revision the server actually holds, when a write lost a race.
 *
 * ``RevisionConflict`` is a 409 carrying ``{expected, found}``. A form that
 * reads ``found`` can offer to reload rather than making someone guess why
 * saving stopped working.
 */
export function foundRevisionOf(error: ApiError | undefined): number | undefined {
  if (codeOf(error) !== "REVISION_CONFLICT") return undefined;
  const found = envelopeOf(error)?.details?.["found"];
  return typeof found === "number" ? found : undefined;
}
