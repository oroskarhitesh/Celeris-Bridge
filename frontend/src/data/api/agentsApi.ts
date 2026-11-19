/**
 * The agents of one workspace: reading them, creating one, and the three
 * state changes that need no form (pause, resume, delete).
 *
 * Two things in here are not obvious and both are about the cache.
 *
 * **Every mutation returns the fresh agent, and it is written into the cache
 * by hand before the tags are invalidated.** Writes carry
 * ``expectRevision``, so a screen holding a stale ``draftRevision`` gets a
 * 409 on its *next* action. Waiting for a refetch to land would leave a
 * window where the button is armed with the wrong number -- updating the
 * cache from the response closes it.
 *
 * **Creating or deleting invalidates the catalogue.** The catalogue carries
 * ``owned`` against ``maxPerWorkspace``, which is how the new-agent screen
 * disables a type instead of explaining a 409 afterwards. That count is wrong
 * the moment an agent appears, and nothing else would tell it.
 */

import { baseApi } from "@/data/client/baseApi";
import { LIST } from "@/data/client/tags";
import type { AppDispatch } from "@/data/store/store";
import type { Agent, AgentFilter, CreateAgentBody, Revised } from "@/data/types/agent";

export const agentsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    agents: build.query<Agent[], AgentFilter | void>({
      query: (filter) => ({
        url: "/v1/agents",
        params: {
          ...(filter?.templateKey ? { templateKey: filter.templateKey } : {}),
          ...(filter?.status ? { status: filter.status } : {}),
        },
      }),
      providesTags: (agents) => [
        { type: "Agent" as const, id: LIST },
        ...(agents ?? []).map((one) => ({ type: "Agent" as const, id: one.id })),
      ],
    }),

    agent: build.query<Agent, string>({
      query: (id) => `/v1/agents/${id}`,
      providesTags: (_agent, _error, id) => [{ type: "Agent" as const, id }],
    }),

    createAgent: build.mutation<Agent, CreateAgentBody>({
      query: (body) => ({ url: "/v1/agents", method: "POST", body }),
      invalidatesTags: [{ type: "Agent", id: LIST }, "Catalogue"],
    }),

    pauseAgent: build.mutation<Agent, Revised>({
      query: ({ id, expectRevision }) => ({
        url: `/v1/agents/${id}/pause`,
        method: "POST",
        body: { expectRevision },
      }),
      onQueryStarted: writeBack,
      invalidatesTags: (_agent, _error, { id }) => [
        { type: "Agent" as const, id },
        { type: "Agent" as const, id: LIST },
      ],
    }),

    resumeAgent: build.mutation<Agent, Revised>({
      query: ({ id, expectRevision }) => ({
        url: `/v1/agents/${id}/resume`,
        method: "POST",
        body: { expectRevision },
      }),
      onQueryStarted: writeBack,
      invalidatesTags: (_agent, _error, { id }) => [
        { type: "Agent" as const, id },
        { type: "Agent" as const, id: LIST },
      ],
    }),

    deleteAgent: build.mutation<Agent, Revised>({
      // The revision is a query parameter because a DELETE has no body.
      query: ({ id, expectRevision }) => ({
        url: `/v1/agents/${id}`,
        method: "DELETE",
        params: { expectRevision },
      }),
      invalidatesTags: (_agent, _error, { id }) => [
        { type: "Agent" as const, id },
        { type: "Agent" as const, id: LIST },
        "Catalogue",
      ],
    }),
  }),
});

interface WriteBackApi {
  dispatch: AppDispatch;
  queryFulfilled: Promise<{ data: Agent }>;
}

/**
 * Put the agent the server returned into both caches that hold one.
 *
 * Not an optimistic update: nothing is guessed, and nothing is written until
 * the server has answered. It only stops the screen from showing a revision
 * it already knows is old.
 */
async function writeBack(
  _body: Revised,
  { dispatch, queryFulfilled }: WriteBackApi,
): Promise<void> {
  try {
    const { data } = await queryFulfilled;
    cacheAgent(dispatch, data);
  } catch {
    // A refused write changes nothing here. The error is the caller's to
    // show, and the invalidated tags will refetch the truth.
  }
}

/**
 * Put one agent into both cache entries that hold one.
 *
 * Exported because the writes that are *not* in this file -- editing
 * settings, publishing, rolling back, upgrading -- all answer with the fresh
 * agent too, and all have the same reason to want it in the cache before the
 * refetch lands: the next action's ``expectRevision`` is read off it.
 *
 * Not an optimistic update. Nothing is guessed and nothing is written until
 * the server has answered; this only stops a screen from showing a revision
 * it already knows is old.
 */
export function cacheAgent(dispatch: AppDispatch, agent: Agent): void {
  dispatch(
    agentsApi.util.updateQueryData("agent", agent.id, (draft) =>
      Object.assign(draft, agent),
    ),
  );
  dispatch(
    agentsApi.util.updateQueryData("agents", undefined, (draft) => {
      const at = draft.findIndex((one) => one.id === agent.id);
      if (at >= 0) draft[at] = agent;
    }),
  );
}

export const {
  useAgentsQuery,
  useAgentQuery,
  useCreateAgentMutation,
  usePauseAgentMutation,
  useResumeAgentMutation,
  useDeleteAgentMutation,
} = agentsApi;
