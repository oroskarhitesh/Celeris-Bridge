/**
 * Is the backend there, and is the token any good?
 *
 * Two questions and two calls, because the answers fail differently:
 * ``/readyz`` needs no credential, so a failure there is the base URL, CORS
 * or the database, and a 401 from an authenticated call with a working
 * ``/readyz`` is only ever the token. Separating them is what makes the first
 * five minutes of this frontend debuggable.
 */

import { useReadinessQuery } from "@/data/api/healthApi";
import { statusOf } from "@/data/client/errors";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { Spinner } from "@/presentation/components/ui/Spinner";
import { StatusDot } from "@/presentation/components/ui/StatusDot";

export function BackendStatus() {
  const { data, error, isLoading, isFetching, refetch } = useReadinessQuery();

  if (isLoading) return <Spinner label="Checking the backend" />;

  if (error) {
    return (
      <div className="space-y-3">
        <ErrorNotice error={error} />
        {statusOf(error) === undefined ? (
          <p className="text-sm text-ink-soft">
            Nothing answered at <code>{import.meta.env.VITE_API_URL}</code>. Either the
            backend is not running, or its <code>CB_ADMIN_ORIGINS</code> does not include{" "}
            <code>{window.location.origin}</code> — a blocked origin looks exactly like
            an unreachable server from in here.
          </p>
        ) : null}
        <button
          type="button"
          onClick={() => void refetch()}
          className="text-sm font-medium text-brand"
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
      <dt className="text-ink-soft">Backend</dt>
      <dd>
        <StatusDot tone="good" label={data?.status ?? "ready"} />
      </dd>
      <dt className="text-ink-soft">Records</dt>
      <dd className="text-ink">{data?.records}</dd>
      <dt className="text-ink-soft">API</dt>
      <dd className="text-ink">
        <code className="text-xs">{import.meta.env.VITE_API_URL}</code>
        {isFetching ? <span className="ml-2 text-ink-soft">refreshing…</span> : null}
      </dd>
    </dl>
  );
}
