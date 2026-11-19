/**
 * What is unsaved, and the two things to do about it.
 *
 * It stays out of the way at nothing-to-save and becomes a sticky bar the
 * moment there is something, so the answer to "did that take?" is always on
 * screen without a dialog.
 *
 * The count is of real changes, not of fields touched: type a value, type it
 * back, and this disappears on its own. That is the whole reason the draft
 * compares against the server's answer rather than tracking edits.
 */

import type { ApiError } from "@/data/client/errors";
import { Button } from "@/presentation/components/ui/Button";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";

interface SaveBarProps {
  changes: number;
  busy: boolean;
  error: ApiError | undefined;
  onSave: () => void;
  onDiscard: () => void;
}

export function SaveBar({ changes, busy, error, onSave, onDiscard }: SaveBarProps) {
  // The error outlives the draft when a save is refused for something that is
  // not a field -- so it is drawn whether or not anything is still unsaved.
  if (!changes) return error ? <ErrorNotice error={error} /> : null;

  return (
    <div className="sticky bottom-0 space-y-3 border-t border-line bg-surface/95 px-1 py-3 backdrop-blur">
      {error ? <ErrorNotice error={error} /> : null}
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-ink">
          {changes === 1 ? "One unsaved change" : `${changes} unsaved changes`}
          <span className="text-ink-soft">
            {" "}
            · nobody sees them until you save and publish
          </span>
        </p>
        <div className="flex items-center gap-2">
          <Button look="ghost" type="button" disabled={busy} onClick={onDiscard}>
            Discard
          </Button>
          <Button look="primary" type="button" busy={busy} onClick={onSave}>
            Save draft
          </Button>
        </div>
      </div>
    </div>
  );
}
