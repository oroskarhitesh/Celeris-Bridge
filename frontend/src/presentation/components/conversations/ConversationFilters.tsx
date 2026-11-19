/**
 * Narrow the list by agent and by channel, in the URL.
 *
 * Both are the server's filters, and both are kept in the address bar for the
 * same reason the agents list keeps its status there: a link to "everything
 * the support agent said on WhatsApp" should open that, and going back should
 * go back a filter.
 *
 * The agent choices are passed in rather than fetched here. A conversation
 * carries an ``instanceId`` and nothing else -- no label -- so some screen
 * has to join the two, and the page that already needs the agents for its
 * rows is the one that should own that query.
 */

import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

import { CHANNELS } from "./channels";

export interface AgentChoice {
  id: string;
  label: string;
}

interface ConversationFiltersProps {
  agents: AgentChoice[];
  /** Drawn at the right of the row: a count, usually. */
  note?: string;
  /** Extra controls for a screen with one of its own. */
  children?: ReactNode;
}

export function ConversationFilters({
  agents,
  note,
  children,
}: ConversationFiltersProps) {
  const [params, setParams] = useSearchParams();

  function choose(key: string, value: string): void {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex flex-wrap items-end gap-4">
        <div className="space-y-1">
          <label
            htmlFor="filter-agent"
            className="block text-xs font-medium text-ink-soft"
          >
            Agent
          </label>
          <select
            id="filter-agent"
            value={params.get("agent") ?? ""}
            onChange={(event) => choose("agent", event.target.value)}
            className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-brand"
          >
            <option value="">Every agent</option>
            {agents.map((one) => (
              <option key={one.id} value={one.id}>
                {one.label}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-1">
          <label
            htmlFor="filter-channel"
            className="block text-xs font-medium text-ink-soft"
          >
            Channel
          </label>
          <select
            id="filter-channel"
            value={params.get("channel") ?? ""}
            onChange={(event) => choose("channel", event.target.value)}
            className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-brand"
          >
            <option value="">Every channel</option>
            {CHANNELS.map((one) => (
              <option key={one.value} value={one.value}>
                {one.label}
              </option>
            ))}
          </select>
        </div>

        {children}
      </div>

      {note ? <p className="text-xs text-ink-soft">{note}</p> : null}
    </div>
  );
}
