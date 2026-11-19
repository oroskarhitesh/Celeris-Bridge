/**
 * Who the agents have spoken to, and what came of it.
 *
 * The same records as the conversations screen, read the other way round:
 * that one is about what was said, this one about what was collected. Hence
 * the shared filter control -- agent and channel narrow both lists by the
 * same server-side fields -- plus two of this screen's own.
 *
 * **Conversations with no details are left out by default**, which disagrees
 * with the API's default on purpose: a public widget has far more
 * conversations than leads, and a row for every passer-by who said "hi"
 * buries the ones worth calling. The header says which it is showing and the
 * choice is a checkbox in the URL, so nothing is hidden silently.
 *
 * There is no "export" and no "mark as contacted". Both would need a write
 * the API does not have, and inventing one in the frontend would mean a state
 * that exists on one screen and nowhere else.
 */

import { useSearchParams } from "react-router-dom";

import { useAgentsQuery } from "@/data/api/agentsApi";
import { useLeadsQuery } from "@/data/api/leadsApi";
import type { LeadFilter } from "@/data/types/lead";
import { ConversationFilters } from "@/presentation/components/conversations/ConversationFilters";
import { LeadFilters, showsEveryone } from "@/presentation/components/leads/LeadFilters";
import { LeadsTable } from "@/presentation/components/leads/LeadsTable";
import { EmptyState } from "@/presentation/components/ui/EmptyState";

export function LeadsPage() {
  const [params] = useSearchParams();
  const agent = params.get("agent") ?? "";
  const channel = params.get("channel") ?? "";
  const status = params.get("status") ?? "";
  const everyone = showsEveryone(params);

  const filter: LeadFilter = {
    ...(agent ? { instanceId: agent } : {}),
    ...(channel ? { channel } : {}),
    ...(status ? { status } : {}),
    ...(everyone ? {} : { withDetailsOnly: true }),
  };

  const { data, error, isLoading } = useLeadsQuery(filter);
  const { data: agents } = useAgentsQuery();

  const leads = data ?? [];
  const qualified = leads.filter((one) => one.status === "qualified").length;
  const incomplete = leads.filter((one) => one.missing.length).length;
  const nothing = !isLoading && !error && leads.length === 0;
  const filtered = Boolean(agent || channel || status);

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <header>
        <h1 className="text-lg font-semibold text-ink">Leads</h1>
        <p className="text-sm text-ink-soft">
          {leads.length
            ? `${leads.length} ${leads.length === 1 ? "conversation" : "conversations"} · ${qualified} qualified` +
              (incomplete ? ` · ${incomplete} missing something` : "") +
              (everyone ? " · including people who shared nothing" : "")
            : "Conversations that produced details, and what is still missing from them."}
        </p>
      </header>

      <ConversationFilters
        agents={(agents ?? []).map((one) => ({ id: one.id, label: one.label }))}
      >
        <LeadFilters />
      </ConversationFilters>

      {nothing ? (
        <EmptyState
          title={
            filtered
              ? "Nothing matches that"
              : everyone
                ? "No conversations yet"
                : "Nobody has shared anything yet"
          }
          description={
            filtered
              ? "Clear the filters to see the rest."
              : everyone
                ? "A conversation appears here as soon as somebody opens the widget."
                : "Conversations are happening or they are not — tick the box to see the ones where nothing was collected."
          }
        />
      ) : (
        <LeadsTable leads={data} loading={isLoading} error={error} />
      )}
    </div>
  );
}
