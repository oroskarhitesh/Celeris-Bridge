/**
 * On or off.
 *
 * A real checkbox underneath rather than a styled div: it is focusable, it
 * toggles on space, a screen reader announces it, and a test can click it by
 * its label. The switch is decoration drawn on top.
 */

interface ToggleProps {
  id: string;
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}

export function Toggle({ id, checked, disabled = false, label, onChange }: ToggleProps) {
  return (
    <label
      htmlFor={id}
      className={`inline-flex items-center gap-2 text-sm text-ink ${
        disabled ? "opacity-60" : "cursor-pointer"
      }`}
    >
      <span className="relative inline-flex">
        <input
          id={id}
          type="checkbox"
          className="peer size-4 appearance-none rounded-full border border-line bg-canvas transition checked:border-brand checked:bg-brand disabled:cursor-not-allowed"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span
          aria-hidden="true"
          className="pointer-events-none absolute left-1 top-1 size-2 rounded-full bg-surface opacity-0 transition peer-checked:opacity-100"
        />
      </span>
      {label}
    </label>
  );
}
