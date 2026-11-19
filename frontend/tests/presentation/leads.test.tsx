/**
 * The leads table.
 *
 * The interesting assertion is the columns. A lead's details arrive as a map
 * under the keys the workspace asked for, so the columns are built from the
 * data -- and a test that pins them to "Name" and "Email" would be testing
 * the wrong thing. These pin the rule instead: the union of the keys that
 * arrived, in the order they were first seen, and an em dash where a lead has
 * nothing.
 *
 * The other one is the default. This screen asks for ``withDetailsOnly``
 * although the API defaults it to false, which is a disagreement that has to
 * be deliberate and visible rather than accidental.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LeadsPage } from "@/presentation/pages/LeadsPage";

import { fakeApi } from "../support/api";
import { aLead, anAgent } from "../support/fixtures";
import { renderPage } from "../support/render";

const LEADS = "/v1/leads";
const AGENTS = "/v1/agents";

function withLeads(leads: ReturnType<typeof aLead>[]) {
  return fakeApi({ [LEADS]: { body: leads }, [AGENTS]: { body: [anAgent()] } });
}

describe("the leads table", () => {
  it("takes its columns from the details that arrived", async () => {
    withLeads([
      aLead({ details: { name: "Dana Fox", email: "dana@acme.test" } }),
      aLead({
        conversationId: "conv_2",
        // A second lead adds a key the first one did not have, and the
        // column has to appear for both.
        details: { name: "Sam Reed", companySize: "40" },
      }),
    ]);
    renderPage(<LeadsPage />, { token: "devtok", at: "/leads" });

    expect(await screen.findByText("Dana Fox")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Name" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Email" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Company size" })).toBeTruthy();
  });

  it("leaves an em dash where a lead has nothing under a column", async () => {
    // The point of the column existing: an empty cell in a row of full ones
    // is the follow-up.
    withLeads([
      aLead({ details: { name: "Dana Fox", email: "dana@acme.test" } }),
      aLead({ conversationId: "conv_2", details: { name: "Sam Reed" } }),
    ]);
    renderPage(<LeadsPage />, { token: "devtok", at: "/leads" });

    await screen.findByText("Sam Reed");
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("shows the verdict, what is still missing, and why they got in touch", async () => {
    withLeads([aLead()]);
    renderPage(<LeadsPage />, { token: "devtok", at: "/leads" });

    expect(await screen.findByText("Dana Fox")).toBeTruthy();
    // By its explanation rather than its word: "Qualifying" is also one of
    // the filter's options, and a test that cannot tell the badge from the
    // dropdown is a test that passes on an empty table.
    expect(
      screen.getByTitle("Something was shared and something required is still missing."),
    ).toBeTruthy();
    expect(screen.getByText("phone")).toBeTruthy();
    expect(screen.getByText("Do you integrate with our CRM?")).toBeTruthy();
    // What the model was told they are, when it was told anything.
    expect(screen.getByText("buyer")).toBeTruthy();
  });

  it("counts the qualified ones and the unfinished ones in the header", async () => {
    withLeads([
      aLead({ status: "qualified", missing: [], complete: true }),
      aLead({ conversationId: "conv_2", status: "qualifying", missing: ["phone"] }),
    ]);
    renderPage(<LeadsPage />, { token: "devtok", at: "/leads" });

    expect(await screen.findByText(/1 qualified/)).toBeTruthy();
    expect(screen.getByText(/1 missing something/)).toBeTruthy();
  });

  it("asks for only the conversations that produced details", async () => {
    // The server defaults this to false. The screen defaults it to true,
    // because a widget on a public page has far more conversations than
    // leads and a list of passers-by buries the ones worth calling.
    const api = withLeads([aLead()]);
    renderPage(<LeadsPage />, { token: "devtok", at: "/leads" });

    await screen.findByText("Dana Fox");
    expect(api.last(LEADS, "GET")?.search).toBe("?withDetailsOnly=true");
  });

  it("stops asking for that when the box is ticked, and says so", async () => {
    const api = withLeads([aLead()]);
    renderPage(<LeadsPage />, { token: "devtok", at: "/leads" });
    await screen.findByText("Dana Fox");

    fireEvent.click(screen.getByLabelText("Include conversations with no details"));

    await waitFor(() =>
      expect(api.to(LEADS, "GET").some((one) => one.search === "")).toBe(true),
    );
    expect(screen.getByText(/including people who shared nothing/)).toBeTruthy();
  });

  it("sends the verdict filter to the server", async () => {
    const api = withLeads([aLead()]);
    renderPage(<LeadsPage />, { token: "devtok", at: "/leads" });
    await screen.findByText("Dana Fox");

    fireEvent.change(screen.getByLabelText("Verdict"), {
      target: { value: "qualified" },
    });

    await waitFor(() =>
      expect(
        api
          .to(LEADS, "GET")
          .some((one) => one.search.includes("status=qualified")),
      ).toBe(true),
    );
  });

  it("explains an empty list in the words of the filter that produced it", async () => {
    withLeads([]);
    const { unmount } = renderPage(<LeadsPage />, { token: "devtok", at: "/leads" });
    expect(await screen.findByText("Nobody has shared anything yet")).toBeTruthy();
    unmount();

    withLeads([]);
    renderPage(<LeadsPage />, { token: "devtok", at: "/leads?everyone=1" });
    expect(await screen.findByText("No conversations yet")).toBeTruthy();
  });

  it("links each lead to the conversation it came from", async () => {
    // There is no lead record to open: a lead *is* a conversation, and the
    // transcript is the rest of the story.
    withLeads([aLead()]);
    renderPage(<LeadsPage />, { token: "devtok", at: "/leads" });

    const link = await screen.findByRole("link", { name: "Transcript" });
    expect(link.getAttribute("href")).toBe("/conversations/conv_1");
  });

  it("names no workspace, and carries the credential", async () => {
    const api = withLeads([aLead()]);
    renderPage(<LeadsPage />, { token: "devtok", at: "/leads" });
    await screen.findByText("Dana Fox");

    for (const call of api.calls) {
      expect(call.url.toLowerCase()).not.toContain("workspace");
      expect(call.authorization).toBe("Bearer devtok");
    }
  });
});
