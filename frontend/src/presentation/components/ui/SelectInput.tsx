import type { SelectHTMLAttributes } from "react";

interface SelectInputProps extends SelectHTMLAttributes<HTMLSelectElement> {
  choices: { value: string; label: string }[];
  problem?: boolean;
}

export function SelectInput({
  choices,
  problem,
  className = "",
  ...rest
}: SelectInputProps) {
  return (
    <select
      {...rest}
      aria-invalid={problem ? true : undefined}
      className={`w-full rounded-lg border bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-brand ${
        problem ? "border-bad" : "border-line"
      } ${className}`}
    >
      {choices.map((one) => (
        <option key={one.value} value={one.value}>
          {one.label}
        </option>
      ))}
    </select>
  );
}
