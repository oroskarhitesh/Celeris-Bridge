/**
 * Crawling a site, and the report that explains the result.
 *
 * A crawl report exists to answer one question -- "why only four pages?" --
 * and there are three honest answers: the site is that small, the budget ran
 * out, or the site's own rules said no. Each has a different fix, so each is
 * asserted here.
 *
 * On the wire, one rule matters: what this form sends may only make a crawl
 * gentler. An untouched field is left out rather than sent as a zero, because
 * a zero is not "the configured limit" -- it is a crawl of no pages.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { KnowledgePage } from "@/presentation/pages/KnowledgePage";

import { fakeApi } from "../support/api";
import { aCrawlReport, aSource } from "../support/fixtures";
import { renderPage } from "../support/render";

const SOURCES = "/v1/knowledge/sources";
const CRAWL = "/v1/knowledge/crawl";
const ROBOTS = "/v1/knowledge/robots";

function open() {
  return renderPage(<KnowledgePage />, { token: "devtok", at: "/knowledge?tab=crawl" });
}

function url(value = "https://acme.test/docs"): void {
  fireEvent.change(screen.getByLabelText("Start URL"), { target: { value } });
}

describe("starting a crawl", () => {
  it("sends only the URL when no limit was typed", async () => {
    // Blank means "use the configured limit", which is a thing this client
    // cannot see and must not guess at.
    const api = fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + CRAWL]: { body: aCrawlReport() },
    });
    open();

    url();
    fireEvent.click(screen.getByText("Start crawl"));

    await waitFor(() => expect(api.last(CRAWL, "POST")).toBeTruthy());
    expect(api.last(CRAWL, "POST")?.body).toEqual({
      startUrl: "https://acme.test/docs",
    });
  });

  it("sends the limits it was given, under the names the API reads", async () => {
    const api = fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + CRAWL]: { body: aCrawlReport() },
    });
    open();

    url();
    fireEvent.change(screen.getByLabelText("Pages, at most"), {
      target: { value: "10" },
    });
    fireEvent.change(screen.getByLabelText("Depth, at most"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByLabelText(/Seconds between pages/), {
      target: { value: "1.5" },
    });
    fireEvent.click(screen.getByText("Start crawl"));

    await waitFor(() => expect(api.last(CRAWL, "POST")).toBeTruthy());
    expect(api.last(CRAWL, "POST")?.body).toEqual({
      startUrl: "https://acme.test/docs",
      maxPages: 10,
      maxDepth: 1,
      delaySeconds: 1.5,
    });
  });

  it("keeps a depth of zero, which is a real request", async () => {
    // One page and no links followed. The only zero in this form that means
    // something, which is why "falsy" is the wrong test for an empty field.
    const api = fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + CRAWL]: { body: aCrawlReport() },
    });
    open();

    url();
    fireEvent.change(screen.getByLabelText("Depth, at most"), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByText("Start crawl"));

    await waitFor(() => expect(api.last(CRAWL, "POST")).toBeTruthy());
    expect(api.last(CRAWL, "POST")?.body).toEqual({
      startUrl: "https://acme.test/docs",
      maxDepth: 0,
    });
  });

  it("refuses a number the API would refuse, without asking it", async () => {
    const api = fakeApi({ [SOURCES]: { body: [] } });
    open();

    url();
    fireEvent.change(screen.getByLabelText("Depth, at most"), {
      target: { value: "11" },
    });

    expect(screen.getByText("Between 0 and 10.")).toBeTruthy();
    expect(screen.getByText("Start crawl").closest("button")?.disabled).toBe(true);
    expect(api.to(CRAWL, "POST")).toHaveLength(0);
  });

  it("will not start without a URL", async () => {
    fakeApi({ [SOURCES]: { body: [] } });
    open();

    expect(screen.getByText("Start crawl").closest("button")?.disabled).toBe(true);
  });

  it("says that the crawl runs inside the request", async () => {
    // The backend's known debt, said out loud: a long crawl can outlast a
    // proxy, and the advice on the screen is the real advice.
    fakeApi({ [SOURCES]: { body: [] } });
    open();

    expect(screen.getByText(/A crawl runs inside this request/)).toBeTruthy();
  });

  it("re-reads the source list, because each page became one", async () => {
    const api = fakeApi({
      [SOURCES]: [{ body: [] }, { body: [aSource({ sourceId: "web-1" })] }],
      ["POST " + CRAWL]: { body: aCrawlReport() },
    });
    open();
    await waitFor(() => expect(api.to(SOURCES, "GET").length).toBe(1));

    url();
    fireEvent.click(screen.getByText("Start crawl"));

    await waitFor(() => expect(api.to(SOURCES, "GET").length).toBeGreaterThan(1));
  });

  it("blames the right server when the customer's site refuses us", async () => {
    // A 502 with the backend's words. Not a bug in this service, and it must
    // not read like one.
    fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + CRAWL]: {
        status: 502,
        body: {
          error: {
            code: "FETCH_FAILED",
            message: "acme.test answered 403 for /docs",
          },
        },
      },
    });
    open();

    url();
    fireEvent.click(screen.getByText("Start crawl"));

    expect(await screen.findByText(/answered 403/)).toBeTruthy();
  });
});

describe("the crawl report", () => {
  it("counts what was indexed, and what was already up to date", async () => {
    fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + CRAWL]: {
        body: aCrawlReport({
          pages: [
            {
              url: "https://acme.test/docs",
              depth: 0,
              title: "Docs",
              sourceId: "web-1",
              passages: 5,
              unchanged: false,
            },
            {
              url: "https://acme.test/docs/pricing",
              depth: 1,
              title: "Pricing",
              sourceId: "web-2",
              passages: 0,
              unchanged: true,
            },
          ],
        }),
      },
    });
    open();

    url();
    fireEvent.click(screen.getByText("Start crawl"));

    expect(await screen.findByText("Crawl report")).toBeTruthy();
    expect(screen.getByText("2 pages")).toBeTruthy();
    // On a second run most pages are unchanged, and a report that did not
    // say so would look like a crawl that did nothing.
    expect(screen.getByText(/· unchanged/)).toBeTruthy();
  });

  it("says when it ran out of budget rather than out of links", async () => {
    // The difference between "that is the whole site" and "raise the cap".
    fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + CRAWL]: {
        body: aCrawlReport({ stoppedEarly: "Reached the 40 page limit" }),
      },
    });
    open();

    url();
    fireEvent.click(screen.getByText("Start crawl"));

    expect(await screen.findByText(/Reached the 40 page limit/)).toBeTruthy();
  });

  it("lists what it skipped, with the server's reason for each", async () => {
    fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + CRAWL]: {
        body: aCrawlReport({
          skipped: [
            { url: "https://acme.test/admin", reason: "Disallowed by robots.txt" },
            { url: "https://cdn.other.test/a", reason: "Not the same site" },
          ],
        }),
      },
    });
    open();

    url();
    fireEvent.click(screen.getByText("Start crawl"));

    expect(await screen.findByText(/Disallowed by robots.txt/)).toBeTruthy();
    expect(screen.getByText(/Not the same site/)).toBeTruthy();
  });

  it("says whether the site had rules at all", async () => {
    fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + CRAWL]: { body: aCrawlReport({ robotsPresent: false }) },
    });
    open();

    url();
    fireEvent.click(screen.getByText("Start crawl"));

    expect(await screen.findByText("none found")).toBeTruthy();
  });
});

describe("the robots check", () => {
  it("fetches nothing until it is asked to", async () => {
    // It reaches a stranger's server. A form sitting open is not a reason to
    // make that request.
    const api = fakeApi({ [SOURCES]: { body: [] } });
    open();

    expect(api.to(ROBOTS, "GET")).toHaveLength(0);
  });

  it("asks for the URL it was given, and prints the file verbatim", async () => {
    const api = fakeApi({
      [SOURCES]: { body: [] },
      [ROBOTS]: {
        body: {
          url: "https://acme.test/docs",
          text: "User-agent: *\nDisallow: /admin\n",
        },
      },
    });
    open();

    fireEvent.change(screen.getByLabelText(/Any URL on the site/), {
      target: { value: "https://acme.test/docs" },
    });
    fireEvent.click(screen.getByText("Check"));

    await waitFor(() => expect(api.last(ROBOTS, "GET")).toBeTruthy());
    expect(api.last(ROBOTS, "GET")?.search).toBe("?url=https%3A%2F%2Facme.test%2Fdocs");
    // Verbatim, because the crawler obeys the file and a summary here could
    // disagree with what it actually did.
    expect(await screen.findByText(/Disallow: \/admin/)).toBeTruthy();
  });

  it("treats an absent file as the fact it is, not as a failure", async () => {
    fakeApi({
      [SOURCES]: { body: [] },
      [ROBOTS]: { body: { url: "https://acme.test", text: null } },
    });
    open();

    fireEvent.change(screen.getByLabelText(/Any URL on the site/), {
      target: { value: "https://acme.test" },
    });
    fireEvent.click(screen.getByText("Check"));

    expect(await screen.findByText(/Everything is allowed/)).toBeTruthy();
  });
});
