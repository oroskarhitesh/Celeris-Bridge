/**
 * Tabs that live in the address bar.
 *
 * The chosen tab is a search parameter rather than state, for the same reason
 * the agents list reads its filter from the URL: a link to someone's settings
 * tab should open the settings tab, and going back should go back a tab.
 */

import { Link, useSearchParams } from "react-router-dom";

export interface Tab {
  key: string;
  label: string;
  /** A short count or flag drawn next to the label. */
  note?: string;
}

interface TabsProps {
  tabs: Tab[];
  /** The parameter to carry the choice in, and its value when absent. */
  param?: string;
  current: string;
}

export function Tabs({ tabs, param = "tab", current }: TabsProps) {
  const [params] = useSearchParams();

  return (
    <div role="tablist" className="flex gap-1 border-b border-line">
      {tabs.map((tab) => {
        const next = new URLSearchParams(params);
        next.set(param, tab.key);
        const here = tab.key === current;
        return (
          <Link
            key={tab.key}
            to={{ search: "?" + next.toString() }}
            role="tab"
            aria-selected={here}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition ${
              here
                ? "border-brand text-ink"
                : "border-transparent text-ink-soft hover:text-ink"
            }`}
          >
            {tab.label}
            {tab.note ? (
              <span className="ml-1.5 rounded-full bg-brand-soft px-1.5 py-0.5 text-xs text-brand">
                {tab.note}
              </span>
            ) : null}
          </Link>
        );
      })}
    </div>
  );
}
