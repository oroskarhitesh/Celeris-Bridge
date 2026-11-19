/**
 * The leads, as a table whose columns come from the data.
 *
 * This is the one real decision on the screen. A lead's ``details`` is a map
 * under the keys the workspace asked for -- a workspace that collects a
 * company size and no phone number is configured, not broken -- so a table
 * with fixed "Name" and "Email" headings would be wrong for anybody who
 * changed the setting, and showing the map as loose key/value pairs per row
 * would throw away the thing a table is for: reading one column down forty
 * rows to see who is missing a phone number.
 *
 * So the columns are the union of the keys that actually arrived, in the
 * order they were first seen, and a lead with nothing under one gets an em
 * dash. The header spells a key as a person would read it, and that is only
 * presentation: the key itself is what the agent collected under.
 *
 * It is given its data, like every other list here.
 */

import type { ApiError } from "@/data/client/errors";
import type { Lead } from "@/data/types/lead";
import { ErrorNotice } from "@/presentation/components/ui/ErrorNotice";
import { Spinner } from "@/presentation/components/ui/Spinner";

import { LeadRow } from "./LeadRow";

/**
 * The detail keys present across these leads, first-seen order.
 *
 * Order matters and alphabetical would be the wrong one: the keys arrive in
 * the order the workspace declared them, which is the order the agent asks
 * the questions in.
 */
export function columnsOf(leads: Lead[]): string[] {
  const seen: string[] = [];
  for (const lead of leads) {
    for (const key of Object.keys(lead.details)) {
      if (!seen.includes(key)) seen.push(key);
    }
  }
  return seen;
}

/** ``companySize`` as "Company size". The key stays the key. */
export function headingOf(key: string): string {
  const spaced = key.replace(/[_-]+/g, " ").replace(/([a-z\d])([A-Z])/g, "$1 $2");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

interface LeadsTableProps {
  leads: Lead[] | undefined;
  loading: boolean;
  error: ApiError | undefined;
}

export function LeadsTable({ leads, loading, error }: LeadsTableProps) {
  if (loading) return <Spinner label="Loading leads" />;
  if (error) return <ErrorNotice error={error} />;

  const rows = leads ?? [];
  const columns = columnsOf(rows);

  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-surface">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="text-xs font-medium text-ink-soft">
            <th scope="col" className="px-3 py-2">
              Verdict
            </th>
            {columns.map((key) => (
              <th key={key} scope="col" className="px-3 py-2">
                {headingOf(key)}
              </th>
            ))}
            <th scope="col" className="px-3 py-2">
              Still missing
            </th>
            <th scope="col" className="px-3 py-2">
              Why they got in touch
            </th>
            <th scope="col" className="px-3 py-2">
              Channel
            </th>
            <th scope="col" className="px-3 py-2">
              Last said
            </th>
            <th scope="col" className="px-3 py-2">
              <span className="sr-only">Transcript</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((lead) => (
            <LeadRow key={lead.conversationId} lead={lead} columns={columns} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
