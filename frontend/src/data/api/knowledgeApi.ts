/**
 * A workspace's knowledge: the list, uploads, crawls, reindexing, deletes,
 * and the robots check.
 *
 * Four decisions in here are not obvious.
 *
 * **The upload is the one request in this application that is not JSON.** It
 * goes out as ``FormData``, and the content type is deliberately *not* set:
 * a multipart body is only parseable with the boundary the browser invents,
 * so writing the header by hand is how an upload becomes a 422 that looks
 * like a backend bug. ``fetchBaseQuery`` leaves a ``FormData`` body alone for
 * exactly this reason, so it is enough to hand it one.
 *
 * **Crawling and reindexing invalidate the source list, and nothing is
 * written back by hand.** Unlike an agent write, these answer with a report
 * rather than with the records they changed -- a crawl of forty pages is
 * forty sources, and reconstructing the list from a report would be guessing
 * what the server now holds. A refetch is cheap and it is the truth.
 *
 * **``robots`` is a query, not a mutation, and it is used lazily.** It
 * fetches somebody else's file, so it is a read and it may be cached per URL;
 * but nothing should fetch it on render, because a crawl form sitting open is
 * not a reason to hit a stranger's server. ``useLazyRobotsQuery`` is the
 * shape that gives both.
 *
 * **The original is downloaded through here, not linked to.** A plain
 * ``<a href>`` carries no ``Authorization`` header, so the browser would be
 * sent to a 401. It arrives as a blob and the component saves it.
 *
 * Nothing here names a workspace. The backend derives it from the credential,
 * which is what makes "my sources" a question this API can answer safely.
 */

import { baseApi } from "@/data/client/baseApi";
import { LIST } from "@/data/client/tags";
import type {
  CrawlBody,
  CrawlReport,
  Deleted,
  Ingested,
  Robots,
  Source,
  UploadBody,
} from "@/data/types/knowledge";

/** The cap the API enforces while reading the body, mirrored so a 20 MB
 *  file can be refused before it is uploaded rather than after. */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;

/** What a caller-chosen source id may look like, as the API states it. */
export const SOURCE_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/;

export interface ReindexBody {
  /** 0 means everything. Batching exists because a thousand documents is a
   *  bill and a very long request. */
  limit?: number;
}

export const knowledgeApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    sources: build.query<Source[], void>({
      query: () => "/v1/knowledge/sources",
      providesTags: (sources) => [
        { type: "Source" as const, id: LIST },
        ...(sources ?? []).map((one) => ({
          type: "Source" as const,
          id: one.sourceId,
        })),
      ],
    }),

    uploadSource: build.mutation<Ingested, UploadBody>({
      query: ({ file, name = "", sourceId = "", approved = false }) => {
        const form = new FormData();
        form.append("file", file);
        form.append("name", name);
        // Only when chosen. An empty string is accepted -- the API allows it
        // precisely because a browser form sends one for a field nobody
        // filled in -- but leaving it out says what is meant.
        if (sourceId) form.append("sourceId", sourceId);
        form.append("approved", approved ? "true" : "false");
        // ``agentInstanceIds`` is left out, which means the whole workspace.
        // Scoping a document to particular agents is a thing the API can do
        // and this screen does not yet ask about.
        return { url: "/v1/knowledge/sources", method: "POST", body: form };
      },
      invalidatesTags: [{ type: "Source", id: LIST }],
    }),

    deleteSource: build.mutation<Deleted, string>({
      query: (sourceId) => ({
        url: `/v1/knowledge/sources/${sourceId}`,
        method: "DELETE",
      }),
      invalidatesTags: (_deleted, _error, sourceId) => [
        { type: "Source" as const, id: sourceId },
        { type: "Source" as const, id: LIST },
      ],
    }),

    reindex: build.mutation<Ingested[], ReindexBody | void>({
      query: (body) => ({
        url: "/v1/knowledge/reindex",
        method: "POST",
        params: body?.limit ? { limit: body.limit } : {},
      }),
      invalidatesTags: [{ type: "Source", id: LIST }],
    }),

    crawl: build.mutation<CrawlReport, CrawlBody>({
      query: (body) => ({ url: "/v1/knowledge/crawl", method: "POST", body }),
      invalidatesTags: [{ type: "Source", id: LIST }],
    }),

    robots: build.query<Robots, string>({
      query: (url) => ({ url: "/v1/knowledge/robots", params: { url } }),
      // Not tagged: it is a fact about somebody else's server, and nothing
      // this application does can change it. Re-checking is a click.
    }),

    originalOf: build.mutation<Blob, string>({
      query: (sourceId) => ({
        url: `/v1/knowledge/sources/${sourceId}/original`,
        // The bytes as they were uploaded, so the handler must not try to
        // read them as JSON. A refusal arrives as a blob too, which is why
        // the caller shows its own words for a failed download rather than
        // the envelope's -- there is none to read.
        responseHandler: (response) => response.blob(),
      }),
    }),
  }),
});

export const {
  useSourcesQuery,
  useUploadSourceMutation,
  useDeleteSourceMutation,
  useReindexMutation,
  useCrawlMutation,
  useLazyRobotsQuery,
  useOriginalOfMutation,
} = knowledgeApi;
