/**
 * The frame around one control: its label, its words, and its problem.
 *
 * Every control in the generated form is wrapped in this, so the label is
 * tied to the input in one place rather than eight, and a rejected value is
 * drawn the same way whichever type of setting it was.
 *
 * ``htmlFor`` is passed in rather than derived from the label, because a
 * setting's label is prose written by whoever wrote the agent type -- two
 * settings may legitimately share one, and a generated id from a label would
 * then point a click at the wrong field.
 *
 * ``composite`` is for a control that is not one element -- a set of
 * checkboxes, rows of fields. ``for`` may only name a form control, so a
 * label pointing at the wrapper of several would be invalid markup and would
 * leave a screen reader with an unlabelled group. Those get a named group
 * instead, which is the same label said the way the platform understands.
 */

import type { ReactNode } from "react";

interface FieldProps {
  id: string;
  label: string;
  description?: string;
  problem?: string;
  /** Pulled to the right of the label: "default", "changed", "locked". */
  aside?: ReactNode;
  /** True when the control is several elements rather than one. */
  composite?: boolean;
  children: ReactNode;
}

export function Field({
  id,
  label,
  description,
  problem,
  aside,
  composite = false,
  children,
}: FieldProps) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3">
        {composite ? (
          <span id={`${id}-label`} className="text-sm font-medium text-ink">
            {label}
          </span>
        ) : (
          <label htmlFor={id} className="text-sm font-medium text-ink">
            {label}
          </label>
        )}
        {aside}
      </div>
      {description ? <p className="text-xs text-ink-soft">{description}</p> : null}
      {composite ? (
        <div role="group" aria-labelledby={`${id}-label`}>
          {children}
        </div>
      ) : (
        children
      )}
      {problem ? (
        <p id={`${id}-problem`} role="alert" className="text-xs text-bad">
          {problem}
        </p>
      ) : null}
    </div>
  );
}
