/**
 * The two filters that are the leads screen's own: the verdict, and whether
 * to show people who shared nothing.
 *
 * The agent and the channel are filtered by the shared control -- the leads
 * list and the conversations list are two views of the same records and
 * narrow them by the same two fields, so there is one control for it.
 *
 * **Showing everyone is off by default, and that disagrees with the API on
 * purpose.** The server's ``withDetailsOnly`` defaults to false; this screen
 * asks for true. A public widget has far more conversations than leads, and a
 * list that includes every passer-by who said "hi" buries the ones worth
 * calling. The header says which it is showing and the choice is in the URL,
 * so it is a default rather than a hidden filter.
 */

import { useSearchParams } from "react-router-dom";

const STATUSES = [
  { value: "", label: "Any verdict" },
  { value: "qualified", label: "Qualified" },
  { value: "qualifying", label: "Qualifying" },
  { value: "exploring", label: "Exploring" },
  { value: "not_a_fit", label: "Not a fit" },
];

/** True when the screen should ask for conversations with no details too. */
export function showsEveryone(params: URLSearchParams): boolean {
  return params.get("everyone") === "1";
}

export function LeadFilters() {
  const [params, setParams] = useSearchParams();

  function set(key: string, value: string): void {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  return (
    <>
      <div className="space-y-1">
        <label
          htmlFor="filter-status"
          className="block text-xs font-medium text-ink-soft"
        >
          Verdict
        </label>
        <select
          id="filter-status"
          value={params.get("status") ?? ""}
          onChange={(event) => set("status", event.target.value)}
          className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink outline-none focus:border-brand"
        >
          {STATUSES.map((one) => (
            <option key={one.value || "any"} value={one.value}>
              {one.label}
            </option>
          ))}
        </select>
      </div>

      <label className="flex items-center gap-2 pb-1.5 text-sm text-ink">
        <input
          type="checkbox"
          checked={showsEveryone(params)}
          onChange={(event) => set("everyone", event.target.checked ? "1" : "")}
          className="size-4 rounded border-line text-brand focus:ring-brand"
        />
        Include conversations with no details
      </label>
    </>
  );
}
