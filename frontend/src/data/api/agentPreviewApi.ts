/**
 * Talking to the draft before anybody else can.
 *
 * Both endpoints are mutations and neither is a query, which is the one
 * decision in this file. A turn costs real tokens and appends to a stored
 * transcript, so it must never be something the cache can repeat on its own
 * -- a refetch on tab focus would be a second message to a model, billed,
 * and an admin watching a transcript grow by itself.
 *
 * For the same reason nothing here is cached. The conversation lives in the
 * component while the panel is open.
 *
 * Both do invalidate the conversations list, though, and that is the one tag
 * in this file: a preview is *stored* like any other conversation, flagged
 * rather than hidden, and it shows up on the conversations screen with its
 * tokens and its ability calls. Without this, trying a draft and then opening
 * that screen shows a list that is missing the thing just done -- and a
 * refetch of a list nobody is looking at costs nothing, because RTK Query
 * only refetches what something is subscribed to.
 *
 * What makes this a *preview* is the endpoint it calls: ``/preview/...``
 * resolves the **draft**, not the published snapshot. It previews the draft
 * as last saved, so unsaved edits on the screen are not in it -- the panel
 * says so rather than leaving someone to wonder why their new greeting did
 * not appear.
 */

import { baseApi } from "@/data/client/baseApi";
import { LIST } from "@/data/client/tags";
import type { PreviewConversation, Reply } from "@/data/types/settings";

export interface StartPreviewBody {
  id: string;
  pageUrl?: string;
  pageTitle?: string;
}

export interface SayBody {
  id: string;
  conversationId: string;
  message: string;
}

export const agentPreviewApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    startPreview: build.mutation<PreviewConversation, StartPreviewBody>({
      query: ({ id, ...body }) => ({
        url: `/v1/agents/${id}/preview/conversations`,
        method: "POST",
        body,
      }),
      invalidatesTags: [{ type: "Conversation", id: LIST }],
    }),

    sayToPreview: build.mutation<Reply, SayBody>({
      query: ({ id, conversationId, message }) => ({
        url: `/v1/agents/${id}/preview/conversations/${conversationId}/messages`,
        method: "POST",
        body: { message },
      }),
      // The turn counts and the token totals on that list are this
      // conversation's, and they moved.
      invalidatesTags: (_reply, _error, { conversationId }) => [
        { type: "Conversation" as const, id: conversationId },
        { type: "Conversation" as const, id: LIST },
      ],
    }),
  }),
});

export const { useStartPreviewMutation, useSayToPreviewMutation } = agentPreviewApi;
