/**
 * What a workspace has given its agent, as the API describes it.
 *
 * A mirror of the views in ``api/routes/knowledge.py``. Three of these fields
 * carry more meaning than their type suggests, and every screen in this chunk
 * is built on them:
 *
 * ``Source.status``
 *     ``indexed`` | ``empty`` | ``pending``. A document that failed to parse
 *     looks exactly like one nothing has happened to yet, so the backend says
 *     which: ``empty`` means it *was* read and there was no text in it -- a
 *     blank page, or a scan with no text layer, which needs OCR rather than a
 *     retry. The list has to show those differently or the honest answer
 *     ("there is nothing in this PDF") reads as "try again later".
 * ``Ingested.skipped``
 *     prose, set when nothing was indexed and that is not an error. Shown to
 *     whoever uploaded it, because they are the only person who can act on it.
 * ``CrawlReport.stoppedEarly``
 *     set when the crawl ran out of budget rather than out of links. The
 *     difference between "that is the whole site" and "raise the cap" is the
 *     one thing a crawl report has to answer.
 */

/** ``indexed`` and ``empty`` are both finished states. Only one is useful. */
export type SourceStatus = "indexed" | "empty" | "pending";

export interface Source {
  sourceId: string;
  name: string;
  /** The content type it arrived as. ``text/html`` for a crawled page. */
  kind: string;
  bytesStored: number;
  passages: number;
  /** The embedder that produced the passages. Empty when none have been. */
  embeddingModel: string;
  /** Where it was crawled from, or null for an upload. */
  sourceUrl: string | null;
  /** An official answer: it outranks ordinary passages at retrieval. */
  approved: boolean;
  status: SourceStatus;
}

/** The answer to an upload, and to each document a reindex touched. */
export interface Ingested {
  sourceId: string;
  sourceName: string;
  passages: number;
  characters: number;
  model: string;
  kind: string;
  indexed: boolean;
  /** Why nothing was indexed, when that is not an error. Often empty. */
  skipped: string;
}

export interface CrawledPage {
  url: string;
  depth: number;
  title: string;
  sourceId: string;
  passages: number;
  /** Fetched and identical to what is indexed: nothing was embedded again. */
  unchanged: boolean;
}

export interface SkippedPage {
  url: string;
  /** Prose. A person is deciding whether it matters. */
  reason: string;
}

export interface CrawlReport {
  startUrl: string;
  robotsPresent: boolean;
  stoppedEarly: string;
  indexedPages: number;
  passages: number;
  pages: CrawledPage[];
  skipped: SkippedPage[];
}

export interface Robots {
  url: string;
  /** Null when there is none, or it could not be read -- which allows
   *  everything, deliberately, for a site the customer owns. */
  text: string | null;
}

export interface Deleted {
  sourceId: string;
  passagesRemoved: number;
}

/**
 * An upload. Not JSON: it goes out as multipart, because one of the fields is
 * a file.
 *
 * ``sourceId`` is how a document is *replaced* rather than duplicated, so it
 * is optional and the server generates one when it is absent. A client that
 * always chose its own would be a client that can collide with a document
 * somebody else uploaded.
 */
export interface UploadBody {
  file: File;
  /** What a citation will show. The file's own name when left empty. */
  name?: string;
  sourceId?: string;
  approved?: boolean;
}

/**
 * A crawl.
 *
 * Every limit is optional, and leaving one out means "use the configured
 * one". Sending one may only make the crawl *gentler*: the server takes the
 * lower of pages and depth and the longer of the delays, so a request cannot
 * raise a cap on how hard this service hits somebody else's web server.
 */
export interface CrawlBody {
  startUrl: string;
  maxPages?: number;
  maxDepth?: number;
  delaySeconds?: number;
}
