/**
 * The conversations list.
 *
 * Two things are worth testing here and they are not the markup. One: the
 * filter goes to the *server*, under the server's field names, because the
 * list is cut off by a limit and filtering after it arrived would hide rows
 * that were inside it. Two: a conversation carries an agent id and no label,
 * so the join against the agents query is the screen's only real logic -- and
 * it has to degrade to the id rather than to nothing.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConversationsPage } from "@/presentation/pages/ConversationsPage";

import { fakeApi } from "../support/api";
import { aConversation, anAgent } from "../support/fixtures";
import { LocationProbe } from "../support/LocationProbe";
import { renderPage } from "../support/render";

const CONVERSATIONS = "/v1/conversations";
const AGENTS = "/v1/agents";

function withConversations(
  conversations: ReturnType<typeof aConversation>[],
  agents: ReturnType<typeof anAgent>[] = [anAgent()],
) {
  return fakeApi({
    [CONVERSATIONS]: { body: conversations },
    [AGENTS]: { body: agents },
  });
}

describe("the conversations list", () => {
  it("shows the page a visitor was on, and what the exchange cost", async () => {
    withConversations([aConversation()]);
    renderPage(<ConversationsPage />, { token: "devtok", at: "/conversations" });

    expect(await screen.findByText("Pricing")).toBeTruthy();
    expect(screen.getByText("1,200 in · 340 out")).toBeTruthy();
  });

  it("names the agent rather than its id", async () => {
    // The whole reason this page loads the agents list as well. Asserted
    // inside the row: the agent's label is also one of the filter's options,
    // so a bare query would pass on a row that never got its label.
    withConversations([aConversation({ instanceId: "agent_1" })], [anAgent()]);
    renderPage(<ConversationsPage />, { token: "devtok", at: "/conversations" });

    const row = (await screen.findByText("Pricing")).closest("article");
    await waitFor(() => expect(row?.textContent).toContain("Acme website"));
    expect(row?.textContent).not.toContain("agent_1");
  });

  it("falls back to the id when that agent is gone", async () => {
    // A deleted agent's conversations are still records. A row with a blank
    // where the name goes is worse than one that says the id.
    withConversations([aConversation({ instanceId: "agent_vanished" })], [anAgent()]);
    renderPage(<ConversationsPage />, { token: "devtok", at: "/conversations" });

    const row = (await screen.findByText("Pricing")).closest("article");
    expect(row?.textContent).toContain("agent_vanished");
  });

  it("counts the previews separately instead of hiding them", async () => {
    // They are real stored conversations had against a draft, and the
    // backend flags rather than hides them -- a filtered-out conversation is
    // one somebody goes looking for.
    withConversations([
      aConversation(),
      aConversation({ id: "conv_2", preview: true, instanceVersion: null }),
    ]);
    renderPage(<ConversationsPage />, { token: "devtok", at: "/conversations" });

    expect(await screen.findByText(/2 conversations/)).toBeTruthy();
    expect(screen.getByText(/1 from the preview panel/)).toBeTruthy();
    expect(screen.getByText("Preview")).toBeTruthy();
  });

  it("says when the agent greeted somebody and nobody answered", async () => {
    withConversations([aConversation({ turns: 1, visitorTurns: 0 })]);
    renderPage(<ConversationsPage />, { token: "devtok", at: "/conversations" });

    expect(await screen.findByText("Nobody replied")).toBeTruthy();
  });

  it("asks the server for the chosen agent, under the API's own field name", async () => {
    const api = withConversations([aConversation()]);
    renderPage(
      <>
        <ConversationsPage />
        <LocationProbe />
      </>,
      { token: "devtok", at: "/conversations" },
    );
    await screen.findByText("Pricing");

    fireEvent.change(screen.getByLabelText("Agent"), { target: { value: "agent_1" } });

    await waitFor(() =>
      expect(
        api
          .to(CONVERSATIONS, "GET")
          .some((one) => one.search === "?instanceId=agent_1"),
      ).toBe(true),
    );
    // And in the address bar, so the filtered list is a link.
    expect(screen.getByTestId("location").textContent).toBe(
      "/conversations?agent=agent_1",
    );
  });

  it("sends the channel as the backend's enum value", async () => {
    const api = withConversations([aConversation()]);
    renderPage(<ConversationsPage />, { token: "devtok", at: "/conversations" });
    await screen.findByText("Pricing");

    fireEvent.change(screen.getByLabelText("Channel"), {
      target: { value: "whatsapp" },
    });

    await waitFor(() =>
      expect(
        api.to(CONVERSATIONS, "GET").some((one) => one.search === "?channel=whatsapp"),
      ).toBe(true),
    );
  });

  it("ignores a channel in the URL that is not one", async () => {
    // The URL is user input, and the server's enum would answer 422. An
    // unreadable bookmark should show the unfiltered list.
    const api = withConversations([aConversation()]);
    renderPage(<ConversationsPage />, {
      token: "devtok",
      at: "/conversations?channel=telepathy",
    });

    await screen.findByText("Pricing");
    expect(api.last(CONVERSATIONS, "GET")?.search).toBe("");
  });

  it("explains an empty list, and says something different when it is filtered", async () => {
    withConversations([]);
    const { unmount } = renderPage(<ConversationsPage />, {
      token: "devtok",
      at: "/conversations",
    });
    expect(await screen.findByText("Nobody has talked to an agent yet")).toBeTruthy();
    unmount();

    withConversations([]);
    renderPage(<ConversationsPage />, {
      token: "devtok",
      at: "/conversations?channel=email",
    });
    expect(await screen.findByText("Nothing matches that")).toBeTruthy();
  });

  it("names no workspace, and carries the credential", async () => {
    // The tenancy rule: the workspace comes from the token, so a request
    // that could name one would be a request that could name somebody
    // else's.
    const api = withConversations([aConversation()]);
    renderPage(<ConversationsPage />, { token: "devtok", at: "/conversations" });
    await screen.findByText("Pricing");

    for (const call of api.calls) {
      expect(call.url.toLowerCase()).not.toContain("workspace");
      expect(call.authorization).toBe("Bearer devtok");
    }
  });
});
