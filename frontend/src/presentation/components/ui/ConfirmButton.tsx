/**
 * A destructive action that takes two clicks, not a modal.
 *
 * The second click is the confirmation, and it is labelled with what will
 * happen rather than "OK" -- the useful property of a confirmation is that it
 * says what is about to be done, which a dialog full of chrome often does
 * worse.
 */

import { useEffect, useState } from "react";

import { Button } from "./Button";

interface ConfirmButtonProps {
  label: string;
  confirmLabel: string;
  busy?: boolean;
  disabled?: boolean;
  onConfirm: () => void;
}

export function ConfirmButton({
  label,
  confirmLabel,
  busy = false,
  disabled = false,
  onConfirm,
}: ConfirmButtonProps) {
  const [armed, setArmed] = useState(false);

  // Disarms itself, so a page left open does not keep a destructive button
  // one click away for the rest of the afternoon.
  useEffect(() => {
    if (!armed) return;
    const timer = window.setTimeout(() => setArmed(false), 5000);
    return () => window.clearTimeout(timer);
  }, [armed]);

  if (!armed) {
    return (
      <Button look="danger" disabled={disabled} onClick={() => setArmed(true)}>
        {label}
      </Button>
    );
  }

  return (
    <span className="inline-flex items-center gap-1">
      <Button
        look="danger"
        busy={busy}
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
      >
        {confirmLabel}
      </Button>
      <Button look="ghost" onClick={() => setArmed(false)}>
        Keep
      </Button>
    </span>
  );
}
