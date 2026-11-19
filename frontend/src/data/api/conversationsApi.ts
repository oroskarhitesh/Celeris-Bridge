/**
 * Reading what the agents have said.
 *
 * Both endpoints are queries and there is nothing else: the admin API has no
 * way to change a conversation, which is right -- a transcript is a record of
 * something that happened, and an API that could edit one would be an API
 * that could make it disagree with the model calls it was billed for.
 *
 * Two decisions worth stating.
 *
 * **The transcript is its own cache entry, keyed by id.** The list sends
 * summaries -- counts and totals, no turns -- so a transcript cannot be built
 * from the list, and a page that reads one should not have to load fifty.
 * Opening the same conversation twice costs nothing the second time.
 *
 * **Both are tagged, although nothing in this file invalidates anything.**
 * The one thing that creates a conversation from the admin side is the draft
 * preview, which lives in ``agentPreviewApi`` -- so the invalidation belongs
 * there and the tag has to exist here for it to land on. A tag provided by a
 * query nobody invalidates is dead weight; this one has exactly one writer.
 */

import { baseApi } from "@/data/client/baseApi";
import { LIST } from "@/data/client/tags";
import type {
  ConversationFilter,
  ConversationSummary,
  Transcript,
} from "@/data/types/conversation";

export const conversationsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    conversations: build.query<ConversationSummary[], ConversationFilter | void>({
      query: (filter) => ({
        url: "/v1/conversations",
        // Only what was asked for. An empty string would be sent as a real
        // value and filter the list down to agents whose id is "".
        params: {
          ...(filter?.instanceId ? { instanceId: filter.instanceId } : {}),
          ...(filter?.channel ? { channel: filter.channel } : {}),
          ...(filter?.limit ? { limit: filter.limit } : {}),
        },
      }),
      providesTags: (conversations) => [
        { type: "Conversation" as const, id: LIST },
        ...(conversations ?? []).map((one) => ({
          type: "Conversation" as const,
          id: one.id,
        })),
      ],
    }),

    transcript: build.query<Transcript, string>({
      query: (id) => `/v1/conversations/${id}`,
      providesTags: (_transcript, _error, id) => [
        { type: "Conversation" as const, id },
      ],
    }),
  }),
});

export const { useConversationsQuery, useTranscriptQuery } = conversationsApi;
