"""
Crawling a site into a workspace's knowledge.

The loop is breadth-first, and that is the one decision everything else hangs
off. A crawl is always cut short by a cap -- nobody indexes a whole site -- so
the question is not which pages are missed but which are kept, and breadth
first keeps the shallow ones. The front page, then everything the front page
links to, then their links: that order follows what the site itself presents
as important, and the pages a visitor asks about are rarely nine clicks down.

Three kinds of duplicate are collapsed, because each one is a page embedded
twice and cited twice:

* **the same URL** written differently -- normalised before anything else,
* **two URLs that redirect to one page** -- the final URL is what is recorded,
* **the same content at two URLs** -- a checksum, which catches the print
  version, the trailing-slash twin, and the ``?lang=en`` default.

And a page whose content has not changed since the last crawl is not embedded
again. On a nightly crawl that is nearly all of them, and it is the difference
between a bill that scales with the site and one that scales with its edits.

Nothing here does I/O. The fetcher, the document store, the embedder and the
index all arrive as ports.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from celeris_bridge.domain.knowledge.ingestion import (
    IngestionResult,
    checksum,
    ingest,
)
from celeris_bridge.domain.knowledge.extraction import (
    UnreadableDocument,
    UnsupportedDocument,
)
from celeris_bridge.domain.knowledge.robots import DEFAULT_USER_AGENT, RobotsRules
from celeris_bridge.domain.knowledge.urls import (
    UnsafeUrl,
    links_from,
    normalise,
    same_site,
    sitemaps_from_robots,
    validate_url,
)
from celeris_bridge.ports.documents import DocumentStore, PassageWriter
from celeris_bridge.ports.knowledge import EmbeddingModel
from celeris_bridge.ports.web import FetchFailed, FetchedPage, WebFetcher

__all__ = [
    "CrawlLimits",
    "CrawlReport",
    "CrawledPage",
    "SkippedPage",
    "crawl_site",
    "robots_url_for",
    "source_id_for",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrawlLimits:
    """How far a crawl may go.

    Defaults chosen to be useful on a marketing site and safe on a surprise:
    40 pages is a company site's public pages, and 3 levels reaches a blog
    post from the front page without walking into an archive of ten years.
    """

    max_pages: int = 40
    max_depth: int = 3
    #: Leaving the site is almost never wanted: one link to a news article and
    #: the crawler is indexing a newspaper as the customer's knowledge.
    same_site_only: bool = True
    #: Seconds between requests. A floor, raised by ``Crawl-delay``. Being
    #: rude to a customer's own server is a support ticket about the product.
    delay_seconds: float = 0.5
    #: Per page. A page larger than this is a download, not a document.
    max_bytes: int = 4_000_000
    follow_robots: bool = True
    user_agent: str = DEFAULT_USER_AGENT


@dataclass(frozen=True)
class CrawledPage:
    """A page that was fetched and what became of it."""

    url: str
    depth: int
    title: str = ""
    source_id: str = ""
    passages: int = 0
    #: True when the page was already indexed with this content and this
    #: embedder, so nothing was embedded again.
    unchanged: bool = False

    @property
    def indexed(self) -> bool:
        return self.passages > 0 or self.unchanged


@dataclass(frozen=True)
class SkippedPage:
    """A page that was not indexed, and why. Prose: this is shown to a person."""

    url: str
    reason: str


@dataclass
class CrawlReport:
    """What a crawl did."""

    start_url: str
    pages: list[CrawledPage] = field(default_factory=list)
    skipped: list[SkippedPage] = field(default_factory=list)
    #: Set when the crawl stopped because it ran out of budget rather than out
    #: of links -- which is the difference between "this is the whole site" and
    #: "there is more, raise the cap".
    stopped_early: str = ""
    robots_present: bool = False

    @property
    def indexed_pages(self) -> int:
        return sum(1 for page in self.pages if page.indexed)

    @property
    def passages(self) -> int:
        return sum(page.passages for page in self.pages)


def source_id_for(url: str) -> str:
    """A stable, filesystem-safe source id for a page.

    From the URL, so re-crawling the same page replaces its passages instead
    of adding a second copy -- and from a *hash* of it, because a URL contains
    slashes, dots and percent-escapes and the document store rightly refuses
    to turn any of those into a directory name.
    """
    return "web-" + checksum(normalise(url).encode("utf-8"))[:24]


async def crawl_site(
    *,
    workspace_id: str,
    start_url: str,
    fetch: WebFetcher,
    documents: DocumentStore,
    embeddings: EmbeddingModel,
    index: PassageWriter,
    limits: CrawlLimits | None = None,
    robots_text: str | None = None,
    agent_instance_ids: tuple[str, ...] = (),
    sleep=asyncio.sleep,
) -> CrawlReport:
    """
    Fetch a site and index what it says.

    ``robots_text`` is passed in rather than fetched here, because fetching it
    is I/O and this module has none: the caller gets it through the same
    fetcher, with the same safety rules applied. ``None`` means it was not
    available, which allows everything -- see
    :mod:`celeris_bridge.domain.knowledge.robots` on why that is the right
    reading for a site the customer owns.

    ``sleep`` is injected so a test does not wait out the politeness delay.
    Everything else about the delay is real.
    """
    limits = limits or CrawlLimits()
    report = CrawlReport(start_url=start_url)

    try:
        first = validate_url(start_url)
    except UnsafeUrl as refused:
        report.skipped.append(SkippedPage(url=start_url, reason=str(refused)))
        report.stopped_early = str(refused)
        return report

    rules = RobotsRules.parse(robots_text, user_agent=limits.user_agent) if robots_text else RobotsRules.absent()
    report.robots_present = rules.present
    delay = max(limits.delay_seconds, rules.crawl_delay)

    # Breadth-first: a list used as a queue, with the depth carried alongside.
    queue: list[tuple[str, int]] = [(first, 0)]
    queued = {first}
    #: Content already indexed in this crawl, so the print version of a page
    #: is not embedded a second time.
    bodies: dict[str, str] = {}
    fetched = 0

    if robots_text:
        for sitemap in sitemaps_from_robots(robots_text):
            # Not fetched -- parsing XML sitemaps is a chunk of its own -- but
            # recorded, because it is the one place a site states which pages
            # matter and it belongs in the report rather than nowhere.
            logger.info("Site declares a sitemap at %s", sitemap)

    while queue:
        if fetched >= limits.max_pages:
            report.stopped_early = (
                f"Stopped at the limit of {limits.max_pages} pages; "
                f"{len(queue)} more were waiting."
            )
            break

        url, depth = queue.pop(0)

        if limits.follow_robots and not rules.allows(url, user_agent=limits.user_agent):
            report.skipped.append(SkippedPage(url=url, reason="robots.txt disallows it"))
            continue

        if fetched and delay:
            await sleep(delay)

        known = await documents.describe(
            workspace_id=workspace_id, source_id=source_id_for(url)
        )
        try:
            page = await fetch.fetch(url, etag=known.etag if known else "")
        except UnsafeUrl as refused:
            report.skipped.append(SkippedPage(url=url, reason=str(refused)))
            continue
        except FetchFailed as failure:
            report.skipped.append(SkippedPage(url=url, reason=str(failure)))
            continue
        fetched += 1

        if not page.ok and not page.unchanged:
            report.skipped.append(SkippedPage(url=url, reason=f"the server answered {page.status}"))
            continue

        # The URL the content came from, which is not always the one asked for.
        final = normalise(page.url or url)
        if final != url and final in bodies:
            report.skipped.append(
                SkippedPage(url=url, reason=f"redirects to {final}, already indexed")
            )
            continue

        if page.unchanged:
            report.pages.append(
                CrawledPage(
                    url=final,
                    depth=depth,
                    source_id=source_id_for(final),
                    passages=known.indexed_passages if known else 0,
                    unchanged=True,
                )
            )
            # Nothing to read, so nothing to follow. The links on a page that
            # has not changed are the links already followed last time.
            continue

        if len(page.content) > limits.max_bytes:
            report.skipped.append(
                SkippedPage(
                    url=final,
                    reason=f"{len(page.content):,} bytes is past the {limits.max_bytes:,} byte limit",
                )
            )
            continue

        digest = checksum(page.content)
        if digest in bodies.values():
            twin = next(other for other, value in bodies.items() if value == digest)
            report.skipped.append(
                SkippedPage(url=final, reason=f"identical to {twin}")
            )
            continue

        # The second, and more useful, unchanged check. A server with no ETag
        # answers 200 with the same bytes as last night, and embedding them
        # again would cost the same as the first time for no new knowledge.
        # The embedder has to match too: the same bytes under a new model are
        # vectors the index will refuse.
        if (
            known is not None
            and known.checksum == digest
            and known.indexed_model == embeddings.identifier
        ):
            bodies[final] = digest
            report.pages.append(
                CrawledPage(
                    url=final,
                    depth=depth,
                    title=known.name,
                    source_id=known.source_id,
                    passages=known.indexed_passages,
                    unchanged=True,
                )
            )
            # Unlike a 304, the content is in hand -- so the links on it are
            # still followed. A page can be unchanged while the pages it links
            # to are new.
            _enqueue(page, final, depth, limits, first, queue, queued)
            continue

        result, skip = await _index_page(
            workspace_id=workspace_id,
            page=page,
            url=final,
            documents=documents,
            embeddings=embeddings,
            index=index,
            agent_instance_ids=agent_instance_ids,
        )
        if skip is not None:
            report.skipped.append(skip)
        if result is not None:
            bodies[final] = digest
            report.pages.append(
                CrawledPage(
                    url=final,
                    depth=depth,
                    title=result.source_name,
                    source_id=result.source_id,
                    passages=result.passages,
                )
            )

        _enqueue(page, final, depth, limits, first, queue, queued)

    if queue and not report.stopped_early:
        report.stopped_early = f"{len(queue)} page(s) were still queued."
    return report


def _enqueue(
    page: FetchedPage,
    url: str,
    depth: int,
    limits: CrawlLimits,
    root: str,
    queue: list[tuple[str, int]],
    queued: set[str],
) -> None:
    """Add this page's links to the back of the queue.

    The back, which is what makes the crawl breadth-first, and in the order
    the page lists them -- the navigation before the footer.
    """
    if depth >= limits.max_depth or not page.is_html:
        return
    for link in links_from(page.content.decode("utf-8", errors="replace"), base=url):
        if link in queued:
            continue
        if limits.same_site_only and not same_site(link, root=root):
            continue
        try:
            validate_url(link)
        except UnsafeUrl:
            # Silent: a site linking to localhost in a code sample is not a
            # problem worth a line in a customer-facing report.
            continue
        queued.add(link)
        queue.append((link, depth + 1))


async def _index_page(
    *,
    workspace_id: str,
    page: FetchedPage,
    url: str,
    documents: DocumentStore,
    embeddings: EmbeddingModel,
    index: PassageWriter,
    agent_instance_ids: tuple[str, ...],
) -> tuple[IngestionResult | None, SkippedPage | None]:
    """One page through the ingestion pipeline.

    ``use_document_title`` is on here and off for an upload, which is the whole
    difference: a crawled page has no file name, and a URL makes a poor
    citation next to the page's own title.
    """
    try:
        result = await ingest(
            workspace_id=workspace_id,
            source_id=source_id_for(url),
            name=_name_for(url),
            content=page.content,
            content_type=page.content_type,
            source_url=url,
            documents=documents,
            embeddings=embeddings,
            index=index,
            agent_instance_ids=agent_instance_ids,
            use_document_title=True,
            etag=page.etag,
        )
    except (UnsupportedDocument, UnreadableDocument) as refused:
        return None, SkippedPage(url=url, reason=str(refused))
    except ValueError as refused:
        # DocumentTooLarge and anything else the pipeline refuses. One bad
        # page must not end the crawl.
        return None, SkippedPage(url=url, reason=str(refused))

    if not result.indexed:
        return result, SkippedPage(url=url, reason=result.skipped or "nothing to index")
    return result, None


def _name_for(url: str) -> str:
    """A readable fallback citation, used only if the page has no title.

    The last path segment, or the host for a front page. Better than the whole
    URL, which in a citation is a line of slugs.
    """
    parts = urlsplit(url)
    tail = [segment for segment in parts.path.split("/") if segment]
    return tail[-1] if tail else (parts.hostname or url)


def robots_url_for(url: str) -> str:
    """Where a site's robots.txt is. Always the root, by the standard."""
    return urljoin(normalise(url), "/robots.txt")
