import type { ButtonHTMLAttributes, ReactNode } from "react";

import { Spinner } from "./Spinner";

type Look = "primary" | "secondary" | "danger" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  look?: Look;
  /** Shows a spinner and disables the button, so no caller does both. */
  busy?: boolean;
  children: ReactNode;
}

const LOOKS: Record<Look, string> = {
  primary: "bg-brand text-white hover:opacity-90",
  secondary: "border border-line bg-surface text-ink hover:bg-canvas",
  danger: "border border-bad/30 bg-surface text-bad hover:bg-bad/5",
  ghost: "text-ink-soft hover:bg-canvas hover:text-ink",
};

export function Button({
  look = "secondary",
  busy = false,
  disabled,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      {...rest}
      disabled={disabled ?? busy}
      className={`inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${LOOKS[look]} ${className}`}
    >
      {busy ? <Spinner /> : null}
      {children}
    </button>
  );
}
