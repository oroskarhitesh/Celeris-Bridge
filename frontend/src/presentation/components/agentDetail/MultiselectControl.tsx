/**
 * Several of a fixed set of choices.
 *
 * Checkboxes rather than a multiple ``<select>``: the options are declared,
 * usually few, and a native multi-select is the control people most often
 * get wrong -- ctrl-clicking to keep a previous choice is not discoverable.
 *
 * ``maxItems`` disables the unticked boxes once the limit is reached instead
 * of refusing the click silently. Order is the declared order, so the value
 * saved reads the same way it was presented.
 */

import type { ItemSpec } from "@/data/types/settings";

interface MultiselectControlProps {
  /** Only for the checkbox ids: the group's label is the field's. */
  id: string;
  spec: ItemSpec;
  value: string[];
  disabled?: boolean;
  onChange: (value: string[]) => void;
}

export function MultiselectControl({
  id,
  spec,
  value,
  disabled = false,
  onChange,
}: MultiselectControlProps) {
  const options = spec.options ?? [];
  const limit = spec.maxItems ?? options.length;
  const full = value.length >= limit;

  return (
    <div className="flex flex-wrap gap-x-5 gap-y-2">
      {options.map((option) => {
        const on = value.includes(option.value);
        return (
          <label
            key={option.value}
            className={`inline-flex items-center gap-2 text-sm text-ink ${
              disabled || (full && !on) ? "opacity-50" : "cursor-pointer"
            }`}
          >
            <input
              id={`${id}-${option.value}`}
              type="checkbox"
              checked={on}
              disabled={disabled || (full && !on)}
              onChange={() =>
                onChange(
                  on
                    ? value.filter((one) => one !== option.value)
                    : // Rebuilt in declared order rather than appended, so two
                      // people ticking the same boxes save the same value.
                      options
                        .map((one) => one.value)
                        .filter((one) => one === option.value || value.includes(one)),
                )
              }
              className="size-4 rounded border-line text-brand"
            />
            {option.label}
          </label>
        );
      })}
      {full ? (
        <span className="text-xs text-ink-soft">
          {limit} is the most this setting takes.
        </span>
      ) : null}
    </div>
  );
}
