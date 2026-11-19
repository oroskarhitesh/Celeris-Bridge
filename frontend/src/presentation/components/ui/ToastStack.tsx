/**
 * The toasts, read straight out of the store.
 *
 * Mounted once in the shell. Nothing passes toasts down as props, because the
 * code that raises them is usually a mutation handler several levels away.
 */

import { useAppDispatch, useAppSelector } from "@/data/store/hooks";
import { selectToasts, toastDismissed } from "@/data/store/slices/uiSlice";
import type { ToastKind } from "@/data/store/slices/uiSlice";

const KINDS: Record<ToastKind, string> = {
  success: "border-good/40 bg-good/10",
  error: "border-bad/40 bg-bad/10",
  info: "border-line bg-surface",
};

export function ToastStack() {
  const toasts = useAppSelector(selectToasts);
  const dispatch = useAppDispatch();
  if (!toasts.length) return null;
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 flex-col gap-2"
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`pointer-events-auto flex items-start justify-between gap-3 rounded-lg border px-4 py-3 text-sm shadow-sm ${KINDS[toast.kind]}`}
        >
          <span className="text-ink">{toast.message}</span>
          <button
            type="button"
            onClick={() => dispatch(toastDismissed(toast.id))}
            className="text-ink-soft hover:text-ink"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
