import type { InputHTMLAttributes } from "react";

interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
  problem?: string;
}

export function TextInput({ label, hint, problem, id, ...rest }: TextInputProps) {
  const fieldId = id ?? `field-${label.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <div className="space-y-1">
      <label htmlFor={fieldId} className="block text-sm font-medium text-ink">
        {label}
      </label>
      <input
        {...rest}
        id={fieldId}
        aria-invalid={problem ? true : undefined}
        aria-describedby={problem ? `${fieldId}-problem` : undefined}
        className={`w-full rounded-lg border bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-brand ${
          problem ? "border-bad" : "border-line"
        }`}
      />
      {problem ? (
        <p id={`${fieldId}-problem`} className="text-xs text-bad">
          {problem}
        </p>
      ) : null}
      {!problem && hint ? <p className="text-xs text-ink-soft">{hint}</p> : null}
    </div>
  );
}
