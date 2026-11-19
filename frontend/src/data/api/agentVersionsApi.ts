/**
 * Versions: the history, publishing, rolling back, and moving to a newer
 * agent type.
 *
 * Four operations that look unrelated and are one subject -- each of them
 * either creates a version or changes which one a visitor is served.
 *
 * **Publishing and rolling back both answer with a version *and* an agent**,
 * so both write the agent into the cache and both invalidate the history.
 * Rolling back is itself a publish: nothing is edited or deleted, a new
 * version carries the old overrides and records where they came from, which
 * is why the history is the one place that shows it happened.
 *
 * **The upgrade preview is a read that writes nothing**, and it is a separate
 * endpoint from the upgrade for a reason worth keeping: a workspace about to
 * lose a setting it configured should be shown which one and allowed to say
 * no. It is tagged against the settings form because what it would drop
 * depends on the draft's overrides -- edit a setting and the answer changes.
 */

import { baseApi } from "@/data/client/baseApi";
import { LIST } from "@/data/client/tags";
import type { AppDispatch } from "@/data/store/store";
import type {
  Agent,
  PublishAgentBody,
  RollbackAgentBody,
  UpgradeAgentBody,
} from "@/data/types/agent";
import type { AgentVersion, Published, UpgradePreview } from "@/data/types/settings";

import { cacheAgent } from "./agentsApi";

function touched(id: string) {
  return [
    { type: "Agent" as const, id },
    { type: "Agent" as const, id: LIST },
    { type: "SettingsForm" as const, id },
    { type: "AgentVersion" as const, id },
  ];
}

interface Publishing {
  dispatch: AppDispatch;
  queryFulfilled: Promise<{ data: Published }>;
}

async function cachePublished(
  _body: unknown,
  { dispatch, queryFulfilled }: Publishing,
): Promise<void> {
  try {
    const { data } = await queryFulfilled;
    cacheAgent(dispatch, data.agent);
  } catch {
    // Refused: the tags will fetch whatever the truth turned out to be.
  }
}

interface Upgrading {
  dispatch: AppDispatch;
  queryFulfilled: Promise<{ data: Agent }>;
}

export const agentVersionsApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    agentVersions: build.query<AgentVersion[], string>({
      query: (id) => `/v1/agents/${id}/versions`,
      providesTags: (_versions, _error, id) => [{ type: "AgentVersion" as const, id }],
    }),

    upgradePreview: build.query<UpgradePreview, { id: string; toVersion?: number }>({
      query: ({ id, toVersion }) => ({
        url: `/v1/agents/${id}/upgrade`,
        ...(toVersion ? { params: { toVersion } } : {}),
      }),
      providesTags: (_preview, _error, { id }) => [
        { type: "SettingsForm" as const, id },
      ],
    }),

    publishAgent: build.mutation<Published, PublishAgentBody>({
      query: ({ id, ...body }) => ({
        url: `/v1/agents/${id}/publish`,
        method: "POST",
        body,
      }),
      onQueryStarted: cachePublished,
      invalidatesTags: (_published, _error, { id }) => touched(id),
    }),

    rollbackAgent: build.mutation<Published, RollbackAgentBody>({
      query: ({ id, ...body }) => ({
        url: `/v1/agents/${id}/rollback`,
        method: "POST",
        body,
      }),
      onQueryStarted: cachePublished,
      invalidatesTags: (_published, _error, { id }) => touched(id),
    }),

    upgradeAgent: build.mutation<Agent, UpgradeAgentBody>({
      query: ({ id, ...body }) => ({
        url: `/v1/agents/${id}/upgrade`,
        method: "POST",
        body,
      }),
      onQueryStarted: async (_body, { dispatch, queryFulfilled }: Upgrading) => {
        try {
          const { data } = await queryFulfilled;
          cacheAgent(dispatch, data);
        } catch {
          /* the tags will correct it */
        }
      },
      // An upgrade moves the draft onto another template version, so the
      // whole form is different afterwards: different settings, different
      // abilities, different locked rules.
      invalidatesTags: (_agent, _error, { id }) => touched(id),
    }),
  }),
});

export const {
  useAgentVersionsQuery,
  useUpgradePreviewQuery,
  usePublishAgentMutation,
  useRollbackAgentMutation,
  useUpgradeAgentMutation,
} = agentVersionsApi;
