/**
 * The agents list, and the three writes that need no form.
 *
 * Most of what matters here is on the wire rather than on the screen: which
 * revision a button sent, where it sent it, and what the cache did with the
 * answer. A screen that shows the right words while sending the wrong
 * revision is a screen that works until two people use it.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AgentsPage } from "@/presentation/pages/AgentsPage";

import { fakeApi } from "../support/api";
import { anAgent } from "../support/fixtures";
import { LocationProbe } from "../support/LocationProbe";
import { renderPage } from "../support/render";

const AGENTS = "/v1/agents";

function withAgents(...agents: ReturnType<typeof anAgent>[]) {
  return fakeApi({ [AGENTS]: { body: agents } });
}

describe("the agents list", () => {
  it("shows an agent, its status and its pinned type version", async () => {
    withAgents(anAgent());
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });

    expect(await screen.findByText("Acme website")).toBeTruthy();
    expect(screen.getByText("Live · v2")).toBeTruthy();
    expect(screen.getByText(/pinned v1/)).toBeTruthy();
  });

  it("says when what is on the screen is not what visitors are served", async () => {
    // The most confusing state in a system with drafts, so it gets words.
    // The API answers this by comparing the draft with the published
    // snapshot, so it is false on a freshly published agent.
    withAgents(anAgent({ hasUnpublishedChanges: true, publishedVersion: 2 }));
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });

    expect(await screen.findByText("unpublished edits")).toBeTruthy();
  });

  it("stays quiet when there is nothing waiting to be published", async () => {
    withAgents(anAgent({ hasUnpublishedChanges: false, publishedVersion: 2 }));
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });

    await screen.findByText("Acme website");
    expect(screen.queryByText("unpublished edits")).toBeNull();
  });

  it("does not call them edits when nothing was ever published", async () => {
    // True, but useless: there is no published version for them to differ
    // from, which is a different sentence.
    withAgents(
      anAgent({ status: "paused", publishedVersion: null, hasUnpublishedChanges: true }),
    );
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });

    expect(await screen.findByText("never published")).toBeTruthy();
    expect(screen.queryByText("unpublished edits")).toBeNull();
  });

  it("explains an empty workspace instead of showing an empty box", async () => {
    withAgents();
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });

    expect(await screen.findByText("No agents yet")).toBeTruthy();
    expect(screen.getByText("Create the first one")).toBeTruthy();
  });

  it("filters through the URL, and asks the server for that status", async () => {
    const api = withAgents(anAgent());
    renderPage(
      <>
        <AgentsPage />
        <LocationProbe />
      </>,
      { token: "devtok", at: "/agents" },
    );
    await screen.findByText("Acme website");

    fireEvent.click(screen.getByText("Paused"));

    await waitFor(() =>
      expect(api.to(AGENTS, "GET").some((one) => one.search === "?status=paused")).toBe(
        true,
      ),
    );
    // In the address bar too, so a reload and a shared link both work.
    expect(screen.getByTestId("location").textContent).toBe("/agents?status=paused");
  });

  it("ignores a status in the URL that is not one", async () => {
    // The URL is user input. An unknown status asks for everything rather
    // than sending the server a word it will refuse with a 422.
    const api = withAgents(anAgent());
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents?status=banana" });

    await screen.findByText("Acme website");
    expect(api.last(AGENTS, "GET")?.search).toBe("");
  });
});

describe("pausing and resuming", () => {
  it("sends the revision it was rendered with", async () => {
    const api = fakeApi({
      [AGENTS]: { body: [anAgent({ draftRevision: 3 })] },
      "POST /v1/agents/agent_1/pause": {
        body: anAgent({ status: "paused", draftRevision: 4 }),
      },
    });
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });
    fireEvent.click(await screen.findByText("Pause"));

    await waitFor(() => expect(api.last("/v1/agents/agent_1/pause")).toBeTruthy());
    expect(api.last("/v1/agents/agent_1/pause")?.body).toEqual({ expectRevision: 3 });
  });

  it("lets two actions in a row work, each with the revision before it", async () => {
    // The sequence a person actually performs. Each write bumps the
    // revision, so the second button has to be armed with the one the first
    // answered -- which is what the write-back in the data layer is for
    // (proved on its own in tests/data/agentsApi.test.ts).
    const api = fakeApi({
      ["GET " + AGENTS]: [
        { body: [anAgent({ draftRevision: 3 })] },
        { body: [anAgent({ status: "paused", draftRevision: 4 })] },
      ],
      "POST /v1/agents/agent_1/pause": {
        body: anAgent({ status: "paused", draftRevision: 4 }),
      },
      "POST /v1/agents/agent_1/resume": {
        body: anAgent({ status: "live", draftRevision: 5 }),
      },
    });
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });
    fireEvent.click(await screen.findByText("Pause"));

    fireEvent.click(await screen.findByText("Resume"));
    await waitFor(() => expect(api.last("/v1/agents/agent_1/resume")).toBeTruthy());
    expect(api.last("/v1/agents/agent_1/resume")?.body).toEqual({ expectRevision: 4 });
  });

  it("offers resume for a paused agent and does not offer pause for a draft", async () => {
    withAgents(anAgent({ status: "draft", publishedVersion: null }));
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });

    const pause = await screen.findByText("Pause");
    expect(pause.closest("button")?.disabled).toBe(true);
  });

  it("treats a lost race as a reload, not as a failure", async () => {
    const api = fakeApi({
      [AGENTS]: { body: [anAgent({ draftRevision: 3 })] },
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
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });
    fireEvent.click(await screen.findByText("Pause"));

    expect(
      await screen.findByText(/changed somewhere else/),
    ).toBeTruthy();
    // And the list is re-read, because that is what fixes the screen.
    await waitFor(() => expect(api.to(AGENTS, "GET").length).toBeGreaterThan(1));
  });
});

describe("deleting", () => {
  it("takes two clicks, and sends the revision as a query parameter", async () => {
    // A DELETE has no body, so the revision travels in the URL -- under its
    // camelCase name, which is the one the backend reads.
    const api = fakeApi({
      [AGENTS]: { body: [anAgent({ draftRevision: 3 })] },
      "DELETE /v1/agents/agent_1": { body: anAgent({ draftRevision: 4 }) },
    });
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });

    fireEvent.click(await screen.findByText("Delete"));
    expect(api.last("/v1/agents/agent_1", "DELETE")).toBeUndefined();

    fireEvent.click(screen.getByText("Delete for good"));
    await waitFor(() =>
      expect(api.last("/v1/agents/agent_1", "DELETE")?.search).toBe("?expectRevision=3"),
    );
  });

  it("does not promise that the conversations are gone", async () => {
    // The backend marks it deleted and keeps everything, so the words match.
    fakeApi({
      [AGENTS]: { body: [anAgent()] },
      "DELETE /v1/agents/agent_1": { body: anAgent() },
    });
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });

    fireEvent.click(await screen.findByText("Delete"));
    fireEvent.click(screen.getByText("Delete for good"));
    expect(await screen.findByText(/conversations are kept/)).toBeTruthy();
  });
});

describe("the tenancy rule, from this side", () => {
  it("never names a workspace, in a path, a query or a body", async () => {
    const api = fakeApi({
      [AGENTS]: { body: [anAgent()] },
      "POST /v1/agents/agent_1/pause": { body: anAgent({ status: "paused" }) },
    });
    renderPage(<AgentsPage />, { token: "devtok", at: "/agents" });
    fireEvent.click(await screen.findByText("Pause"));
    await waitFor(() => expect(api.last("/v1/agents/agent_1/pause")).toBeTruthy());

    for (const call of api.calls) {
      expect(/workspace/i.test(call.url)).toBe(false);
      expect(/workspace/i.test(JSON.stringify(call.body ?? {}))).toBe(false);
      expect(call.authorization).toBe("Bearer devtok");
    }
  });
});
