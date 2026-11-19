/**
 * One scalar setting, as whichever control its declaration calls for.
 *
 * A switch on ``spec.type`` and nothing else. It is deliberately one
 * component rather than eight files: the whole of each branch is which input
 * element and which constraints to hand it, and a reader comparing "how is a
 * number drawn against a colour" should not have to open two files to find
 * out that the answer is three lines each.
 *
 * The constraints are passed to the element, not re-implemented. ``maxLength``
 * stops a paste the validator would refuse, ``min``/``max`` make a number
 * input's arrows respect the setting's range, and ``step`` follows
 * ``integer``. None of it is the real check -- that is the server's, from the
 * same declaration -- but a control that cannot produce a rejected value is
 * better than one that explains the rejection afterwards.
 *
 * It takes an ``ItemSpec``, so it serves both a top-level setting and one
 * field of a list setting's row. That is why rows cost no extra controls.
 */

import type { ItemSpec } from "@/data/types/settings";
import { SelectInput } from "@/presentation/components/ui/SelectInput";
import { TextArea } from "@/presentation/components/ui/TextArea";
import { Toggle } from "@/presentation/components/ui/Toggle";

import { MultiselectControl } from "./MultiselectControl";
import { StringListControl } from "./StringListControl";

export interface ControlProps {
  id: string;
  spec: ItemSpec;
  value: unknown;
  disabled?: boolean;
  problem?: boolean;
  /** True when ``null`` is a value this setting may hold. */
  nullable?: boolean;
  onChange: (value: unknown) => void;
}

export function SettingControl({
  id,
  spec,
  value,
  disabled = false,
  problem = false,
  nullable = false,
  onChange,
}: ControlProps) {
  const shared = { id, disabled, problem };

  switch (spec.type) {
    case "boolean":
      return (
        <Toggle
          id={id}
          checked={value === true}
          disabled={disabled}
          label={value === true ? "Yes" : "No"}
          onChange={onChange}
        />
      );

    case "enum": {
      const choices = (spec.options ?? []).map((one) => ({
        value: one.value,
        label: one.label,
      }));
      return (
        <SelectInput
          {...shared}
          value={typeof value === "string" ? value : ""}
          // An empty option only when the setting may actually hold nothing.
          // Offering one otherwise would let someone choose a value the
          // validator refuses as "must not be empty".
          choices={nullable ? [{ value: "", label: "Not chosen" }, ...choices] : choices}
          onChange={(event) =>
            onChange(event.target.value === "" && nullable ? null : event.target.value)
          }
        />
      );
    }

    case "multiselect":
      return (
        <MultiselectControl
          {...shared}
          spec={spec}
          value={Array.isArray(value) ? (value as string[]) : []}
          onChange={onChange}
        />
      );

    case "stringList":
      return (
        <StringListControl
          {...shared}
          spec={spec}
          value={Array.isArray(value) ? (value as string[]) : []}
          onChange={onChange}
        />
      );

    case "number":
      return (
        <input
          {...shared}
          type="number"
          value={typeof value === "number" ? value : ""}
          min={spec.min}
          max={spec.max}
          step={spec.integer ? 1 : "any"}
          aria-invalid={problem ? true : undefined}
          onChange={(event) =>
            // Empty is not zero. A cleared box is "no value yet", and sending
            // 0 for it would quietly save a number nobody typed.
            onChange(event.target.value === "" ? null : event.target.valueAsNumber)
          }
          className={`w-40 rounded-lg border bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-brand ${
            problem ? "border-bad" : "border-line"
          }`}
        />
      );

    case "color":
      return (
        <span className="flex items-center gap-2">
          <input
            {...shared}
            type="color"
            value={typeof value === "string" && value ? value : "#000000"}
            onChange={(event) => onChange(event.target.value)}
            className="size-8 cursor-pointer rounded border border-line bg-surface"
          />
          <code className="text-xs text-ink-soft">
            {typeof value === "string" ? value : ""}
          </code>
        </span>
      );

    case "url":
      return (
        <input
          {...shared}
          type="url"
          inputMode="url"
          placeholder="https://"
          value={typeof value === "string" ? value : ""}
          aria-invalid={problem ? true : undefined}
          onChange={(event) => onChange(event.target.value)}
          className={`w-full rounded-lg border bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-brand ${
            problem ? "border-bad" : "border-line"
          }`}
        />
      );

    case "string":
      return spec.multiline ? (
        <TextArea
          {...shared}
          value={typeof value === "string" ? value : ""}
          maxLength={spec.maxLength}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <input
          {...shared}
          type="text"
          value={typeof value === "string" ? value : ""}
          maxLength={spec.maxLength}
          aria-invalid={problem ? true : undefined}
          onChange={(event) => onChange(event.target.value)}
          className={`w-full rounded-lg border bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-brand ${
            problem ? "border-bad" : "border-line"
          }`}
        />
      );
  }
}
