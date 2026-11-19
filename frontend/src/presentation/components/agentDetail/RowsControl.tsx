/**
 * A ``list`` setting: rows of declared fields.
 *
 * The only control that draws other controls. Each row field is an
 * ``ItemSpec``, so every row cell is the same ``SettingControl`` a top-level
 * setting uses -- which is why adding a setting type to that switch makes it
 * available inside rows too, for free.
 *
 * Two behaviours come straight from the declaration and matter:
 *
 * **A new row is built from the item specs, not from an empty object.** The
 * validator refuses a row that is missing any field, so a blank row would be
 * unsaveable from the moment it appeared. Each cell starts at the emptiest
 * value its own type allows.
 *
 * **``uniqueBy`` is marked on the row, not just refused on save.** Two rows
 * with the same key is the mistake this kind of setting invites, and the
 * server's message names the field rather than the row, so pointing at the
 * duplicate here is the only way anyone finds which two.
 */

import type { FieldSpec, ItemSpec } from "@/data/types/settings";
import { Button } from "@/presentation/components/ui/Button";
import { ConfirmButton } from "@/presentation/components/ui/ConfirmButton";

import { SettingControl } from "./SettingControl";

type Row = Record<string, unknown>;

interface RowsControlProps {
  id: string;
  spec: FieldSpec;
  value: Row[];
  disabled?: boolean;
  onChange: (value: Row[]) => void;
}

/** The emptiest value a cell of this type can hold and still be a value. */
function blank(spec: ItemSpec): unknown {
  switch (spec.type) {
    case "boolean":
      return false;
    case "number":
      return spec.min ?? 0;
    case "enum":
      return spec.options?.[0]?.value ?? "";
    case "multiselect":
    case "stringList":
      return [];
    default:
      return "";
  }
}

export function RowsControl({
  id,
  spec,
  value,
  disabled = false,
  onChange,
}: RowsControlProps) {
  const fields = Object.entries(spec.item ?? {});
  const limit = spec.maxItems ?? 50;
  const unique = spec.uniqueBy;

  // Which values appear more than once, so each offender can be marked.
  const duplicated = new Set(
    unique
      ? value
          .map((row) => String(row[unique] ?? ""))
          .filter((one, at, all) => one !== "" && all.indexOf(one) !== at)
      : [],
  );

  return (
    <div className="space-y-3">
      {value.map((row, at) => {
        const clash = unique ? duplicated.has(String(row[unique] ?? "")) : false;
        return (
          <div
            key={at}
            className={`space-y-3 rounded-lg border bg-canvas px-4 py-3 ${
              clash ? "border-bad" : "border-line"
            }`}
          >
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs font-medium text-ink-soft">Item {at + 1}</span>
              <ConfirmButton
                label="Remove"
                confirmLabel="Remove it"
                disabled={disabled}
                onConfirm={() => onChange(value.filter((_one, index) => index !== at))}
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {fields.map(([name, item]) => (
                <div key={name} className="space-y-1">
                  <label
                    htmlFor={`${id}-${at}-${name}`}
                    className="block text-xs font-medium text-ink"
                  >
                    {item.label}
                  </label>
                  <SettingControl
                    id={`${id}-${at}-${name}`}
                    spec={item}
                    value={row[name]}
                    disabled={disabled}
                    onChange={(next) =>
                      onChange(
                        value.map((one, index) =>
                          index === at ? { ...one, [name]: next } : one,
                        ),
                      )
                    }
                  />
                  {item.description ? (
                    <p className="text-xs text-ink-soft">{item.description}</p>
                  ) : null}
                </div>
              ))}
            </div>
            {clash && unique ? (
              <p role="alert" className="text-xs text-bad">
                Another item already uses this {spec.item?.[unique]?.label ?? unique}.
              </p>
            ) : null}
          </div>
        );
      })}
      <div className="flex items-center gap-3">
        <Button
          type="button"
          disabled={disabled || value.length >= limit}
          onClick={() =>
            onChange([
              ...value,
              Object.fromEntries(fields.map(([name, item]) => [name, blank(item)])),
            ])
          }
        >
          Add item
        </Button>
        <span className="text-xs text-ink-soft">
          {value.length} of {limit}
        </span>
      </div>
    </div>
  );
}
