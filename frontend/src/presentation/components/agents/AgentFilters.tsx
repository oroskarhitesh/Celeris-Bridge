/**
 * Filter by status, kept in the URL rather than in a slice.
 *
 * The URL already is a place to keep this: it survives a reload and it can be
 * sent to somebody. A slice holding it would be a second source of truth for
 * something the router is for.
 */

import { useSearchParams } from "react-router-dom";

import type { AgentStatus } from "@/data/types/agent";

const CHOICES: Array<{ value: AgentStatus | ""; label: string }> = [
  { value: "", label: "All" },
  { value: "live", label: "Live" },
  { value: "draft", label: "Drafts" },
  { value: "paused", label: "Paused" },
];

export function AgentFilters() {
  const [params, setParams] = useSearchParams();
  const current = params.get("status") ?? "";

  return (
    <div className="inline-flex rounded-lg border border-line bg-surface p-0.5">
      {CHOICES.map((choice) => {
        const active = current === choice.value;
        return (
          <button
            key={choice.value || "all"}
            type="button"
            aria-pressed={active}
            onClick={() => {
              const next = new URLSearchParams(params);
              if (choice.value) next.set("status", choice.value);
              else next.delete("status");
              setParams(next, { replace: true });
            }}
            className={
              "rounded-md px-3 py-1 text-sm font-medium transition " +
              (active ? "bg-brand-soft text-brand" : "text-ink-soft hover:text-ink")
            }
          >
            {choice.label}
          </button>
        );
      })}
    </div>
  );
}
