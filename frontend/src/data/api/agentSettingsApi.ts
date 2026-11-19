/**
 * One agent's settings: the generated form, and the two ways to change it.
 *
 * **The form is a read, not a schema.** It arrives with every setting's
 * declaration, its resolved value, and whether this workspace is the one that
 * chose it. Nothing is cached beyond the query cache and nothing is derived
 * at build time, so the day the platform publishes a template version with a
 * new setting, the form grows a control without this file changing.
 *
 * **``edit`` is a patch and ``reset`` is a deletion.** They are not two
 * spellings of the same thing. Writing a value equal to the default *drops*
 * the override, because the backend stores a delta -- so a field's "use the
 * default" needs no request of its own, and ``reset`` exists for the one
 * thing a patch cannot say: drop everything, including the abilities'.
 *
 * Both invalidate the form as well as the agent. The form carries
 * ``overridden`` per setting, which is exactly what an edit changes.
 */

import { baseApi } from "@/data/client/baseApi";
import { LIST } from "@/data/client/tags";
import type { AppDispatch } from "@/data/store/store";
import type { Agent, EditAgentBody, ResetAgentBody } from "@/data/types/agent";
import type { SettingsForm } from "@/data/types/settings";

import { cacheAgent } from "./agentsApi";

/** What a write to the draft touches: the agent, the list, and the form. */
function touched(id: string) {
  return [
    { type: "Agent" as const, id },
    { type: "Agent" as const, id: LIST },
    { type: "SettingsForm" as const, id },
  ];
}

interface Written {
  dispatch: AppDispatch;
  queryFulfilled: Promise<{ data: Agent }>;
}

async function writeBack(
  _body: unknown,
  { dispatch, queryFulfilled }: Written,
): Promise<void> {
  try {
    const { data } = await queryFulfilled;
    cacheAgent(dispatch, data);
  } catch {
    // Refused: the error belongs to the caller, and the tags fetch the truth.
  }
}

export const agentSettingsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    settingsForm: build.query<SettingsForm, string>({
      query: (id) => `/v1/agents/${id}/settings`,
      providesTags: (_form, _error, id) => [{ type: "SettingsForm" as const, id }],
    }),

    editAgent: build.mutation<Agent, EditAgentBody>({
      query: ({ id, ...body }) => ({
        url: `/v1/agents/${id}`,
        method: "PATCH",
        body,
      }),
      onQueryStarted: writeBack,
      invalidatesTags: (_agent, _error, { id }) => touched(id),
    }),

    resetAgent: build.mutation<Agent, ResetAgentBody>({
      query: ({ id, ...body }) => ({
        url: `/v1/agents/${id}/reset`,
        method: "POST",
        body,
      }),
      onQueryStarted: writeBack,
      invalidatesTags: (_agent, _error, { id }) => touched(id),
    }),
  }),
});

export const { useSettingsFormQuery, useEditAgentMutation, useResetAgentMutation } =
  agentSettingsApi;
