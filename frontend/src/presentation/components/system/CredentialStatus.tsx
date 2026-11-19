/**
 * Whether the token in the session is accepted, and by what.
 *
 * It asks the catalogue -- the cheapest authenticated read there is, and one
 * every workspace token is allowed to make. A 403 rather than a 401 means the
 * token is real but has no workspace, which is exactly what a platform token
 * is, so it gets said rather than drawn as a failure.
 */

import { useCatalogueQuery } from "@/data/api/catalogueApi";
import { codeOf, messageOf, statusOf } from "@/data/client/errors";
import { StatusDot } from "@/presentation/components/ui/StatusDot";
import { Spinner } from "@/presentation/components/ui/Spinner";

export function CredentialStatus({ token }: { token: string }) {
  const { error, isLoading, isSuccess } = useCatalogueQuery(undefined, {
    skip: !token,
  });

  if (!token) return <StatusDot tone="idle" label="No token set" />;
  if (isLoading) return <Spinner label="Checking the token" />;
  if (isSuccess) return <StatusDot tone="good" label="Token accepted" />;

  const status = statusOf(error);
  if (status === 401) return <StatusDot tone="bad" label="Token rejected" />;
  if (status === 403 && codeOf(error) === "FORBIDDEN") {
    return <StatusDot tone="warn" label="Platform token: no workspace" />;
  }
  return <StatusDot tone="bad" label={messageOf(error)} />;
}
