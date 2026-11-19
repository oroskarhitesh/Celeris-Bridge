/**
 * A stand-in for the backend: a fetch that answers from a table of paths.
 *
 * Keys are either ``"METHOD /path"`` or just ``"/path"``, so a test only
 * spells the method when it is asserting on one. A list of answers is
 * consumed in order and the last one repeats, which is how a test says "the
 * second read sees the write". ``hangs()`` never answers at all, which is how
 * a test pins the cache still long enough to assert what a mutation put in
 * it.
 *
 * Everything that goes out is recorded -- the search string, the header, the
 * parsed body -- because the things worth testing in a data layer are exactly
 * those: that the credential was attached, that a revision was sent, that a
 * field went out under its camelCase name, and that no workspace was ever
 * named. The paths are the real ones, so renaming an endpoint fails here too.
 *
 * **A multipart body is parsed here, by hand.** The upload endpoint is the one
 * place this application does not send JSON, and ``fetchBaseQuery`` hands
 * ``fetch`` a built ``Request`` rather than the options it was given -- so by
 * the time anything here sees it, the form is already serialised. Reading it
 * back with ``Request.formData()`` would be the obvious move and it does not
 * work in this environment: ``FormData`` comes from undici and ``File`` comes
 * from jsdom, so undici writes the body happily and then refuses to parse its
 * own output, because the files in it are not the ``File`` it recognises. The
 * lines below are the price of not making the suite depend on that pairing.
 * Files are recorded by name and type rather than by content, which is what an
 * assertion about an upload needs; what is *in* the file is the backend's
 * business and is tested there.
 */

import { vi } from "vitest";

export interface Answer {
  status?: number;
  body: unknown;
}

interface Hang {
  hang: true;
}

export type Reply = Answer | Hang;

export function hangs(): Hang {
  return { hang: true };
}

/** A file as it went out. Not its bytes: its identity. */
export interface SentFile {
  name: string;
  type: string;
}

export interface Call {
  url: string;
  path: string;
  search: string;
  method: string;
  authorization: string | null;
  /** The parsed JSON body, for the requests that send one. */
  body: unknown;
  /** The text fields of a multipart body, for the one request that does not. */
  form?: Record<string, string>;
  /** The files of a multipart body, by field name. */
  files?: Record<string, SentFile>;
}

const NEVER = new Promise<Response>(() => {});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

interface Multipart {
  form: Record<string, string>;
  files: Record<string, SentFile>;
}

/** ``name="x"`` or ``filename="y"`` out of one part's headers. */
function quoted(headers: string, key: string): string | undefined {
  const found = new RegExp(key + '="([^"]*)"').exec(headers);
  return found ? found[1] : undefined;
}

/**
 * The fields of a multipart body, from the text that was sent.
 *
 * Deliberately strict about the shape rather than lenient: it parses one
 * writer's output -- the one the application actually uses -- and a part it
 * cannot read is a part it must not silently drop, because the assertions
 * built on this are about fields being present.
 */
function partsOf(text: string, boundary: string): Multipart {
  const form: Record<string, string> = {};
  const files: Record<string, SentFile> = {};

  for (const chunk of text.split("--" + boundary)) {
    const at = chunk.indexOf("\r\n\r\n");
    if (at < 0) continue;
    const headers = chunk.slice(0, at);
    const field = quoted(headers, "name");
    if (!field) continue;
    const value = chunk.slice(at + 4).replace(/\r\n$/, "");
    const filename = quoted(headers, "filename");
    if (filename === undefined) {
      form[field] = value;
      continue;
    }
    const type = /content-type:\s*([^\r\n;]+)/i.exec(headers)?.[1];
    files[field] = { name: filename, type: type ? type.trim() : "" };
  }

  return { form, files };
}

export interface FakeApi {
  calls: Call[];
  /** Every call to one path, in order, method included. */
  to(path: string, method?: string): Call[];
  /** The most recent call to one path, or undefined. */
  last(path: string, method?: string): Call | undefined;
}

export function fakeApi(answers: Record<string, Reply | Reply[]>): FakeApi {
  const calls: Call[] = [];
  const taken = new Map<string, number>();

  const chosen = (key: string): Reply | undefined => {
    const entry = answers[key];
    if (!entry) return undefined;
    if (!Array.isArray(entry)) return entry;
    const at = taken.get(key) ?? 0;
    taken.set(key, at + 1);
    return entry[Math.min(at, entry.length - 1)];
  };

  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    const sent = request.headers.get("content-type") ?? "";
    const boundary = /boundary=([^\s;]+)/.exec(sent)?.[1];
    const text = await request.clone().text();
    const multipart =
      sent.startsWith("multipart/form-data") && boundary
        ? partsOf(text, boundary)
        : undefined;
    calls.push({
      url: request.url,
      path: url.pathname,
      search: url.search,
      method: request.method,
      authorization: request.headers.get("authorization"),
      body: text && !multipart ? JSON.parse(text) : undefined,
      ...(multipart ? { form: multipart.form, files: multipart.files } : {}),
    });

    const reply = chosen(request.method + " " + url.pathname) ?? chosen(url.pathname);
    if (!reply) {
      return json(404, { error: { code: "HTTP_ERROR", message: "Not Found" } });
    }
    if ("hang" in reply) return NEVER;
    return json(reply.status ?? 200, reply.body);
  });

  vi.stubGlobal("fetch", fetcher);

  const matching = (path: string, method?: string) =>
    calls.filter((one) => one.path === path && (!method || one.method === method));

  return {
    calls,
    to: matching,
    last: (path, method) => matching(path, method).at(-1),
  };
}
