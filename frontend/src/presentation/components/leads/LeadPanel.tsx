/**
 * What was learned in this conversation, beside the words that produced it.
 *
 * The transcript screen is the only place the two routers meet, and this is
 * why it is worth a second request: a transcript answers "what was said" and
 * this answers "what came of it", and the two together are how somebody
 * decides whether the agent asked the right questions.
 *
 * A conversation nobody shared anything in is not an error -- the backend
 * answers with an empty record, ``exploring``, nothing missing -- so this
 * panel has a real empty state rather than a failure.
 *
 * Nothing here is editable, and that is the API: a lead is a conversation,
 * there is no lead record to patch, and the verdict was recorded when the
 * visitor said the thing that produced it.
 */

import type { Lead } from "@/data/types/lead";
import { Card } from "@/presentation/components/ui/Card";

import { LeadStatusBadge } from "./LeadStatusBadge";

export function LeadPanel({ lead }: { lead: Lead }) {
  const given = Object.entries(lead.details);

  return (
    <Card
      title="What was learned"
      description="Recorded when the visitor said it, against the settings in force at the time."
      aside={<LeadStatusBadge status={lead.status} />}
    >
      <div className="space-y-4">
        {given.length ? (
          <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
            {given.map(([key, value]) => (
              <div key={key}>
                <dt className="text-xs text-ink-soft">{key}</dt>
                <dd className="text-sm text-ink">{value}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-sm text-ink-soft">
            Nothing was shared in this conversation.
          </p>
        )}

        {lead.visitorType && lead.visitorType !== "unknown" ? (
          <p className="text-sm text-ink-soft">
            Described as <span className="text-ink">{lead.visitorType}</span>.
          </p>
        ) : null}

        {lead.missing.length ? (
          <p className="text-sm text-warn">
            Still missing: <span className="font-medium">{lead.missing.join(", ")}</span>
          </p>
        ) : null}

        {lead.reasons.length ? (
          <ul className="space-y-1 text-sm text-ink-soft">
            {lead.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </Card>
  );
}
