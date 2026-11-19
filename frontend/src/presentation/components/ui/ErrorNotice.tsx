/**
 * One failure, shown the same way everywhere.
 *
 * It takes the error object rather than a string so that no component has to
 * know the envelope: the data layer's ``messageOf`` and ``problemsOf`` decide
 * what a failure means, here and in every other place one is drawn.
 */

import type { ApiError } from "@/data/client/errors";
import { messageOf, problemsOf, statusOf } from "@/data/client/errors";

export function ErrorNotice({ error }: { error: ApiError | undefined }) {
  if (!error) return null;
  const problems = problemsOf(error);
  const status = statusOf(error);
  return (
    <div
      role="alert"
      className="rounded-lg border border-bad/30 bg-bad/5 px-4 py-3 text-sm text-ink"
    >
      <p className="font-medium text-bad">
        {messageOf(error)}
        {status ? <span className="font-normal text-ink-soft"> ({status})</span> : null}
      </p>
      {problems.length ? (
        <ul className="mt-2 space-y-1 text-ink-soft">
          {problems.map((one) => (
            <li key={`${one.path}:${one.message}`}>
              <code className="text-xs">{one.path}</code> — {one.message}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
