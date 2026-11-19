/**
 * One agent type a workspace may build on.
 *
 * The tile is disabled when ``owned`` has reached ``maxPerWorkspace``, and it
 * says so. Both numbers come down in the catalogue precisely so this screen
 * can refuse before the request rather than translating a 409 afterwards.
 */

import type { CatalogueEntry } from "@/data/types/catalogue";

interface CatalogueTileProps {
  entry: CatalogueEntry;
  selected: boolean;
  onSelect: () => void;
}

export function CatalogueTile({ entry, selected, onSelect }: CatalogueTileProps) {
  const full = entry.owned >= entry.maxPerWorkspace;

  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={full}
      aria-pressed={selected}
      className={
        "w-full rounded-xl border p-4 text-left transition disabled:cursor-not-allowed disabled:opacity-60 " +
        (selected ? "border-brand bg-brand-soft" : "border-line bg-surface hover:border-brand/40")
      }
    >
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-semibold text-ink">{entry.name}</span>
        <span className="text-xs text-ink-soft">v{entry.version}</span>
      </div>
      <p className="mt-1 text-sm text-ink-soft">{entry.description}</p>
      <p className="mt-3 text-xs text-ink-soft">
        {entry.owned} of {entry.maxPerWorkspace} used
        {full ? " — no more of this type" : null}
      </p>
    </button>
  );
}
