/**
 * The knowledge screen: what is indexed, adding to it, and taking it away.
 *
 * Most of these assertions are about words rather than markup, and
 * deliberately. The three states of a source are the whole product here: a
 * document that was read and had no text in it is *finished*, and a screen
 * that draws that as "pending" sends somebody away to wait for a job that
 * already ran. The rest is on the wire -- that an upload goes out as
 * multipart under the field names the API reads, that a cap is applied before
 * 20 MB is sent, and that no workspace is ever named.
 */

import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { KnowledgePage } from "@/presentation/pages/KnowledgePage";

import { fakeApi } from "../support/api";
import { aSource, anIngested } from "../support/fixtures";
import { renderPage } from "../support/render";

const SOURCES = "/v1/knowledge/sources";
const REINDEX = "/v1/knowledge/reindex";

function withSources(...sources: ReturnType<typeof aSource>[]) {
  return fakeApi({ [SOURCES]: { body: sources } });
}

function open() {
  return renderPage(<KnowledgePage />, { token: "devtok", at: "/knowledge" });
}

/** A file, with a size a test can choose. Twenty megabytes of real bytes is
 *  not needed to prove a limit is applied to a number. */
function aFile(name: string, size: number, type = "application/pdf"): File {
  const file = new File(["x"], name, { type });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

function choose(file: File): void {
  const input = screen.getByLabelText("File");
  fireEvent.change(input, { target: { files: [file] } });
}

describe("the source list", () => {
  it("shows a source with what it costs and what it answers from", async () => {
    withSources(aSource());
    open();

    expect(await screen.findByText("Pricing.pdf")).toBeTruthy();
    expect(screen.getByText("Indexed")).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getByText("47 kB")).toBeTruthy();
    // The embedder a source is on is the only way to see which ones a
    // reindex would touch.
    expect(screen.getByText("voyage-3")).toBeTruthy();
  });

  it("says a document had no text, rather than that it is still waiting", async () => {
    // The distinction the backend draws with ``status``: this one was read,
    // and the answer is OCR or a different file, not patience.
    withSources(aSource({ passages: 0, embeddingModel: "voyage-3", status: "empty" }));
    open();

    expect(await screen.findByText("No text")).toBeTruthy();
    expect(screen.queryByText("Not indexed")).toBeNull();
  });

  it("marks one that nothing has been indexed from yet", async () => {
    withSources(aSource({ passages: 0, embeddingModel: "", status: "pending" }));
    open();

    expect(await screen.findByText("Not indexed")).toBeTruthy();
  });

  it("counts what is answering nothing, so it is not lost in the list", async () => {
    withSources(
      aSource(),
      aSource({ sourceId: "up-2", name: "Scan.pdf", passages: 0, status: "empty" }),
    );
    open();

    expect(await screen.findByText(/2 sources · 12 passages · 1 answering nothing/))
      .toBeTruthy();
  });

  it("shows where a crawled page came from", async () => {
    withSources(
      aSource({
        sourceId: "web-1",
        name: "Pricing",
        kind: "text/html",
        sourceUrl: "https://acme.test/pricing",
      }),
    );
    open();

    expect(await screen.findByText("https://acme.test/pricing")).toBeTruthy();
  });

  it("marks an approved answer, because retrieval ranks it higher", async () => {
    withSources(aSource({ approved: true }));
    open();

    expect(await screen.findByText("Approved")).toBeTruthy();
  });

  it("explains an empty workspace, and offers nothing to reindex", async () => {
    withSources();
    open();

    expect(await screen.findByText("Nothing to answer from yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Reindex" })).toBeNull();
  });
});

describe("uploading", () => {
  it("sends the file and the fields the API reads, as multipart", async () => {
    const api = fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + SOURCES]: { body: anIngested() },
    });
    open();
    await screen.findByText("Nothing to answer from yet");

    choose(aFile("Pricing.pdf", 48_000));
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Pricing, 2026" },
    });
    fireEvent.click(
      screen.getByLabelText(/An approved answer/),
    );
    fireEvent.click(screen.getByText("Upload"));

    await waitFor(() => expect(api.last(SOURCES, "POST")).toBeTruthy());
    const call = api.last(SOURCES, "POST");
    // The part is there, under the field name the API reads, with the type
    // the browser gave it. Not the filename: undici writes "blob" for a file
    // that is not its own File, which is this environment's seam and not
    // something the application controls -- and the name a citation shows is
    // sent as a field of its own, asserted below.
    expect(call?.files?.["file"]?.type).toBe("application/pdf");
    expect(call?.form?.["name"]).toBe("Pricing, 2026");
    // A form field is not a model field and gets no alias generator, so the
    // backend spells this one out: it reads ``approved`` as a string.
    expect(call?.form?.["approved"]).toBe("true");
    // Nothing was typed in it, so it is left out rather than sent empty.
    expect(call?.form?.["sourceId"]).toBeUndefined();
  });

  it("names the document after the file when nothing was typed", async () => {
    // The name is what a citation will show, so the client sends one rather
    // than leaving the server to read it off the multipart filename.
    const api = fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + SOURCES]: { body: anIngested() },
    });
    open();
    await screen.findByText("Nothing to answer from yet");

    choose(aFile("Pricing.pdf", 48_000));
    fireEvent.click(screen.getByText("Upload"));

    await waitFor(() =>
      expect(api.last(SOURCES, "POST")?.form?.["name"]).toBe("Pricing.pdf"),
    );
  });

  it("sends a chosen id under its camelCase name, which is how a replace works", async () => {
    const api = fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + SOURCES]: { body: anIngested() },
    });
    open();
    await screen.findByText("Nothing to answer from yet");

    choose(aFile("Pricing.pdf", 48_000));
    fireEvent.change(screen.getByLabelText(/Replace/), {
      target: { value: "pricing-sheet" },
    });
    fireEvent.click(screen.getByText("Upload"));

    await waitFor(() =>
      expect(api.last(SOURCES, "POST")?.form?.["sourceId"]).toBe("pricing-sheet"),
    );
  });

  it("refuses an id the API's pattern would refuse, without asking it", async () => {
    const api = fakeApi({ [SOURCES]: { body: [] } });
    open();
    await screen.findByText("Nothing to answer from yet");

    choose(aFile("Pricing.pdf", 48_000));
    fireEvent.change(screen.getByLabelText(/Replace/), {
      target: { value: "../../etc/passwd" },
    });

    expect(screen.getByText(/dashes and underscores only/)).toBeTruthy();
    expect(screen.getByText("Upload").closest("button")?.disabled).toBe(true);
    expect(api.to(SOURCES, "POST")).toHaveLength(0);
  });

  it("applies the size cap before uploading rather than after", async () => {
    // The server checks this too, chunk by chunk, and that is the check that
    // counts. This one saves someone a refusal they waited for.
    const api = fakeApi({ [SOURCES]: { body: [] } });
    open();
    await screen.findByText("Nothing to answer from yet");

    choose(aFile("Huge.pdf", 21 * 1024 * 1024));

    expect(screen.getByText(/The limit is 20 MB/)).toBeTruthy();
    fireEvent.click(screen.getByText("Upload"));
    expect(api.to(SOURCES, "POST")).toHaveLength(0);
  });

  it("reports a file that was stored and indexed, with what it cost", async () => {
    fakeApi({
      [SOURCES]: [{ body: [] }, { body: [aSource()] }],
      ["POST " + SOURCES]: { body: anIngested() },
    });
    open();
    await screen.findByText("Nothing to answer from yet");

    choose(aFile("Pricing.pdf", 48_000));
    fireEvent.click(screen.getByText("Upload"));

    expect(await screen.findByText(/12 passages from 9,400 characters/)).toBeTruthy();
    expect(screen.getByText("up-1")).toBeTruthy();
  });

  it("prints the API's own sentence when nothing was indexed", async () => {
    // Accepted, stored, and useless: the one outcome a green tick would lie
    // about. The words come from the server because they are advice.
    fakeApi({
      [SOURCES]: [{ body: [] }, { body: [aSource({ passages: 0, status: "empty" })] }],
      ["POST " + SOURCES]: {
        body: anIngested({
          indexed: false,
          passages: 0,
          skipped: "No text layer — this looks like a scan and needs OCR.",
        }),
      },
    });
    open();
    await screen.findByText("Nothing to answer from yet");

    choose(aFile("Scan.pdf", 48_000));
    fireEvent.click(screen.getByText("Upload"));

    expect(await screen.findByText(/this looks like a scan and needs OCR/)).toBeTruthy();
  });

  it("re-reads the list, because the server decides what is in it", async () => {
    // A crawl or an upload answers with a report, not with the records it
    // wrote. Rebuilding the list from one would be guessing.
    const api = fakeApi({
      [SOURCES]: [{ body: [] }, { body: [aSource()] }],
      ["POST " + SOURCES]: { body: anIngested() },
    });
    open();
    await screen.findByText("Nothing to answer from yet");

    choose(aFile("Pricing.pdf", 48_000));
    fireEvent.click(screen.getByText("Upload"));

    await waitFor(() => expect(api.to(SOURCES, "GET").length).toBeGreaterThan(1));
    // The badge belongs to the row rather than to the upload result, so it
    // says the list itself arrived.
    expect(await screen.findByText("Indexed")).toBeTruthy();
  });

  it("keeps what was typed when the upload was refused", async () => {
    fakeApi({
      [SOURCES]: { body: [] },
      ["POST " + SOURCES]: {
        status: 415,
        body: {
          error: {
            code: "DOCUMENT_UNSUPPORTED",
            message: "A .zip is not a document",
          },
        },
      },
    });
    open();
    await screen.findByText("Nothing to answer from yet");

    choose(aFile("Bundle.zip", 48_000, "application/zip"));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Bundle" } });
    fireEvent.click(screen.getByText("Upload"));

    expect(await screen.findByText("A .zip is not a document")).toBeTruthy();
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Bundle");
  });
});

describe("deleting a source", () => {
  it("takes two clicks, and the second one says what it removes", async () => {
    const api = fakeApi({
      [SOURCES]: { body: [aSource()] },
      "DELETE /v1/knowledge/sources/up-1": {
        body: { sourceId: "up-1", passagesRemoved: 12 },
      },
    });
    open();

    fireEvent.click(await screen.findByText("Delete"));
    expect(api.last("/v1/knowledge/sources/up-1", "DELETE")).toBeUndefined();

    // Unlike deleting an agent, this really does remove things.
    fireEvent.click(screen.getByText("Delete and drop 12 passages"));
    await waitFor(() =>
      expect(api.last("/v1/knowledge/sources/up-1", "DELETE")).toBeTruthy(),
    );
    expect(await screen.findByText(/gone, with 12 passages/)).toBeTruthy();
  });
});

describe("reindexing", () => {
  it("asks for a batch, because the whole workspace is a bill", async () => {
    const api = fakeApi({
      [SOURCES]: { body: [aSource()] },
      ["POST " + REINDEX]: { body: [anIngested()] },
    });
    open();

    await screen.findByText("Pricing.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Reindex" }));
    await waitFor(() => expect(api.last(REINDEX, "POST")).toBeTruthy());
    expect(api.last(REINDEX, "POST")?.search).toBe("?limit=50");
    expect(await screen.findByText(/1 of 1 re-embedded, 12 passages/)).toBeTruthy();
  });

  it("asks for everything when the limit is cleared", async () => {
    const api = fakeApi({
      [SOURCES]: { body: [aSource()] },
      ["POST " + REINDEX]: { body: [] },
    });
    open();
    await screen.findByText("Pricing.pdf");

    fireEvent.change(screen.getByLabelText(/How many/), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Reindex" }));

    await waitFor(() => expect(api.last(REINDEX, "POST")).toBeTruthy());
    expect(api.last(REINDEX, "POST")?.search).toBe("");
  });

  it("says plainly when there was nothing to do", async () => {
    // The common case, and the one a spinner followed by silence makes look
    // like a failure.
    fakeApi({
      [SOURCES]: { body: [aSource()] },
      ["POST " + REINDEX]: { body: [] },
    });
    open();

    await screen.findByText("Pricing.pdf");
    fireEvent.click(screen.getByRole("button", { name: "Reindex" }));
    expect(
      await screen.findByText("Everything is already on the current embedding model."),
    ).toBeTruthy();
  });
});

describe("the tenancy rule, from this side", () => {
  it("never names a workspace, in a path, a query, a body or a form", async () => {
    const api = fakeApi({
      [SOURCES]: { body: [aSource()] },
      ["POST " + SOURCES]: { body: anIngested() },
    });
    open();
    await screen.findByText("Pricing.pdf");

    choose(aFile("Pricing.pdf", 48_000));
    fireEvent.click(screen.getByText("Upload"));
    await waitFor(() => expect(api.last(SOURCES, "POST")).toBeTruthy());

    for (const call of api.calls) {
      expect(/workspace/i.test(call.url)).toBe(false);
      expect(/workspace/i.test(JSON.stringify(call.body ?? {}))).toBe(false);
      expect(/workspace/i.test(JSON.stringify(call.form ?? {}))).toBe(false);
      expect(call.authorization).toBe("Bearer devtok");
    }
  });
});
