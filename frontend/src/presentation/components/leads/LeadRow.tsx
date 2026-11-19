/**
 * One lead, as a row of the table.
 *
 * The detail columns are handed in rather than worked out here, because they
 * are a property of the whole list: which details an agent collects is a
 * setting, so the columns come from the data and every row has to use the
 * same ones. A lead with nothing under a column gets an em dash, which is
 * the point of the column existing -- an empty cell in a row of full ones is
 * the follow-up.
 *
 * ``missing`` is shown in warn colour and ``reasons`` in plain words under
 * the verdict. They answer different questions: missing is what to ask next,
 * reasons is why not to bother.
 */

import { Link } from "react-router-dom";

import type { Lead } from "@/data/types/lead";
import { moment } from "@/presentation/format";
import { channelLabel } from "@/presentation/components/conversations/channels";

import { LeadStatusBadge } from "./LeadStatusBadge";

interface LeadRowProps {
  lead: Lead;
  /** The detail keys this table has columns for, in order. */
  columns: string[];
}

export function LeadRow({ lead, columns }: LeadRowProps) {
  return (
    <tr className="border-t border-line align-top">
      <td className="px-3 py-3">
        <LeadStatusBadge status={lead.status} />
        {lead.visitorType && lead.visitorType !== "unknown" ? (
          <p className="mt-1 text-xs text-ink-soft">{lead.visitorType}</p>
        ) : null}
        {lead.reasons.length ? (
          <ul className="mt-1 space-y-0.5">
            {lead.reasons.map((reason) => (
              <li key={reason} className="text-xs text-ink-soft">
                {reason}
              </li>
            ))}
          </ul>
        ) : null}
      </td>

      {columns.map((key) => (
        <td key={key} className="px-3 py-3 text-sm text-ink">
          {lead.details[key] ? (
            lead.details[key]
          ) : (
            <span className="text-ink-soft">—</span>
          )}
        </td>
      ))}

      <td className="px-3 py-3 text-xs">
        {lead.missing.length ? (
          <span className="text-warn">{lead.missing.join(", ")}</span>
        ) : (
          <span className="text-ink-soft">
            {/* Nothing missing is only good news if something was asked for.
                An empty record has nothing missing either. */}
            {lead.complete ? "nothing" : "—"}
          </span>
        )}
      </td>

      <td className="max-w-xs px-3 py-3 text-sm text-ink-soft">
        <span className="line-clamp-2" title={lead.openingMessage}>
          {lead.openingMessage || "—"}
        </span>
      </td>

      <td className="px-3 py-3 text-xs text-ink-soft">
        <p>{channelLabel(lead.channel)}</p>
        <p>{lead.turns} turns</p>
      </td>

      <td className="whitespace-nowrap px-3 py-3 text-xs text-ink-soft">
        {moment(lead.lastAt)}
      </td>

      <td className="px-3 py-3">
        <Link
          to={"/conversations/" + lead.conversationId}
          className="whitespace-nowrap text-xs font-medium text-brand hover:underline"
        >
          Transcript
        </Link>
      </td>
    </tr>
  );
}
