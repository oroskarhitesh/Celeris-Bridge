import type { TextareaHTMLAttributes } from "react";

export function TextArea({
  problem,
  className = "",
  ...rest
}: TextareaHTMLAttributes<HTMLTextAreaElement> & { problem?: boolean }) {
  return (
    <textarea
      {...rest}
      rows={rest.rows ?? 4}
      aria-invalid={problem ? true : undefined}
      className={`w-full rounded-lg border bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-brand ${
        problem ? "border-bad" : "border-line"
      } ${className}`}
    />
  );
}
