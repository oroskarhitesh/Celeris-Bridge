/**
 * The cache, on its own, without a screen in the way.
 *
 * One claim is worth testing here rather than through a page, because through
 * a page it is indistinguishable from a refetch arriving quickly: a mutation
 * writes the agent the server returned into the cache *itself*. The list
 * refetch is made to hang, so if the revision in the cache is the new one,
 * only the write-back can have put it there.
 */

import { describe, expect, it } from "vitest";

import { agentsApi } from "@/data/api/agentsApi";
import { makeStore } from "@/data/store/store";

import { fakeApi, hangs } from "../support/api";
import { anAgent } from "../support/fixtures";

const AGENTS = "/v1/agents";

function cached(store: ReturnType<typeof makeStore>) {
  return agentsApi.endpoints.agents.select(undefined)(store.getState()).data;
}

describe("a state change", () => {
  it("puts the answer in the cache without waiting for a refetch", async () => {
    const api = fakeApi({
      // The first read, then nothing: the invalidation refetch never lands.
      ["GET " + AGENTS]: [{ body: [anAgent({ draftRevision: 3 })] }, hangs()],
      "POST /v1/agents/agent_1/pause": {
        body: anAgent({ status: "paused", draftRevision: 4 }),
      },
    });
    const store = makeStore({ session: { token: "devtok" } });

    await store.dispatch(agentsApi.endpoints.agents.initiate(undefined));
    expect(cached(store)?.[0]?.draftRevision).toBe(3);

    await store.dispatch(
      agentsApi.endpoints.pauseAgent.initiate({ id: "agent_1", expectRevision: 3 }),
    );

    expect(cached(store)?.[0]?.draftRevision).toBe(4);
    expect(cached(store)?.[0]?.status).toBe("paused");
    // And it did try to re-read, which is what keeps the rest of the list
    // honest; it simply is not what fixed this entry.
    expect(api.to(AGENTS, "GET").length).toBe(2);
  });

  it("leaves the cache alone when the write was refused", async () => {
    fakeApi({
      ["GET " + AGENTS]: [{ body: [anAgent({ draftRevision: 3 })] }, hangs()],
      "POST /v1/agents/agent_1/pause": {
        status: 409,
        body: {
          error: {
            code: "REVISION_CONFLICT",
            message: "Expected revision 3, found 4",
            details: { expected: 3, found: 4 },
          },
        },
      },
    });
    const store = makeStore({ session: { token: "devtok" } });
    await store.dispatch(agentsApi.endpoints.agents.initiate(undefined));

    await store.dispatch(
      agentsApi.endpoints.pauseAgent.initiate({ id: "agent_1", expectRevision: 3 }),
    );

    // Nothing invented: the agent is as it was read, and the refetch is what
    // will correct it.
    expect(cached(store)?.[0]?.draftRevision).toBe(3);
    expect(cached(store)?.[0]?.status).toBe("live");
  });

  it("keeps a filtered list in its own cache entry", async () => {
    // RTK Query keys by the query argument, so two filters are two entries
    // and switching back does not refetch. The test is that the argument
    // actually reaches the URL.
    const api = fakeApi({ ["GET " + AGENTS]: { body: [] } });
    const store = makeStore({ session: { token: "devtok" } });

    await store.dispatch(agentsApi.endpoints.agents.initiate({ status: "paused" }));
    await store.dispatch(agentsApi.endpoints.agents.initiate({ templateKey: "x_v1" }));

    expect(api.to(AGENTS, "GET").map((one) => one.search)).toEqual([
      "?status=paused",
      "?templateKey=x_v1",
    ]);
  });
});
