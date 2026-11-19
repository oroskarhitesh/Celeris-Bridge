/**
 * The agent types, as a chooser.
 *
 * An empty catalogue is not an error and is not this workspace's fault: it
 * means the platform has not published an agent type yet, which is a
 * different sentence from "nothing matched".
 */

import type { ApiError } from "@/data/client/errors";
import type { CatalogueEntry } from "@/data/types/catalogue";
import { EmptyState } from "@/presentation/components/ui/EmptyState";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { Spinner } from "@/presentation/components/ui/Spinner";

import { CatalogueTile } from "./CatalogueTile";

interface CatalogueGridProps {
  entries: CatalogueEntry[] | undefined;
  loading: boolean;
  error: ApiError | undefined;
  selected: string;
  onSelect: (templateKey: string) => void;
}

export function CatalogueGrid({
  entries,
  loading,
  error,
  selected,
  onSelect,
}: CatalogueGridProps) {
  if (loading) return <Spinner label="Loading agent types" />;
  if (error) return <ErrorNotice error={error} />;
  if (!entries?.length) {
    return (
      <EmptyState
        title="No agent types yet"
        description="The platform publishes these. Until one is published there is nothing to build on."
      />
    );
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {entries.map((entry) => (
        <CatalogueTile
          key={entry.templateKey}
          entry={entry}
          selected={entry.templateKey === selected}
          onSelect={() => onSelect(entry.templateKey)}
        />
      ))}
    </div>
  );
}
