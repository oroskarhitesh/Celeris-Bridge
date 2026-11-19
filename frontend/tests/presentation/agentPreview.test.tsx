/**
 * Previewing the draft.
 *
 * Two claims are worth a test each. A turn costs a model call, so nothing
 * here may happen on its own: the conversation starts when somebody asks for
 * it. And the turn limit is not an error -- the agent has stopped, the input
 * box goes away, and the words say so.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AgentPage } from "@/presentation/pages/AgentPage";

import { fakeApi } from "../support/api";
import type { Reply } from "../support/api";
import { aSettingsForm, anAgent } from "../support/fixtures";
import { renderPage } from "../support/render";

const START = "/v1/agents/agent_1/preview/conversations";
const SAY = "/v1/agents/agent_1/preview/conversations/conv_1/messages";

function page() {
  return renderPage(<AgentPage />, {
    token: "devtok",
    at: "/agents/agent_1?tab=preview",
    path: "/agents/:id",
  });
}

function answers(extra: Record<string, Reply | Reply[]> = {}) {
  return {
    "/v1/agents/agent_1": { body: anAgent() },
    "/v1/agents/agent_1/settings": { body: aSettingsForm() },
    "/v1/agents/agent_1/versions": { body: [] },
    "/v1/catalogue": { body: [] },
    ...extra,
  };
}

const REPLY = {
  conversationId: "conv_1",
  text: "Happy to help. What are you building?",
  ending: "answered",
  open: true,
  sources: [{ name: "Pricing", url: "https://acme.test/pricing" }],
  quickReplies: ["Book a demo"],
};

describe("the preview", () => {
  it("starts nothing until it is asked to", async () => {
    const api = fakeApi(answers());
    page();

    expect(await screen.findByText("Start a preview")).toBeTruthy();
    // No conversation, and so no model call and no spend, from opening a tab.
    expect(api.to(START, "POST")).toHaveLength(0);
    expect(screen.queryByLabelText("Message to the draft")).toBe(null);
  });

  it("sends a message and shows the reply with its sources", async () => {
    const api = fakeApi(
      answers({
        ["POST " + START]: {
          status: 201,
          body: { conversationId: "conv_1", instanceVersion: null, templateVersion: 1 },
        },
        ["POST " + SAY]: { body: REPLY },
      }),
    );
    page();

    fireEvent.click(await screen.findByText("Start a preview"));

    const box = await screen.findByLabelText("Message to the draft");
    fireEvent.change(box, { target: { value: "What do you do?" } });
    fireEvent.click(screen.getByText("Send"));

    expect(await screen.findByText("Happy to help. What are you building?")).toBeTruthy();
    expect(screen.getByText("What do you do?")).toBeTruthy();
    expect(screen.getByText("Pricing")).toBeTruthy();
    expect(screen.getByText("Book a demo")).toBeTruthy();
    expect(api.last(SAY, "POST")?.body).toEqual({ message: "What do you do?" });
  });

  it("closes the box when the conversation reaches its turn limit", async () => {
    // Not a failure: maxTurns is a setting doing its job, and the visitor's
    // message was stored either way. Letting someone keep typing into a
    // conversation that will refuse them is the bug.
    fakeApi(
      answers({
        ["POST " + START]: {
          status: 201,
          body: { conversationId: "conv_1", instanceVersion: null, templateVersion: 1 },
        },
        ["POST " + SAY]: {
          status: 409,
          body: {
            error: {
              code: "CONVERSATION_TOO_LONG",
              message: "This conversation reached its limit of 20 turns",
              details: { limit: 20 },
            },
          },
        },
      }),
    );
    page();

    fireEvent.click(await screen.findByText("Start a preview"));
    fireEvent.change(await screen.findByLabelText("Message to the draft"), {
      target: { value: "One more?" },
    });
    fireEvent.click(screen.getByText("Send"));

    expect(
      await screen.findByText("This conversation reached its limit of 20 turns"),
    ).toBeTruthy();
    await waitFor(() =>
      expect(screen.queryByLabelText("Message to the draft")).toBe(null),
    );
  });

  it("stops offering the box when a reply says the agent is done", async () => {
    fakeApi(
      answers({
        ["POST " + START]: {
          status: 201,
          body: { conversationId: "conv_1", instanceVersion: null, templateVersion: 1 },
        },
        ["POST " + SAY]: { body: { ...REPLY, open: false, quickReplies: [] } },
      }),
    );
    page();

    fireEvent.click(await screen.findByText("Start a preview"));
    fireEvent.change(await screen.findByLabelText("Message to the draft"), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByText("Send"));

    expect(
      await screen.findByText("This conversation is over. Start another to keep testing."),
    ).toBeTruthy();
  });

  it("takes an unanswered message back out of the transcript", async () => {
    // A turn that failed did not happen. Leaving it on screen would make the
    // transcript claim something the stored one does not have.
    fakeApi(
      answers({
        ["POST " + START]: {
          status: 201,
          body: { conversationId: "conv_1", instanceVersion: null, templateVersion: 1 },
        },
        ["POST " + SAY]: {
          status: 500,
          body: { error: { code: "INTERNAL", message: "" } },
        },
      }),
    );
    page();

    fireEvent.click(await screen.findByText("Start a preview"));
    fireEvent.change(await screen.findByLabelText("Message to the draft"), {
      target: { value: "Are you there?" },
    });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => expect(screen.queryByText("Are you there?")).toBe(null));
    // And it is back in the box, so nobody has to retype it.
    expect(
      (screen.getByLabelText("Message to the draft") as HTMLInputElement).value,
    ).toBe("Are you there?");
  });
});
