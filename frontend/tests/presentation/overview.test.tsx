/**
 * The scaffold's one claim: the layers are connected.
 *
 * A page asks a hook, the hook reaches the data layer, the data layer adds
 * the credential and calls the real path, and what comes back is drawn. If
 * this passes, the next chunk is only screens.
 */

import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OverviewPage } from "@/presentation/pages/OverviewPage";

import { fakeApi } from "../support/api";
import { renderPage } from "../support/render";

const READY = { body: { status: "ready", records: "memory" } };
const CATALOGUE = { body: [] };

describe("the overview page", () => {
  it("draws what the backend answered", async () => {
    fakeApi({ "/readyz": READY, "/v1/catalogue": CATALOGUE });
    renderPage(<OverviewPage />, { token: "devtok" });

    expect(await screen.findByText("ready")).toBeTruthy();
    expect(screen.getByText("memory")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Token accepted")).toBeTruthy());
  });

  it("sends the token from the session and never a workspace", async () => {
    const { calls } = fakeApi({ "/readyz": READY, "/v1/catalogue": CATALOGUE });
    renderPage(<OverviewPage />, { token: "devtok" });
    await waitFor(() => expect(screen.getByText("Token accepted")).toBeTruthy());

    const catalogue = calls.find((one) => one.url.endsWith("/v1/catalogue"));
    expect(catalogue?.authorization).toBe("Bearer devtok");
    // The tenancy rule, from this side: the workspace is the token's business.
    expect(calls.every((one) => !/workspace/i.test(one.url))).toBe(true);
  });

  it("asks for readiness without a credential at all", async () => {
    const { calls } = fakeApi({ "/readyz": READY, "/v1/catalogue": CATALOGUE });
    renderPage(<OverviewPage />);

    expect(await screen.findByText("ready")).toBeTruthy();
    const readiness = calls.find((one) => one.url.endsWith("/readyz"));
    expect(readiness?.authorization).toBeNull();
    // Nothing authenticated was attempted, so an empty session shows the
    // token form rather than a failure that reads like a broken server.
    expect(calls.some((one) => one.url.includes("/v1/"))).toBe(false);
    expect(screen.getByText("No token set")).toBeTruthy();
  });

  it("says what a rejected token is, and what a platform token is", async () => {
    fakeApi({
      "/readyz": READY,
      "/v1/catalogue": {
        status: 401,
        body: { error: { code: "UNAUTHENTICATED", message: "No" } },
      },
    });
    renderPage(<OverviewPage />, { token: "wrong" });
    expect(await screen.findByText("Token rejected")).toBeTruthy();
  });

  it("tells a platform token apart from a bad one", async () => {
    fakeApi({
      "/readyz": READY,
      "/v1/catalogue": {
        status: 403,
        body: {
          error: {
            code: "FORBIDDEN",
            message: "This token is not scoped to a workspace",
          },
        },
      },
    });
    renderPage(<OverviewPage />, { token: "platform" });
    expect(await screen.findByText("Platform token: no workspace")).toBeTruthy();
  });

  it("explains an unreachable backend as the two things it can be", async () => {
    // A request that never arrived: no status, no envelope. The two causes
    // are indistinguishable from in here -- the server is down, or its
    // admin origin list does not include this one -- so the page says both
    // rather than picking the wrong one.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    renderPage(<OverviewPage />, { token: "devtok" });

    // Both panels say it, because both calls failed the same way.
    expect(await screen.findAllByText("Could not reach the server.")).toHaveLength(2);
    expect(screen.getByText(/CB_ADMIN_ORIGINS/)).toBeTruthy();
  });
});
