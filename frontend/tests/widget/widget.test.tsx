/**
 * The public chat.
 *
 * Rendered with ``@testing-library/react`` directly rather than through
 * ``renderPage``: there is no store and no router in the widget, and a test
 * that wrapped it in either would be testing something the customer's page
 * never loads.
 *
 * What is asserted here is mostly *restraint*: that no conversation is opened
 * until somebody clicks, that nothing is drawn when the agent is not
 * available, that a failed turn gives the message back, and that the one
 * failure the server keeps the message for keeps it on the screen too.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WidgetConfig } from "@/widget/client";
import { Widget } from "@/widget/Widget";

import { fakeApi } from "../support/api";
import { aFace, aReply } from "../support/widgetFixtures";

const CONFIG: WidgetConfig = {
  api: "http://api.test",
  workspaceId: "ws_1",
  instanceId: "agent_1",
};

const FACE = "/v1/widget/ws_1/agent_1";
const START = FACE + "/conversations";
const SAY = START + "/conv_1/messages";

function live(answers: Record<string, Parameters<typeof fakeApi>[0][string]> = {}) {
  return fakeApi({
    [FACE]: { body: aFace() },
    [START]: { status: 201, body: { conversationId: "conv_1" } },
    [SAY]: { body: aReply() },
    ...answers,
  });
}

describe("the widget before anybody clicks", () => {
  it("draws the launcher the customer configured", async () => {
    live();
    render(<Widget config={CONFIG} />);

    expect(await screen.findByText("Chat with us")).toBeTruthy();
  });

  it("opens no conversation until somebody clicks", async () => {
    // Opening one costs no model call, but it files a stored record -- so a
    // page view is not a conversation.
    const api = live();
    render(<Widget config={CONFIG} />);
    await screen.findByText("Chat with us");

    expect(api.to(START, "POST")).toHaveLength(0);
  });

  it("draws nothing at all when the agent is not available here", async () => {
    // A paused agent, or a page that is not in the allowed origins. Neither
    // is the visitor's problem, and an error box on somebody's website
    // would be.
    fakeApi({
      [FACE]: {
        status: 403,
        body: {
          error: { code: "ORIGIN_NOT_ALLOWED", message: "https://wrong.test" },
        },
      },
    });
    const { container } = render(<Widget config={CONFIG} />);

    await waitFor(() => expect(container.textContent).toBe(""));
  });
});

describe("a conversation", () => {
  it("greets without a model call, and opens the conversation on the server", async () => {
    const api = live();
    render(<Widget config={CONFIG} />);
    fireEvent.click(await screen.findByText("Chat with us"));

    // The greeting is a setting, not a reply: it is on the screen with no
    // request behind it.
    expect(screen.getByText("Hi there! Ask me anything.")).toBeTruthy();
    await waitFor(() => expect(api.to(START, "POST")).toHaveLength(1));
    // Where the visitor was, which is most of a qualification on its own.
    expect(api.last(START, "POST")?.body).toEqual({
      pageUrl: window.location.href,
      pageTitle: document.title,
    });
  });

  it("sends what was typed, and shows the answer with its sources", async () => {
    const api = live();
    render(<Widget config={CONFIG} />);
    fireEvent.click(await screen.findByText("Chat with us"));
    await waitFor(() => expect(api.to(START, "POST")).toHaveLength(1));

    fireEvent.change(screen.getByLabelText("Your message"), {
      target: { value: "What does it cost?" },
    });
    fireEvent.click(screen.getByText("Send"));

    expect(await screen.findByText("We start at 29 per seat.")).toBeTruthy();
    expect(api.last(SAY, "POST")?.body).toEqual({ message: "What does it cost?" });
    // A citation a visitor may see: a name and a link, and nothing else.
    const link = screen.getByRole("link", { name: "Pricing" });
    expect(link.getAttribute("href")).toBe("https://acme.test/pricing");
  });

  it("shows the visitor's own message before the reply arrives", async () => {
    // A turn takes seconds. A message that vanishes until the answer lands
    // reads as a lost message.
    live({ [SAY]: { body: aReply() } });
    render(<Widget config={CONFIG} />);
    fireEvent.click(await screen.findByText("Chat with us"));
    await waitFor(() => expect(screen.getByLabelText("Your message")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Your message"), {
      target: { value: "Hello?" },
    });
    fireEvent.click(screen.getByText("Send"));

    expect(screen.getByText("Hello?")).toBeTruthy();
  });

  it("offers a quick reply as a button, and sends it when tapped", async () => {
    const api = live();
    render(<Widget config={CONFIG} />);
    fireEvent.click(await screen.findByText("Chat with us"));
    await waitFor(() => expect(screen.getByLabelText("Your message")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Your message"), {
      target: { value: "hi" },
    });
    fireEvent.click(screen.getByText("Send"));
    await screen.findByText("We start at 29 per seat.");

    fireEvent.click(screen.getByText("Book a demo"));

    await waitFor(() => expect(api.to(SAY, "POST")).toHaveLength(2));
    expect(api.last(SAY, "POST")?.body).toEqual({ message: "Book a demo" });
  });

  it("takes the box away when the agent says the conversation is closed", async () => {
    // ``open`` goes false at the turn limit. The box goes rather than
    // letting somebody type into a conversation that will refuse them.
    live({ [SAY]: { body: aReply({ open: false }) } });
    render(<Widget config={CONFIG} />);
    fireEvent.click(await screen.findByText("Chat with us"));
    await waitFor(() => expect(screen.getByLabelText("Your message")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Your message"), {
      target: { value: "hi" },
    });
    fireEvent.click(screen.getByText("Send"));

    await screen.findByText("This conversation has ended. Thanks for stopping by.");
    expect(screen.queryByLabelText("Your message")).toBeNull();
  });
});

describe("when a turn fails", () => {
  it("gives the message back, because it was not answered", async () => {
    live({
      [SAY]: {
        status: 500,
        body: { error: { code: "INTERNAL", message: "Something went wrong" } },
      },
    });
    render(<Widget config={CONFIG} />);
    fireEvent.click(await screen.findByText("Chat with us"));
    await waitFor(() => expect(screen.getByLabelText("Your message")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Your message"), {
      target: { value: "Are you there?" },
    });
    fireEvent.click(screen.getByText("Send"));

    expect(
      await screen.findByText("Something went wrong. Please try again."),
    ).toBeTruthy();
    // Taken off the screen: a transcript that claims a turn which did not
    // happen is worse than an error.
    expect(screen.queryByText("Are you there?")).toBeNull();
    // And the box is still there, because this one might work next time.
    expect(screen.getByLabelText("Your message")).toBeTruthy();
  });

  it("does not quote a 500 at a visitor", async () => {
    live({
      [SAY]: {
        status: 500,
        body: { error: { code: "INTERNAL", message: "redis: connection refused" } },
      },
    });
    render(<Widget config={CONFIG} />);
    fireEvent.click(await screen.findByText("Chat with us"));
    await waitFor(() => expect(screen.getByLabelText("Your message")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Your message"), {
      target: { value: "hi" },
    });
    fireEvent.click(screen.getByText("Send"));

    await screen.findByText("Something went wrong. Please try again.");
    expect(screen.queryByText(/redis/)).toBeNull();
  });

  it("keeps the message when the server kept it, and says why it stopped", async () => {
    // CONVERSATION_TOO_LONG is a 409 and the backend stores the visitor's
    // message anyway. Removing it here would be the screen disagreeing with
    // the transcript. The sentence is the backend's, written for a visitor.
    live({
      [SAY]: {
        status: 409,
        body: {
          error: {
            code: "CONVERSATION_TOO_LONG",
            message: "Let me pass you to someone who can help.",
            details: { limit: 20 },
          },
        },
      },
    });
    render(<Widget config={CONFIG} />);
    fireEvent.click(await screen.findByText("Chat with us"));
    await waitFor(() => expect(screen.getByLabelText("Your message")).toBeTruthy());

    fireEvent.change(screen.getByLabelText("Your message"), {
      target: { value: "one more thing" },
    });
    fireEvent.click(screen.getByText("Send"));

    expect(
      await screen.findByText("Let me pass you to someone who can help."),
    ).toBeTruthy();
    expect(screen.getByText("one more thing")).toBeTruthy();
    expect(screen.queryByLabelText("Your message")).toBeNull();
  });

  it("stops offering a box when the agent was paused mid-conversation", async () => {
    live({
      [START]: {
        status: 409,
        body: {
          error: {
            code: "AGENT_NOT_LIVE",
            message: "The agent is paused",
            details: { status: "paused", reason: "" },
          },
        },
      },
    });
    render(<Widget config={CONFIG} />);
    fireEvent.click(await screen.findByText("Chat with us"));

    expect(await screen.findByText("This chat is not available right now.")).toBeTruthy();
    expect(screen.queryByLabelText("Your message")).toBeNull();
  });
});

describe("the tenancy rule, from the public side", () => {
  it("sends no credential and reads nothing but its own agent", async () => {
    // The ids are the public address of a chat widget. What makes that safe
    // is that this is the whole surface: three paths under one agent.
    const api = live();
    render(<Widget config={CONFIG} />);
    fireEvent.click(await screen.findByText("Chat with us"));
    await waitFor(() => expect(api.to(START, "POST")).toHaveLength(1));

    for (const call of api.calls) {
      expect(call.authorization).toBeNull();
      expect(call.path.startsWith("/v1/widget/ws_1/agent_1")).toBe(true);
    }
  });
});
