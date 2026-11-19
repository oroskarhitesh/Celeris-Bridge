/**
 * The generated settings form.
 *
 * The first test is the one that matters most: the form is drawn from the
 * declarations the server sent, so a dropdown exists because the spec said
 * "enum" and not because any file here mentions ``tone``. If that stops being
 * true the whole point of this screen has gone, and this is where it shows.
 *
 * The rest are the things a form built this way can still get wrong: sending
 * values nobody changed, offering a control for a locked setting, losing a
 * rejection, and throwing away a draft that was refused.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AgentPage } from "@/presentation/pages/AgentPage";

import { fakeApi } from "../support/api";
import type { Reply } from "../support/api";
import { aSetting, aSettingsForm, anAbility, anAgent } from "../support/fixtures";
import { renderPage } from "../support/render";

const AGENT = "/v1/agents/agent_1";
const FORM = "/v1/agents/agent_1/settings";

function page(at = "/agents/agent_1") {
  return renderPage(<AgentPage />, { token: "devtok", at, path: "/agents/:id" });
}

/** The four reads the detail page makes, with room to replace any of them. */
function answers(extra: Record<string, Reply | Reply[]> = {}) {
  return {
    [AGENT]: { body: anAgent({ draftRevision: 3 }) },
    [FORM]: { body: aSettingsForm() },
    "/v1/agents/agent_1/versions": { body: [] },
    "/v1/catalogue": { body: [] },
    ...extra,
  };
}

describe("drawing the form", () => {
  it("renders a control per declaration, not per hard-coded setting", async () => {
    fakeApi(answers());
    page();

    // A string: a text box, holding the server's value.
    const greeting = (await screen.findByLabelText("Greeting")) as HTMLInputElement;
    expect(greeting.value).toBe("Hi there!");

    // An enum: a select, with exactly the declared options.
    const tone = screen.getByLabelText("Tone") as HTMLSelectElement;
    expect([...tone.options].map((one) => one.value)).toEqual(["friendly", "formal"]);

    // A number: its range comes from the spec, so the arrows cannot leave it.
    const turns = screen.getByLabelText("Turns before handover") as HTMLInputElement;
    expect(turns.type).toBe("number");
    expect(turns.min).toBe("4");
    expect(turns.max).toBe("60");
  });

  it("groups the settings in the order the definition declared", async () => {
    fakeApi(answers());
    page();

    await screen.findByLabelText("Greeting");
    const headings = screen
      .getAllByRole("heading", { level: 2 })
      .map((one) => one.textContent);
    expect(headings.slice(0, 2)).toEqual(["Persona", "Conversation"]);
  });

  it("shows a locked setting without a way to change it", async () => {
    fakeApi(answers());
    page();

    const model = (await screen.findByLabelText("Chat model")) as HTMLInputElement;
    expect(model.disabled).toBe(true);
    expect(screen.getAllByText("set by the platform").length).toBeGreaterThan(0);
  });

  it("says which values are the workspace's own and which are defaults", async () => {
    fakeApi(
      answers({
        [FORM]: {
          body: aSettingsForm({
            settings: [aSetting({ value: "Hello you", overridden: true })],
          }),
        },
      }),
    );
    page();

    await screen.findByLabelText("Greeting");
    expect(screen.getByText("yours")).toBeTruthy();
  });

  it("reads out the rules it cannot change", async () => {
    fakeApi(answers());
    page();

    expect(await screen.findByText("Never claim to be a person.")).toBeTruthy();
  });
});

describe("saving", () => {
  it("sends only what changed, with the revision it read", async () => {
    const api = fakeApi(
      answers({ ["PATCH " + AGENT]: { body: anAgent({ draftRevision: 4 }) } }),
    );
    page();

    fireEvent.change(await screen.findByLabelText("Greeting"), {
      target: { value: "Hello!" },
    });
    fireEvent.click(screen.getByText("Save draft"));

    await waitFor(() => expect(api.last(AGENT, "PATCH")).toBeTruthy());
    expect(api.last(AGENT, "PATCH")?.body).toEqual({
      expectRevision: 3,
      settings: { greeting: "Hello!" },
      abilities: {},
    });
  });

  it("offers nothing to save until something really differs", async () => {
    fakeApi(answers());
    page();

    const greeting = await screen.findByLabelText("Greeting");
    expect(screen.queryByText("Save draft")).toBe(null);

    fireEvent.change(greeting, { target: { value: "Hello!" } });
    expect(screen.getByText("One unsaved change")).toBeTruthy();

    // Typed back: the bar goes away on its own, because "changed" is a
    // comparison against the server's value, not a record of edits.
    fireEvent.change(greeting, { target: { value: "Hi there!" } });
    expect(screen.queryByText("Save draft")).toBe(null);
  });

  it("writes the default to drop an override, rather than asking for a reset", async () => {
    const api = fakeApi(
      answers({
        [FORM]: {
          body: aSettingsForm({
            settings: [aSetting({ value: "Hello you", overridden: true })],
          }),
        },
        ["PATCH " + AGENT]: { body: anAgent() },
      }),
    );
    page();

    await screen.findByLabelText("Greeting");
    fireEvent.click(screen.getByText("Use default"));
    fireEvent.click(screen.getByText("Save draft"));

    await waitFor(() => expect(api.last(AGENT, "PATCH")).toBeTruthy());
    // The backend keeps a delta, so sending today's default deletes the
    // override -- and the setting follows a better default when one ships.
    expect(api.last(AGENT, "PATCH")?.body).toMatchObject({
      settings: { greeting: "Hi there!" },
    });
  });

  it("marks the field the backend rejected and keeps what was typed", async () => {
    fakeApi(
      answers({
        ["PATCH " + AGENT]: {
          status: 400,
          body: {
            error: {
              code: "OVERRIDES_REJECTED",
              message: "Some of those values were not accepted",
              details: {
                rejections: [
                  {
                    path: "settings.greeting",
                    code: "SETTING_INVALID",
                    message: "Greeting must be at most 200 characters",
                  },
                ],
              },
            },
          },
        },
      }),
    );
    page();

    const greeting = await screen.findByLabelText("Greeting");
    fireEvent.change(greeting, { target: { value: "xxxxxxxxxx" } });
    fireEvent.click(screen.getByText("Save draft"));

    expect(
      await screen.findByText("Greeting must be at most 200 characters"),
    ).toBeTruthy();
    // Nothing was applied, so the draft is still there to fix and resend. A
    // form that cleared itself on a refusal is one somebody has to retype.
    expect((greeting as HTMLInputElement).value).toBe("xxxxxxxxxx");
    expect(screen.getByText("One unsaved change")).toBeTruthy();
  });

  it("throws the draft away when asked, and only then", async () => {
    fakeApi(answers());
    page();

    fireEvent.change(await screen.findByLabelText("Greeting"), {
      target: { value: "Hello!" },
    });
    fireEvent.click(screen.getByText("Discard"));

    expect((screen.getByLabelText("Greeting") as HTMLInputElement).value).toBe(
      "Hi there!",
    );
  });
});

describe("abilities", () => {
  it("switches one, and sends it as an ability change", async () => {
    const api = fakeApi(answers({ ["PATCH " + AGENT]: { body: anAgent() } }));
    page("/agents/agent_1?tab=abilities");

    const toggle = (await screen.findByLabelText("On")) as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    fireEvent.click(toggle);
    fireEvent.click(screen.getByText("Save draft"));

    await waitFor(() => expect(api.last(AGENT, "PATCH")).toBeTruthy());
    expect(api.last(AGENT, "PATCH")?.body).toMatchObject({
      abilities: { find_slots: { enabled: false } },
    });
  });

  it("disables an ability's own settings while it is off", async () => {
    fakeApi(
      answers({
        [FORM]: { body: aSettingsForm({ abilities: [anAbility({ enabled: false })] }) },
      }),
    );
    page("/agents/agent_1?tab=abilities");

    const days = (await screen.findByLabelText(
      "How far ahead to look",
    )) as HTMLInputElement;
    // Visible, so it can be read before switching the ability on, and
    // disabled, so it cannot be set for an ability that will not run.
    expect(days.disabled).toBe(true);
  });

  it("says when an ability the definition names is not in this build", async () => {
    fakeApi(
      answers({
        [FORM]: {
          body: aSettingsForm({
            abilities: [
              anAbility({ key: "book_slot", label: "Book a time", missing: true }),
            ],
          }),
        },
      }),
    );
    page("/agents/agent_1?tab=abilities");

    expect(
      await screen.findByText("One ability is not available in this deployment"),
    ).toBeTruthy();
    expect(screen.getByText("unavailable")).toBeTruthy();
  });
});
