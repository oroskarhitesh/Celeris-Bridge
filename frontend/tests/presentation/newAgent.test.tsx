/**
 * Creating an agent.
 *
 * Two of these are about refusing before the request rather than explaining
 * afterwards: a type at its per-workspace limit cannot be chosen, and the
 * button is dead until there is a type and a name. The rest is the wire:
 * camelCase field names, no template version, no workspace.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NewAgentPage } from "@/presentation/pages/NewAgentPage";

import { fakeApi } from "../support/api";
import { anAgent, aType } from "../support/fixtures";
import { LocationProbe } from "../support/LocationProbe";
import { renderPage } from "../support/render";

const CATALOGUE = "/v1/catalogue";
const AGENTS = "/v1/agents";

function page() {
  return renderPage(
    <>
      <NewAgentPage />
      <LocationProbe />
    </>,
    { token: "devtok", at: "/agents/new" },
  );
}

describe("choosing a type", () => {
  it("offers the published types", async () => {
    fakeApi({ [CATALOGUE]: { body: [aType()] } });
    page();

    expect(await screen.findByText("Marketing agent")).toBeTruthy();
    expect(screen.getByText("0 of 3 used")).toBeTruthy();
  });

  it("refuses a type that is at its limit, before the request", async () => {
    // ``owned`` and ``maxPerWorkspace`` travel together for exactly this: the
    // tile goes dead instead of the backend answering 409 after a form was
    // filled in.
    fakeApi({ [CATALOGUE]: { body: [aType({ owned: 3, maxPerWorkspace: 3 })] } });
    page();

    const tile = (await screen.findByText("Marketing agent")).closest("button");
    expect(tile?.disabled).toBe(true);
    expect(screen.getByText(/no more of this type/)).toBeTruthy();
  });

  it("says plainly when the platform has published nothing", async () => {
    fakeApi({ [CATALOGUE]: { body: [] } });
    page();

    expect(await screen.findByText("No agent types yet")).toBeTruthy();
  });
});

describe("creating", () => {
  it("needs a type and a name", async () => {
    fakeApi({ [CATALOGUE]: { body: [aType()] } });
    page();

    // Wait for the catalogue, not for the button: the button is on the page
    // from the first render.
    await screen.findByText("Marketing agent");
    const button = screen.getByText("Create draft").closest("button");
    expect(button?.disabled).toBe(true);

    fireEvent.click(screen.getByText("Marketing agent"));
    expect(button?.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("What this agent is called"), {
      target: { value: "Acme website" },
    });
    expect(button?.disabled).toBe(false);
  });

  it("posts the type and the label, and nothing else", async () => {
    // No ``templateVersion``: left out, the backend pins the agent to the
    // latest published version of the type, which is what "new" means.
    const api = fakeApi({
      [CATALOGUE]: { body: [aType()] },
      "POST /v1/agents": { status: 201, body: anAgent({ status: "draft" }) },
    });
    page();

    fireEvent.click(await screen.findByText("Marketing agent"));
    fireEvent.change(screen.getByLabelText("What this agent is called"), {
      target: { value: "  Acme website  " },
    });
    fireEvent.click(screen.getByText("Create draft"));

    await waitFor(() => expect(api.last(AGENTS, "POST")).toBeTruthy());
    expect(api.last(AGENTS, "POST")?.body).toEqual({
      templateKey: "marketing_v1",
      label: "Acme website",
    });
  });

  it("goes to the new agent, because settings are the next thing wanted", async () => {
    fakeApi({
      [CATALOGUE]: { body: [aType()] },
      "POST /v1/agents": { status: 201, body: anAgent({ id: "agent_new" }) },
    });
    page();

    fireEvent.click(await screen.findByText("Marketing agent"));
    fireEvent.change(screen.getByLabelText("What this agent is called"), {
      target: { value: "Acme website" },
    });
    fireEvent.click(screen.getByText("Create draft"));

    await waitFor(() =>
      expect(screen.getByTestId("location").textContent).toBe("/agents/agent_new"),
    );
  });

  it("marks the field the backend rejected", async () => {
    // The envelope's per-field problems, used as a form's own marks rather
    // than as a sentence at the top of the page.
    fakeApi({
      [CATALOGUE]: { body: [aType()] },
      "POST /v1/agents": {
        status: 422,
        body: {
          error: {
            code: "VALIDATION_FAILED",
            message: "That request was not valid",
            details: {
              problems: [{ path: "body.label", message: "String too long" }],
            },
          },
        },
      },
    });
    page();

    fireEvent.click(await screen.findByText("Marketing agent"));
    fireEvent.change(screen.getByLabelText("What this agent is called"), {
      target: { value: "Acme" },
    });
    fireEvent.click(screen.getByText("Create draft"));

    expect(await screen.findByText("String too long")).toBeTruthy();
    expect(screen.getByLabelText("What this agent is called").getAttribute("aria-invalid")).toBe(
      "true",
    );
  });

  it("shows a refusal that belongs to no field at the top", async () => {
    fakeApi({
      [CATALOGUE]: { body: [aType()] },
      "POST /v1/agents": {
        status: 409,
        body: {
          error: {
            code: "AGENT_LIMIT_REACHED",
            message: "This workspace already has 3 of those",
            details: { templateKey: "marketing_v1", limit: 3 },
          },
        },
      },
    });
    page();

    fireEvent.click(await screen.findByText("Marketing agent"));
    fireEvent.change(screen.getByLabelText("What this agent is called"), {
      target: { value: "Acme website" },
    });
    fireEvent.click(screen.getByText("Create draft"));

    expect(await screen.findAllByText("This workspace already has 3 of those")).toHaveLength(2);
  });
});
