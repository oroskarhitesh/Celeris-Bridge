/**
 * Whether a source is actually answering questions.
 *
 * Three states and they are not a progress bar: ``empty`` is *finished*. The
 * document was read and there was no text in it -- a scanned contract with no
 * text layer is the usual case -- and the only thing that changes it is OCR
 * or a different file. Drawing that as "pending" would leave someone waiting
 * for a job that already ran, which is why the backend distinguishes them and
 * why this does too.
 */

import type { SourceStatus } from "@/data/types/knowledge";

const LOOKS: Record<SourceStatus, string> = {
  indexed: "bg-good/10 text-good border-good/30",
  empty: "bg-warn/10 text-warn border-warn/40",
  pending: "bg-canvas text-ink-soft border-line",
};

const WORDS: Record<SourceStatus, string> = {
  indexed: "Indexed",
  empty: "No text",
  pending: "Not indexed",
};

const WHY: Record<SourceStatus, string> = {
  indexed: "Passages from this source can be retrieved.",
  empty: "It was read and contained no text — a blank page, or a scan needing OCR.",
  pending: "Nothing has been indexed from it yet.",
};

export function SourceStatusBadge({ status }: { status: SourceStatus }) {
  return (
    <span
      title={WHY[status]}
      className={`rounded-full border px-2 py-0.5 text-xs font-medium ${LOOKS[status]}`}
    >
      {WORDS[status]}
    </span>
  );
}
