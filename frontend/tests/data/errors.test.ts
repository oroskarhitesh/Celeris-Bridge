/**
 * Reading the backend's envelope, including the parts a form depends on.
 *
 * These are the shapes the API actually sends -- two different keys for
 * per-field problems -- so this is where that difference is absorbed once
 * instead of in each form.
 */

import { describe, expect, it } from "vitest";

import {
  codeOf,
  foundRevisionOf,
  messageOf,
  problemsOf,
  statusOf,
} from "@/data/client/errors";

const refusal = (status: number, body: unknown) => ({ status, data: body });

describe("reading a failure", () => {
  it("takes the code and the message from the envelope", () => {
    const error = refusal(409, {
      error: { code: "ALREADY_EXISTS", message: "That key is taken" },
    });
    expect(codeOf(error)).toBe("ALREADY_EXISTS");
    expect(messageOf(error)).toBe("That key is taken");
    expect(statusOf(error)).toBe(409);
  });

  it("does not quote the server on a 500", () => {
    // The backend sends no words on a 500 on purpose. Repeating whatever did
    // arrive would put an internal failure in front of a customer.
    const error = refusal(500, {
      error: { code: "INTERNAL", message: "Something went wrong" },
    });
    expect(messageOf(error)).toBe("Something went wrong on the server.");
  });

  it("does quote a 502 about somebody else's server", () => {
    // The one exception to the rule above, and the reason it exists: a
    // FETCH_FAILED is the backend reporting that the *customer's* site
    // refused the crawler. That sentence is the whole explanation for a crawl
    // that indexed nothing, and "something went wrong on the server" would
    // point the blame at a server that is working.
    const error = refusal(502, {
      error: { code: "FETCH_FAILED", message: "acme.test answered 403 for /docs" },
    });
    expect(messageOf(error)).toBe("acme.test answered 403 for /docs");
  });

  it("still refuses to quote an unexpected 5xx", () => {
    const error = refusal(503, {
      error: { code: "SOMETHING_NEW", message: "redis: connection refused" },
    });
    expect(messageOf(error)).toBe("Something went wrong on the server.");
  });

  it("has words for a request that never arrived", () => {
    expect(messageOf({ status: "FETCH_ERROR", error: "Failed to fetch" })).toBe(
      "Could not reach the server.",
    );
  });

  it("reads problems from a rejected body", () => {
    const error = refusal(422, {
      error: {
        code: "VALIDATION_FAILED",
        message: "That request was not valid",
        details: { problems: [{ path: "templateKey", message: "Field required" }] },
      },
    });
    expect(problemsOf(error)).toEqual([
      { path: "templateKey", message: "Field required" },
    ]);
  });

  it("reads them from rejected overrides too, where the key is different", () => {
    const error = refusal(400, {
      error: {
        code: "OVERRIDES_REJECTED",
        message: "Some of those values were not accepted",
        details: {
          rejections: [
            { path: "greeting", code: "too_long", message: "At most 300 characters" },
          ],
        },
      },
    });
    expect(problemsOf(error)).toEqual([
      { path: "greeting", code: "too_long", message: "At most 300 characters" },
    ]);
  });

  it("surfaces the revision a lost race found", () => {
    const error = refusal(409, {
      error: {
        code: "REVISION_CONFLICT",
        message: "Expected revision 3, found 4",
        details: { expected: 3, found: 4 },
      },
    });
    expect(foundRevisionOf(error)).toBe(4);
    expect(foundRevisionOf(refusal(409, { error: { code: "ALREADY_EXISTS", message: "" } }))).toBeUndefined();
  });

  it("is not fooled by a body that is not the envelope", () => {
    expect(codeOf(refusal(502, "<html>gateway</html>"))).toBeUndefined();
    expect(messageOf(refusal(502, "<html>gateway</html>"))).toBe(
      "Something went wrong on the server.",
    );
    expect(problemsOf(refusal(400, { error: { code: "X", message: "", details: { problems: "no" } } }))).toEqual([]);
  });
});
