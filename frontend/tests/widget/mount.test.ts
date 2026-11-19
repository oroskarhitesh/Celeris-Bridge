/**
 * Reading the embed.
 *
 * Small surface, and every case in it is a way a snippet pasted into a CMS
 * can be wrong. The important one is the last: a missing id has to fail
 * loudly in the console rather than leave a developer with a blank corner
 * and nothing to search for.
 */

import { describe, expect, it } from "vitest";

import { configFrom } from "@/widget/mount";

function aScript(attributes: Record<string, string>): HTMLScriptElement {
  const script = document.createElement("script");
  for (const [name, value] of Object.entries(attributes)) {
    script.setAttribute(name, value);
  }
  return script;
}

describe("the widget's configuration", () => {
  it("reads the ids from the script tag", () => {
    const config = configFrom(
      aScript({
        src: "https://cdn.example/widget/widget.js",
        "data-workspace": "ws_live",
        "data-instance": "agent_7",
      }),
      "",
    );

    expect(config).toEqual({
      // Defaulted to wherever the script was served from: one fewer
      // attribute to get wrong in a snippet somebody pastes.
      api: "https://cdn.example",
      workspaceId: "ws_live",
      instanceId: "agent_7",
    });
  });

  it("lets the API host be named outright", () => {
    const config = configFrom(
      aScript({
        src: "https://cdn.example/widget.js",
        "data-workspace": "ws_live",
        "data-instance": "agent_7",
        "data-api": "https://api.acme.test/",
      }),
      "",
    );

    // Trailing slash removed, because every path in the client starts with
    // one and "//v1/widget" is a 404 nobody enjoys finding.
    expect(config?.api).toBe("https://api.acme.test");
  });

  it("falls back to the query string, which is how the dev page passes them", () => {
    const config = configFrom(
      null,
      "?workspace=ws_dev&instance=agent_1&api=http://127.0.0.1:8000",
    );

    expect(config).toEqual({
      api: "http://127.0.0.1:8000",
      workspaceId: "ws_dev",
      instanceId: "agent_1",
    });
  });

  it("prefers the attributes over the query string", () => {
    // The page's URL belongs to the customer's site and may carry anything.
    // The script tag is the embed, so the embed wins.
    const config = configFrom(
      aScript({
        src: "https://cdn.example/widget.js",
        "data-workspace": "ws_live",
        "data-instance": "agent_7",
      }),
      "?workspace=ws_other&instance=agent_other",
    );

    expect(config?.workspaceId).toBe("ws_live");
    expect(config?.instanceId).toBe("agent_7");
  });

  it("refuses to guess when an id is missing", () => {
    expect(configFrom(aScript({ "data-workspace": "ws_live" }), "")).toBeNull();
    expect(configFrom(null, "?instance=agent_1")).toBeNull();
  });
});
