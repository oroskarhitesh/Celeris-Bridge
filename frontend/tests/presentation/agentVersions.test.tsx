/**
 * Publishing, the history, rolling back and upgrading.
 *
 * Everything here is about the gap between what is on the screen and what a
 * visitor is being served, which is the thing a draft-based product is
 * easiest to get wrong about. So the tests are mostly refusals: publish is
 * dead while edits are unsaved, dead when the live version already matches,
 * and an upgrade shows what it would cost before it is offered at all.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AgentPage } from "@/presentation/pages/AgentPage";

import { fakeApi } from "../support/api";
import type { Reply } from "../support/api";
import { aSettingsForm, aType, aVersion, anAgent } from "../support/fixtures";
import { renderPage } from "../support/render";

const AGENT = "/v1/agents/agent_1";
const VERSIONS = "/v1/agents/agent_1/versions";
const UPGRADE = "/v1/agents/agent_1/upgrade";

function page() {
  return renderPage(<AgentPage />, {
    token: "devtok",
    at: "/agents/agent_1?tab=versions",
    path: "/agents/:id",
  });
}

function answers(extra: Record<string, Reply | Reply[]> = {}) {
  return {
    [AGENT]: { body: anAgent({ draftRevision: 3 }) },
    "/v1/agents/agent_1/settings": { body: aSettingsForm() },
    [VERSIONS]: { body: [aVersion()] },
    "/v1/catalogue": { body: [aType()] },
    ...extra,
  };
}

describe("publishing", () => {
  it("posts the revision and the summary", async () => {
    const api = fakeApi(
      answers({
        [AGENT]: { body: anAgent({ hasUnpublishedChanges: true, draftRevision: 3 }) },
        "POST /v1/agents/agent_1/publish": {
          status: 201,
          body: { version: aVersion({ version: 3 }), agent: anAgent({ draftRevision: 4 }) },
        },
      }),
    );
    page();

    fireEvent.change(await screen.findByLabelText("What changed"), {
      target: { value: "Warmer greeting" },
    });
    fireEvent.click(screen.getByText("Publish new version"));

    await waitFor(() =>
      expect(api.last("/v1/agents/agent_1/publish", "POST")).toBeTruthy(),
    );
    expect(api.last("/v1/agents/agent_1/publish", "POST")?.body).toEqual({
      expectRevision: 3,
      changeSummary: "Warmer greeting",
    });
    expect(await screen.findByText("Version 3 is live.")).toBeTruthy();
  });

  it("refuses while there are unsaved changes, and says what to do", async () => {
    // Publishing snapshots the saved draft. With edits on the screen it would
    // ship something other than what is being looked at, which is worse than
    // a disabled button.
    fakeApi(
      answers({
        [AGENT]: { body: anAgent({ hasUnpublishedChanges: true }) },
      }),
    );
    const { store } = page();

    await screen.findByText("Publish new version");
    // Touch a setting through the store rather than the settings tab: this
    // test is about the publish panel, not about a text box.
    store.dispatch({
      type: "draft/settingTouched",
      payload: { key: "greeting", value: "Hello!" },
    });

    await waitFor(() =>
      expect(
        (screen.getByText("Publish new version").closest("button") as HTMLButtonElement)
          .disabled,
      ).toBe(true),
    );
    expect(screen.getByText(/Save your change first/)).toBeTruthy();
  });

  it("will not offer a publish that the server would refuse as a no-op", async () => {
    // hasUnpublishedChanges is computed by the same comparison that makes
    // publishing an unchanged draft a NOTHING_TO_PUBLISH, so the button can
    // trust it exactly.
    fakeApi(answers({ [AGENT]: { body: anAgent({ hasUnpublishedChanges: false }) } }));
    page();

    const button = (await screen.findByText("Publish new version")).closest(
      "button",
    ) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText("The live version already matches this draft.")).toBeTruthy();
  });

  it("tells a never-published agent that nobody can reach it", async () => {
    fakeApi(
      answers({
        [AGENT]: { body: anAgent({ status: "draft", publishedVersion: null }) },
        [VERSIONS]: { body: [] },
      }),
    );
    page();

    expect(await screen.findByText("Not published yet")).toBeTruthy();
    expect(screen.getByText("Publish and go live")).toBeTruthy();
  });
});

describe("the history", () => {
  it("marks the live version and offers a way back to the others", async () => {
    fakeApi(
      answers({
        [AGENT]: { body: anAgent({ publishedVersion: 2 }) },
        [VERSIONS]: {
          body: [aVersion({ version: 2 }), aVersion({ version: 1, changeSummary: "" })],
        },
      }),
    );
    page();

    expect(await screen.findByText("Version 2")).toBeTruthy();
    expect(screen.getByText("Live")).toBeTruthy();
    // No summary was written, and the list says so rather than leaving a gap.
    expect(screen.getByText("No summary was written.")).toBeTruthy();
    // One button, for the version that is not live.
    expect(screen.getAllByText("Go back to this")).toHaveLength(1);
  });

  it("rolls back by publishing again, and says that is what happened", async () => {
    const api = fakeApi(
      answers({
        [AGENT]: { body: anAgent({ publishedVersion: 2 }) },
        [VERSIONS]: { body: [aVersion({ version: 2 }), aVersion({ version: 1 })] },
        "POST /v1/agents/agent_1/rollback": {
          status: 201,
          body: {
            version: aVersion({ version: 3, rolledBackFrom: 1 }),
            agent: anAgent({ publishedVersion: 3, draftRevision: 4 }),
          },
        },
      }),
    );
    page();

    fireEvent.click(await screen.findByText("Go back to this"));
    fireEvent.click(screen.getByText("Publish version 1 again"));

    await waitFor(() =>
      expect(api.last("/v1/agents/agent_1/rollback", "POST")).toBeTruthy(),
    );
    expect(api.last("/v1/agents/agent_1/rollback", "POST")?.body).toEqual({
      expectRevision: 3,
      toVersion: 1,
    });
    expect(
      await screen.findByText("Version 3 is live, carrying what version 1 had."),
    ).toBeTruthy();
  });

  it("shows where a rolled-back version came from", async () => {
    fakeApi(
      answers({
        [AGENT]: { body: anAgent({ publishedVersion: 3 }) },
        [VERSIONS]: { body: [aVersion({ version: 3, rolledBackFrom: 1 })] },
      }),
    );
    page();

    expect(await screen.findByText(/carried over from version 1/)).toBeTruthy();
  });
});

describe("upgrading the agent type", () => {
  it("says nothing to do when the agent is on the newest version", async () => {
    const api = fakeApi(
      answers({ "/v1/catalogue": { body: [aType({ version: 1 })] } }),
    );
    page();

    expect(
      await screen.findByText(/which is the newest the platform has published/),
    ).toBeTruthy();
    // And it did not ask what an upgrade would cost, because there is none.
    expect(api.to(UPGRADE, "GET")).toHaveLength(0);
  });

  it("shows what would be lost before offering the upgrade", async () => {
    fakeApi(
      answers({
        "/v1/catalogue": { body: [aType({ version: 4 })] },
        [UPGRADE]: {
          body: {
            fromVersion: 1,
            toVersion: 4,
            dropped: [
              { path: "settings.tone", reason: "is no longer a setting of this agent" },
            ],
            addedSettings: ["languages"],
            losesNothing: false,
          },
        },
      }),
    );
    page();

    expect(
      await screen.findByText("Version 4 of this agent type is available"),
    ).toBeTruthy();
    // Awaited, not read: the card's title appears as soon as the catalogue
    // says a newer version exists, which is before the preview has landed.
    expect(
      await screen.findByText("One of your values cannot come along"),
    ).toBeTruthy();
    expect(screen.getByText("is no longer a setting of this agent")).toBeTruthy();
    expect(screen.getByText("languages")).toBeTruthy();
  });

  it("moves the draft only, and takes two clicks when something is dropped", async () => {
    const api = fakeApi(
      answers({
        "/v1/catalogue": { body: [aType({ version: 4 })] },
        [UPGRADE]: [
          {
            body: {
              fromVersion: 1,
              toVersion: 4,
              dropped: [{ path: "settings.tone", reason: "is no longer a setting" }],
              addedSettings: [],
              losesNothing: false,
            },
          },
        ],
        "POST /v1/agents/agent_1/upgrade": {
          body: anAgent({ draftTemplateVersion: 4, draftRevision: 4 }),
        },
      }),
    );
    page();

    fireEvent.click(await screen.findByText("Upgrade to v4"));
    fireEvent.click(screen.getByText("Upgrade and drop 1"));

    await waitFor(() => expect(api.last(UPGRADE, "POST")).toBeTruthy());
    expect(api.last(UPGRADE, "POST")?.body).toEqual({
      expectRevision: 3,
      toVersion: 4,
    });
    // It is not a publish: visitors keep the old snapshot until one happens.
    expect(
      await screen.findByText("The draft is on agent type v4. Publish to make it live."),
    ).toBeTruthy();
  });

  it("upgrades in one click when nothing would be lost", async () => {
    const api = fakeApi(
      answers({
        "/v1/catalogue": { body: [aType({ version: 2 })] },
        [UPGRADE]: {
          body: {
            fromVersion: 1,
            toVersion: 2,
            dropped: [],
            addedSettings: [],
            losesNothing: true,
          },
        },
        "POST /v1/agents/agent_1/upgrade": { body: anAgent({ draftTemplateVersion: 2 }) },
      }),
    );
    page();

    expect(await screen.findByText("Nothing you have configured would be lost.")).toBeTruthy();
    fireEvent.click(screen.getByText(/Upgrade to v/));

    await waitFor(() => expect(api.last(UPGRADE, "POST")).toBeTruthy());
  });
});
