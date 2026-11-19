/**
 * Reading the leads.
 *
 * Two queries, no mutations, because the API has none: a lead is a
 * conversation that produced details, and creating one by hand would be
 * creating a conversation that never happened.
 *
 * ``lead`` -- one conversation's details -- exists for the transcript screen,
 * which shows the words and what was learned from them side by side. It is
 * the only place the two routers meet, and it is worth the second request:
 * the alternative is finding the row in a list the transcript page has no
 * reason to have loaded.
 *
 * A lead for a conversation nobody shared anything in is not an error. The
 * backend answers with an empty record -- no details, ``exploring``, nothing
 * missing -- because "forty conversations, three leads" is the number a
 * workspace needs to see. Only an unknown conversation is a 404.
 */

import { baseApi } from "@/data/client/baseApi";
import { LIST } from "@/data/client/tags";
import type { Lead, LeadFilter } from "@/data/types/lead";

export const leadsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    leads: build.query<Lead[], LeadFilter | void>({
      query: (filter) => ({
        url: "/v1/leads",
        params: {
          ...(filter?.instanceId ? { instanceId: filter.instanceId } : {}),
          ...(filter?.channel ? { channel: filter.channel } : {}),
          ...(filter?.status ? { status: filter.status } : {}),
          // Sent only when true. The server's default is false, so leaving
          // it out is the same request as sending false -- and a URL that
          // says "withDetailsOnly=false" reads like a filter being applied.
          ...(filter?.withDetailsOnly ? { withDetailsOnly: true } : {}),
          ...(filter?.limit ? { limit: filter.limit } : {}),
        },
      }),
      providesTags: (leads) => [
        { type: "Lead" as const, id: LIST },
        ...(leads ?? []).map((one) => ({
          type: "Lead" as const,
          id: one.conversationId,
        })),
      ],
    }),

    lead: build.query<Lead, string>({
      query: (conversationId) => `/v1/leads/${conversationId}`,
      providesTags: (_lead, _error, conversationId) => [
        { type: "Lead" as const, id: conversationId },
      ],
    }),
  }),
});

export const { useLeadsQuery, useLeadQuery } = leadsApi;
