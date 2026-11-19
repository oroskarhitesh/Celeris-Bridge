/**
 * The list, and the states a list has.
 *
 * Given its data rather than fetching it, like every other list here: the
 * page owns the query, this owns the markup, and a test can render it with a
 * fixed array.
 */

import type { ApiError } from "@/data/client/errors";
import type { Source } from "@/data/types/knowledge";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { Spinner } from "@/presentation/components/ui/Spinner";

import { SourceRow } from "./SourceRow";

interface SourceListProps {
  sources: Source[] | undefined;
  loading: boolean;
  error: ApiError | undefined;
}

export function SourceList({ sources, loading, error }: SourceListProps) {
  if (loading) return <Spinner label="Loading sources" />;
  if (error) return <ErrorNotice error={error} />;
  return (
    <div className="space-y-3">
      {(sources ?? []).map((source) => (
        <SourceRow key={source.sourceId} source={source} />
      ))}
    </div>
  );
}
