/**
 * A list of short strings: one input per entry, plus one to add.
 *
 * Not a comma-separated text box, which is the usual shortcut and is wrong
 * here for two reasons: an entry may legitimately contain a comma, and the
 * server's limits are per entry (``maxLength``) and per list (``maxItems``),
 * so a single box could not say which one a paste broke.
 *
 * An empty entry is refused by the validator, so "Add" stays out of reach
 * until the last row has something in it rather than letting someone build a
 * list that cannot save.
 */

import type { ItemSpec } from "@/data/types/settings";
import { Button } from "@/presentation/components/ui/Button";

interface StringListControlProps {
  id: string;
  spec: ItemSpec;
  value: string[];
  disabled?: boolean;
  onChange: (value: string[]) => void;
}

export function StringListControl({
  id,
  spec,
  value,
  disabled = false,
  onChange,
}: StringListControlProps) {
  const limit = spec.maxItems ?? 20;
  const last = value.at(-1);
  const canAdd = !disabled && value.length < limit && (value.length === 0 || !!last?.trim());

  return (
    <div className="space-y-2">
      {value.map((entry, at) => (
        <div key={at} className="flex items-center gap-2">
          <input
            // Indexed, because the entries have no identity of their own: two
            // may be equal, and the position is what is being edited.
            id={at === 0 ? id : `${id}-${at}`}
            type="text"
            value={entry}
            disabled={disabled}
            maxLength={spec.maxLength ?? 200}
            aria-label={`${spec.label}, entry ${at + 1}`}
            onChange={(event) =>
              onChange(value.map((one, index) => (index === at ? event.target.value : one)))
            }
            className="w-full rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-brand"
          />
          <Button
            look="ghost"
            type="button"
            disabled={disabled}
            aria-label={`Remove entry ${at + 1}`}
            onClick={() => onChange(value.filter((_one, index) => index !== at))}
          >
            Remove
          </Button>
        </div>
      ))}
      <div className="flex items-center gap-3">
        <Button
          type="button"
          disabled={!canAdd}
          onClick={() => onChange([...value, ""])}
        >
          Add
        </Button>
        <span className="text-xs text-ink-soft">
          {value.length} of {limit}
        </span>
      </div>
    </div>
  );
}
