export type Tone = "good" | "warn" | "bad" | "idle";

const TONES: Record<Tone, string> = {
  good: "bg-good",
  warn: "bg-warn",
  bad: "bg-bad",
  idle: "bg-ink-soft/40",
};

export function StatusDot({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink">
      <span className={`size-2 rounded-full ${TONES[tone]}`} aria-hidden="true" />
      {label}
    </span>
  );
}
