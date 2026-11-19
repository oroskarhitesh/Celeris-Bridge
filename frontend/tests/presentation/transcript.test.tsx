/**
 * One transcript, which is the screen the whole chunk exists for.
 *
 * Everything asserted here is something a visitor may never see: the model
 * that answered, the tokens the turn cost, why it stopped, which abilities
 * ran and how long they took, and the passages behind the answer. That
 * asymmetry is the reason the admin router is separate from the widget's, so
 * it is worth tests that would fail if the screen quietly stopped showing one
 * of them.
 */

import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TranscriptPage } from "@/presentation/pages/TranscriptPage";

import { fakeApi } from "../support/api";
import { aLead, aTranscript, aTurn, anAgent } from "../support/fixtures";
import { renderPage } from "../support/render";

const AT = "/conversations/conv_1";
const PATH = "/conversations/:id";
const TRANSCRIPT = "/v1/conversations/conv_1";
const LEAD = "/v1/leads/conv_1";

function withTranscript(
  transcript: ReturnType<typeof aTranscript>,
  lead: ReturnType<typeof aLead> | null = aLead(),
) {
  return fakeApi({
    [TRANSCRIPT]: { body: transcript },
    ...(lead ? { [LEAD]: { body: lead } } : {}),
    "/v1/agents/agent_1": { body: anAgent() },
  });
}

describe("a transcript", () => {
  it("shows both sides of the conversation", async () => {
    withTranscript(aTranscript());
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    expect(await screen.findByText("What does it cost?")).toBeTruthy();
    expect(screen.getByText("We start at 29 per seat.")).toBeTruthy();
    expect(screen.getByText("2 turns, with what each one cost and which abilities ran.")).toBeTruthy();
  });

  it("reads the conversation and its lead record in one go", async () => {
    // The only screen where the two routers meet, and the reason the single
    // lead endpoint exists in the data layer at all.
    const api = withTranscript(aTranscript());
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });
    await screen.findByText("What does it cost?");

    expect(api.last(TRANSCRIPT, "GET")).toBeTruthy();
    expect(api.last(LEAD, "GET")).toBeTruthy();
  });

  it("puts the cost on the turn that cost it, and not on the visitor's", async () => {
    withTranscript(aTranscript());
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    await screen.findByText("What does it cost?");
    expect(screen.getByText(/900 in · 120 out/)).toBeTruthy();
    // The visitor's turn has no model and no tokens, so it gets no numbers
    // at all rather than a row of zeroes.
    expect(screen.queryByText(/0 in · 0 out/)).toBeNull();
  });

  it("says the model that answered", async () => {
    withTranscript(aTranscript());
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    expect(await screen.findByText(/claude-opus-5/)).toBeTruthy();
  });

  it("explains an outage that reached a customer", async () => {
    // ``unavailable`` is the ending worth being able to count: the reply the
    // visitor got was fallback wording, and nothing in the text says so.
    withTranscript(
      aTranscript({
        turnsDetail: [aTurn({ ending: "unavailable", text: "Sorry, one moment." })],
      }),
    );
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    expect(
      await screen.findByText(
        "The model could not be reached. This is the fallback wording.",
      ),
    ).toBeTruthy();
  });

  it("says nothing extra about a turn that went fine", async () => {
    withTranscript(aTranscript({ turnsDetail: [aTurn({ ending: "answered" })] }));
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    await screen.findByText("We start at 29 per seat.");
    expect(screen.queryByText(/fallback wording/)).toBeNull();
  });

  it("shows which abilities ran, how long they took, and which failed", async () => {
    withTranscript(
      aTranscript({
        turnsDetail: [
          aTurn({
            calls: [
              { key: "search_knowledge", ok: true, error: "", milliseconds: 142, replayed: false },
              { key: "book_slot", ok: false, error: "SLOT_TAKEN", milliseconds: 310, replayed: false },
            ],
          }),
        ],
      }),
    );
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    expect(await screen.findByText("search_knowledge")).toBeTruthy();
    expect(screen.getByText("142 ms")).toBeTruthy();
    // A failed ability does not fail the turn: the reply can look fine and be
    // an answer given without the calendar.
    expect(screen.getByText("failed — SLOT_TAKEN")).toBeTruthy();
  });

  it("marks a replayed call, because it did not cost a second one", async () => {
    withTranscript(
      aTranscript({
        turnsDetail: [
          aTurn({
            calls: [
              { key: "search_knowledge", ok: true, error: "", milliseconds: 0, replayed: true },
            ],
          }),
        ],
      }),
    );
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    expect(await screen.findByText("· replayed")).toBeTruthy();
  });

  it("keeps the passage text one click away, with its score", async () => {
    withTranscript(
      aTranscript({
        turnsDetail: [
          aTurn({
            sources: [
              {
                sourceId: "up-1",
                name: "Pricing.pdf",
                url: null,
                score: 0.82,
                approved: true,
                text: "Team plans start at 29 per seat per month.",
              },
            ],
          }),
        ],
      }),
    );
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    // Collapsed: a transcript is read as a conversation first.
    const disclosure = await screen.findByText("1 passage behind this answer");
    fireEvent.click(disclosure);

    expect(screen.getByText("Team plans start at 29 per seat per month.")).toBeTruthy();
    expect(screen.getByText("score 0.82")).toBeTruthy();
    // The thing a visitor is never sent, and the whole point of storing it.
    expect(screen.getByText("Approved")).toBeTruthy();
  });

  it("says which configuration was in force", async () => {
    withTranscript(aTranscript({ templateVersion: 1, instanceVersion: 2 }));
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    expect(await screen.findByText("template v1 · agent v2")).toBeTruthy();
  });

  it("says a preview was had against the draft, since there is no version to name", async () => {
    withTranscript(aTranscript({ preview: true, instanceVersion: null }));
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    expect(await screen.findByText("template v1 · the draft at the time")).toBeTruthy();
    expect(screen.getByText("Preview")).toBeTruthy();
  });

  it("shows what was learned beside the words that produced it", async () => {
    withTranscript(aTranscript(), aLead());
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    expect(await screen.findByText("Dana Fox")).toBeTruthy();
    expect(screen.getByText("dana@acme.test")).toBeTruthy();
    expect(screen.getByText("Qualifying")).toBeTruthy();
    expect(screen.getByText(/Still missing/)).toBeTruthy();
  });

  it("treats a conversation nobody shared anything in as a fact, not a failure", async () => {
    withTranscript(
      aTranscript(),
      aLead({ details: {}, status: "exploring", missing: [], visitorType: "unknown" }),
    );
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    expect(
      await screen.findByText("Nothing was shared in this conversation."),
    ).toBeTruthy();
    expect(screen.getByText("Exploring")).toBeTruthy();
  });

  it("gives the reasons a visitor was not a fit", async () => {
    withTranscript(
      aTranscript(),
      aLead({ status: "not_a_fit", reasons: ["The visitor is a competitor"] }),
    );
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    expect(await screen.findByText("Not a fit")).toBeTruthy();
    expect(screen.getByText("The visitor is a competitor")).toBeTruthy();
  });

  it("quotes the server when the conversation is not there", async () => {
    fakeApi({
      [TRANSCRIPT]: {
        status: 404,
        body: {
          // The code a live backend sends: every "no such thing" in the
          // services is a LookupError, and the error table maps those to one
          // 404 rather than a code per lookup.
          error: { code: "NOT_FOUND", message: "No conversation 'conv_1'" },
        },
      },
    });
    renderPage(<TranscriptPage />, { token: "devtok", at: AT, path: PATH });

    expect(
      await screen.findByText("No conversation 'conv_1'"),
    ).toBeTruthy();
  });
});
