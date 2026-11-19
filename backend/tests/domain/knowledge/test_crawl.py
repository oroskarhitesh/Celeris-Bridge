"""
Tests for the crawl loop.

A fake site in a dict and a fake fetcher over it, with the real document
store, the real pipeline and the real index behind them. That split is the
point: what is faked is the network, and everything the loop decides — order,
caps, duplicates, what happens when a page fails — is real.

The properties that carry the weight are the ones that cost money or lose
pages: breadth-first order (because a cap always bites), the three kinds of
duplicate, not re-embedding an unchanged page, and one bad page not ending the
crawl.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from celeris_bridge.adapters.in_memory import (
    HashingEmbeddings,
    InMemoryDocumentStore,
    InMemoryPassageIndex,
)
from celeris_bridge.domain.knowledge.crawl import (
    CrawlLimits,
    crawl_site,
    robots_url_for,
    source_id_for,
)
from celeris_bridge.domain.knowledge.urls import UnsafeUrl
from celeris_bridge.ports.knowledge import KnowledgeScope
from celeris_bridge.ports.web import FetchFailed, FetchedPage, WebFetcher

ROOT = "https://acme.example/"

BODY = (
    "The Starter plan is 29 per seat per month, billed annually. "
    "The Growth plan is 49 per seat and adds single sign-on. "
    "Every plan includes a 14 day trial and no card is required."
)


def page(*links: str, body: str = BODY, title: str = "") -> bytes:
    head = f"<title>{title}</title>" if title else ""
    anchors = "".join(f"<a href='{link}'>{link}</a>" for link in links)
    return f"<html><head>{head}</head><body>{anchors}<p>{body}</p></body></html>".encode()


SITE: dict[str, bytes] = {
    "https://acme.example/": page("/pricing", "/about", title="Acme"),
    "https://acme.example/pricing": page("/pricing/enterprise", title="Pricing"),
    "https://acme.example/about": page("/team", title="About"),
    "https://acme.example/pricing/enterprise": page(title="Enterprise"),
    "https://acme.example/team": page(title="Team"),
}


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class FakeFetcher:
    """A site in a dict.

    It applies the same URL guard the real adapter does, so a test that
    crawls into something forbidden fails here for the same reason it would in
    production rather than quietly succeeding.
    """

    def __init__(
        self,
        site: dict[str, bytes] | None = None,
        *,
        redirects: dict[str, str] | None = None,
        failures: dict[str, Exception] | None = None,
        statuses: dict[str, int] | None = None,
        etags: dict[str, str] | None = None,
        unchanged: set[str] | None = None,
        content_type: str = "text/html; charset=utf-8",
    ) -> None:
        self.site = dict(site if site is not None else SITE)
        self.redirects = redirects or {}
        self.failures = failures or {}
        self.statuses = statuses or {}
        self.etags = etags or {}
        self.unchanged = unchanged or set()
        self.content_type = content_type
        self.asked: list[str] = []
        self.conditional: list[tuple[str, str]] = []

    async def fetch(self, url: str, *, etag: str = "") -> FetchedPage:
        from celeris_bridge.domain.knowledge.urls import validate_url

        validate_url(url)
        self.asked.append(url)
        if etag:
            self.conditional.append((url, etag))

        if url in self.failures:
            raise self.failures[url]

        final = self.redirects.get(url, url)
        if url in self.unchanged and etag:
            return FetchedPage(
                url=final, status=304, content=b"", requested_url=url, unchanged=True
            )
        if url in self.statuses:
            return FetchedPage(
                url=final,
                status=self.statuses[url],
                content=b"",
                content_type="text/html",
                requested_url=url,
            )
        content = self.site.get(final)
        if content is None:
            return FetchedPage(url=final, status=404, content=b"", requested_url=url)
        return FetchedPage(
            url=final,
            status=200,
            content=content,
            content_type=self.content_type,
            etag=self.etags.get(final, ""),
            requested_url=url,
        )


def world(**changed: Any) -> dict[str, Any]:
    return {
        "documents": InMemoryDocumentStore(),
        "index": InMemoryPassageIndex(),
        "embeddings": Embedder(),
        **changed,
    }


class Embedder:
    def __init__(self, *, identifier: str = "test-embed:8") -> None:
        self._inner = HashingEmbeddings(dimensions=8)
        self._identifier = identifier
        self.batches: list[Any] = []

    @property
    def identifier(self) -> str:
        return self._identifier

    @property
    def dimensions(self) -> int:
        return 8

    async def embed_query(self, text: str):
        return self._inner.embed(text)

    async def embed_documents(self, texts):
        self.batches.append(list(texts))
        return [self._inner.embed(text) for text in texts]


def crawl(
    parts: dict[str, Any],
    *,
    fetcher: FakeFetcher | None = None,
    start: str = ROOT,
    robots: str | None = None,
    **limits: Any,
):
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    report = run(
        crawl_site(
            workspace_id="w1",
            start_url=start,
            fetch=fetcher or FakeFetcher(),
            documents=parts["documents"],
            embeddings=parts["embeddings"],
            index=parts["index"],
            limits=CrawlLimits(**limits) if limits else CrawlLimits(delay_seconds=0.1),
            robots_text=robots,
            sleep=sleep,
        )
    )
    report.slept = slept  # type: ignore[attr-defined]
    return report


def hits(parts: dict[str, Any], query: str = "per seat"):
    embedder = parts["embeddings"]
    return run(
        parts["index"].search(
            scope=KnowledgeScope(workspace_id="w1", agent_instance_id="a1"),
            query_vector=run(embedder.embed_query(query)),
            limit=50,
            min_score=0.0,
            model=embedder.identifier,
        )
    )


class TestAWholeSmallSite:
    def test_every_page_is_found_and_indexed(self) -> None:
        parts = world()
        report = crawl(parts)
        assert sorted(one.url for one in report.pages) == sorted(SITE)
        assert report.indexed_pages == 5

    def test_the_passages_are_searchable(self) -> None:
        parts = world()
        crawl(parts)
        assert len(hits(parts)) == crawl_passages(parts)

    def test_a_page_is_cited_by_its_title_and_not_its_url(self) -> None:
        # A URL in a citation is a line of slugs. The page states its own name
        # and the crawler is the one caller that should take it.
        parts = world()
        crawl(parts)
        assert "Pricing" in {hit.source_name for hit in hits(parts)}

    def test_a_page_with_no_title_falls_back_to_its_path(self) -> None:
        parts = world()
        crawl(
            parts,
            fetcher=FakeFetcher({ROOT: page("/pricing"), f"{ROOT}pricing": page()}),
        )
        assert "pricing" in {hit.source_name for hit in hits(parts)}

    def test_the_url_is_kept_as_the_citation_link(self) -> None:
        parts = world()
        crawl(parts)
        assert all(hit.source_url for hit in hits(parts))

    def test_the_original_html_is_stored(self) -> None:
        # So a change of embedding model is recoverable without crawling the
        # site again -- which is both slow and rude.
        parts = world()
        crawl(parts)
        stored = run(parts["documents"].content(workspace_id="w1", source_id=source_id_for(ROOT)))
        assert b"<html>" in stored


def crawl_passages(parts: dict[str, Any]) -> int:
    return sum(
        one.indexed_passages for one in run(parts["documents"].list(workspace_id="w1"))
    )


class TestBreadthFirst:
    def test_a_level_is_finished_before_the_next_one_starts(self) -> None:
        # A crawl is always cut short by a cap, so the question is which pages
        # are kept. Depth order would keep one deep branch and miss the
        # navigation.
        parts = world()
        report = crawl(parts)
        depths = [one.depth for one in report.pages]
        assert depths == sorted(depths)

    def test_the_front_page_is_first(self) -> None:
        parts = world()
        report = crawl(parts)
        assert report.pages[0].url == ROOT
        assert report.pages[0].depth == 0

    def test_the_page_cap_keeps_the_shallow_pages(self) -> None:
        parts = world()
        report = crawl(parts, max_pages=3, max_depth=5, delay_seconds=0)
        assert [one.depth for one in report.pages] == [0, 1, 1]
        assert "limit of 3 pages" in report.stopped_early

    def test_the_depth_cap_stops_descending(self) -> None:
        parts = world()
        report = crawl(parts, max_depth=1, max_pages=50, delay_seconds=0)
        assert sorted(one.url for one in report.pages) == [
            "https://acme.example/",
            "https://acme.example/about",
            "https://acme.example/pricing",
        ]
        assert report.stopped_early == ""

    def test_a_finished_crawl_does_not_claim_to_have_stopped_early(self) -> None:
        parts = world()
        assert crawl(parts).stopped_early == ""


class TestTheThreeKindsOfDuplicate:
    def test_the_same_url_written_differently_is_fetched_once(self) -> None:
        parts = world()
        fetcher = FakeFetcher(
            {
                ROOT: page("/pricing", "/pricing/", "/pricing?utm_source=x", "/pricing#top"),
                f"{ROOT}pricing": page(title="Pricing"),
                f"{ROOT}pricing/": page(title="Pricing with a slash"),
            }
        )
        crawl(parts, fetcher=fetcher)
        # The campaign tag and the fragment collapse onto /pricing; the
        # trailing slash is a different URL to a server, and the content
        # checksum below is what catches it when it is not.
        assert fetcher.asked.count(f"{ROOT}pricing") == 1

    def test_two_urls_redirecting_to_one_page_are_one_page(self) -> None:
        parts = world()
        fetcher = FakeFetcher(
            {ROOT: page("/pricing", "/plans"), f"{ROOT}pricing": page(title="Pricing")},
            redirects={f"{ROOT}plans": f"{ROOT}pricing"},
        )
        report = crawl(parts, fetcher=fetcher)
        assert [one.url for one in report.pages].count(f"{ROOT}pricing") == 1
        assert any("already indexed" in one.reason for one in report.skipped)

    def test_the_same_content_at_two_urls_is_indexed_once(self) -> None:
        # The print version, the trailing-slash twin, the ?lang=en default.
        parts = world()
        same = page(title="Pricing")
        report = crawl(
            parts,
            fetcher=FakeFetcher(
                {
                    ROOT: page("/pricing", "/pricing/print"),
                    f"{ROOT}pricing": same,
                    f"{ROOT}pricing/print": same,
                }
            ),
        )
        assert report.indexed_pages == 2
        assert any("identical to" in one.reason for one in report.skipped)

    def test_a_link_back_to_the_front_page_does_not_loop(self) -> None:
        parts = world()
        fetcher = FakeFetcher({ROOT: page("/pricing"), f"{ROOT}pricing": page(ROOT, "/")})
        report = crawl(parts, fetcher=fetcher)
        assert len(fetcher.asked) == 2
        assert report.indexed_pages == 2


class TestNotDoingTheWorkTwice:
    def test_a_page_that_has_not_changed_is_not_embedded_again(self) -> None:
        # The whole economics of a nightly crawl: nearly every page is
        # unchanged, and embedding is what costs.
        parts = world()
        crawl(parts)
        first = len(parts["embeddings"].batches)

        parts["embeddings"].batches.clear()
        again = crawl(parts)
        assert parts["embeddings"].batches == []
        assert again.indexed_pages == 5
        assert all(one.unchanged for one in again.pages)
        assert first > 0

    def test_an_unchanged_page_still_reports_its_passages(self) -> None:
        # So the report does not read as though the knowledge disappeared.
        parts = world()
        first = crawl(parts)
        again = crawl(parts)
        assert again.passages == first.passages

    def test_a_changed_page_is_embedded_again(self) -> None:
        parts = world()
        crawl(parts)
        parts["embeddings"].batches.clear()
        changed = dict(SITE)
        changed[f"{ROOT}pricing"] = page(title="Pricing", body="Pricing has moved to 39 per seat.")
        again = crawl(parts, fetcher=FakeFetcher(changed))
        assert len(parts["embeddings"].batches) == 1
        assert [one.unchanged for one in again.pages].count(False) == 1

    def test_a_new_embedder_re_embeds_everything(self) -> None:
        # The same bytes under a new model are vectors the index refuses, so
        # "unchanged" has to mean the content *and* the embedder.
        parts = world()
        crawl(parts)
        parts["embeddings"] = Embedder(identifier="other-embed:8")
        again = crawl(parts)
        assert len(parts["embeddings"].batches) == 5
        assert not any(one.unchanged for one in again.pages)

    def test_a_stored_etag_is_offered_back(self) -> None:
        # Saves the download, where the checksum only saves the embedding.
        parts = world()
        crawl(parts, fetcher=FakeFetcher(etags={ROOT: 'W/"abc"'}))
        fetcher = FakeFetcher(etags={ROOT: 'W/"abc"'})
        crawl(parts, fetcher=fetcher)
        assert (ROOT, 'W/"abc"') in fetcher.conditional

    def test_a_304_is_counted_as_indexed_and_not_as_a_failure(self) -> None:
        parts = world()
        crawl(parts, fetcher=FakeFetcher(etags={ROOT: 'W/"abc"'}))
        report = crawl(
            parts,
            fetcher=FakeFetcher(etags={ROOT: 'W/"abc"'}, unchanged={ROOT}),
        )
        front = next(one for one in report.pages if one.url == ROOT)
        assert front.unchanged is True
        assert not any(one.url == ROOT for one in report.skipped)


class TestStayingPut:
    def test_another_site_is_not_followed(self) -> None:
        # One link to a news article and the crawler indexes a newspaper as
        # the customer's knowledge.
        parts = world()
        fetcher = FakeFetcher(
            {ROOT: page("https://news.example/story", "/pricing"), f"{ROOT}pricing": page()}
        )
        crawl(parts, fetcher=fetcher)
        assert not any("news.example" in one for one in fetcher.asked)

    def test_a_subdomain_is_followed(self) -> None:
        parts = world()
        fetcher = FakeFetcher(
            {ROOT: page("https://blog.acme.example/post"), "https://blog.acme.example/post": page()}
        )
        crawl(parts, fetcher=fetcher)
        assert "https://blog.acme.example/post" in fetcher.asked

    def test_leaving_the_site_can_be_allowed(self) -> None:
        parts = world()
        fetcher = FakeFetcher(
            {ROOT: page("https://partner.example/x"), "https://partner.example/x": page()}
        )
        crawl(parts, fetcher=fetcher, same_site_only=False, max_depth=2, delay_seconds=0)
        assert "https://partner.example/x" in fetcher.asked

    def test_a_link_to_something_internal_is_dropped_without_a_word(self) -> None:
        # A site linking to localhost in a code sample is not a problem worth
        # a line in a customer-facing report.
        parts = world()
        fetcher = FakeFetcher(
            {ROOT: page("http://169.254.169.254/latest", "http://localhost:8080/", "/pricing"),
             f"{ROOT}pricing": page()}
        )
        report = crawl(parts, fetcher=fetcher)
        assert not any("169.254" in one for one in fetcher.asked)
        assert report.skipped == []

    def test_a_start_url_that_is_not_allowed_stops_before_anything(self) -> None:
        parts = world()
        fetcher = FakeFetcher()
        report = crawl(parts, fetcher=fetcher, start="http://169.254.169.254/")
        assert fetcher.asked == []
        assert report.stopped_early
        assert report.pages == []


class TestRobots:
    SITE_ROBOTS = "User-agent: *\nDisallow: /about\nCrawl-delay: 3\n"

    def test_a_disallowed_page_is_not_fetched(self) -> None:
        parts = world()
        fetcher = FakeFetcher()
        report = crawl(parts, fetcher=fetcher, robots=self.SITE_ROBOTS)
        assert f"{ROOT}about" not in fetcher.asked
        assert any("robots" in one.reason for one in report.skipped)

    def test_the_rest_of_the_site_is_still_crawled(self) -> None:
        parts = world()
        report = crawl(parts, robots=self.SITE_ROBOTS)
        assert f"{ROOT}pricing" in {one.url for one in report.pages}

    def test_a_crawl_delay_raises_the_wait_but_never_lowers_it(self) -> None:
        parts = world()
        report = crawl(parts, robots=self.SITE_ROBOTS)
        assert set(report.slept) == {3.0}

        faster = crawl(world(), robots="User-agent: *\nCrawl-delay: 0.01\n")
        assert set(faster.slept) == {0.1}  # the configured floor wins

    def test_no_robots_file_is_recorded_as_such(self) -> None:
        parts = world()
        assert crawl(parts).robots_present is False

    def test_a_robots_file_is_recorded_as_such(self) -> None:
        parts = world()
        assert crawl(parts, robots="User-agent: *\nAllow: /\n").robots_present is True

    def test_robots_can_be_ignored_on_purpose(self) -> None:
        # For a site the customer owns and has told us to crawl anyway.
        parts = world()
        fetcher = FakeFetcher()
        run(
            crawl_site(
                workspace_id="w1",
                start_url=ROOT,
                fetch=fetcher,
                documents=parts["documents"],
                embeddings=parts["embeddings"],
                index=parts["index"],
                limits=CrawlLimits(follow_robots=False, delay_seconds=0),
                robots_text=self.SITE_ROBOTS,
                sleep=_no_sleep,
            )
        )
        assert f"{ROOT}about" in fetcher.asked

    def test_the_robots_url_is_always_at_the_root(self) -> None:
        assert robots_url_for("https://acme.example/pricing/plans?x=1") == (
            "https://acme.example/robots.txt"
        )


async def _no_sleep(seconds: float) -> None:
    return None


class TestPolitenessAndCaps:
    def test_there_is_a_wait_between_requests(self) -> None:
        # Being rude to a customer's own server is a support ticket about
        # this product.
        parts = world()
        report = crawl(parts)
        assert len(report.slept) == 4  # five pages, four gaps

    def test_the_first_request_does_not_wait(self) -> None:
        parts = world()
        fetcher = FakeFetcher({ROOT: page()})
        assert crawl(parts, fetcher=fetcher).slept == []

    def test_a_page_past_the_size_limit_is_skipped_not_indexed(self) -> None:
        parts = world()
        report = crawl(
            parts,
            fetcher=FakeFetcher({ROOT: page(body="x " * 5_000)}),
            max_bytes=1_000,
            delay_seconds=0,
        )
        assert report.indexed_pages == 0
        assert "byte limit" in report.skipped[0].reason


class TestWhenAPageFails:
    def test_a_404_in_the_middle_does_not_stop_the_crawl(self) -> None:
        parts = world()
        report = crawl(parts, fetcher=FakeFetcher(statuses={f"{ROOT}pricing": 404}))
        assert any("404" in one.reason for one in report.skipped)
        assert f"{ROOT}about" in {one.url for one in report.pages}

    def test_a_500_costs_the_pages_only_that_page_linked_to(self) -> None:
        # /team is reachable only through /about, so a crawl is as connected
        # as the site is: one broken page loses its subtree and nothing else.
        parts = world()
        report = crawl(parts, fetcher=FakeFetcher(statuses={f"{ROOT}about": 500}))
        assert sorted(one.url for one in report.pages) == [
            "https://acme.example/",
            "https://acme.example/pricing",
            "https://acme.example/pricing/enterprise",
        ]

    def test_a_dead_host_is_recorded_and_the_crawl_goes_on(self) -> None:
        parts = world()
        report = crawl(
            parts,
            fetcher=FakeFetcher(failures={f"{ROOT}pricing": FetchFailed("timed out")}),
        )
        assert any("timed out" in one.reason for one in report.skipped)
        assert report.indexed_pages >= 2

    def test_a_page_refused_by_the_guard_mid_crawl_is_recorded(self) -> None:
        # The fetcher is the last line: a redirect to something internal is
        # refused there, not here.
        parts = world()
        report = crawl(
            parts,
            fetcher=FakeFetcher(failures={f"{ROOT}pricing": UnsafeUrl("redirects to 10.0.0.5")}),
        )
        assert any("10.0.0.5" in one.reason for one in report.skipped)
        assert report.indexed_pages >= 2

    def test_a_page_with_no_readable_text_is_reported(self) -> None:
        parts = world()
        report = crawl(
            parts,
            fetcher=FakeFetcher({ROOT: b"<html><head><script>x()</script></head></html>"}),
        )
        assert report.indexed_pages == 0
        assert report.skipped[0].reason

    def test_a_site_that_is_entirely_broken_is_a_report_and_not_a_crash(self) -> None:
        parts = world()
        report = crawl(parts, fetcher=FakeFetcher({}, statuses={ROOT: 503}))
        assert report.pages == []
        assert len(report.skipped) == 1


class TestSourceIds:
    def test_the_same_page_gets_the_same_id(self) -> None:
        # So a re-crawl replaces its passages instead of adding a second copy.
        assert source_id_for("https://acme.example/p") == source_id_for(
            "https://acme.example/p?utm_source=x#top"
        )

    def test_different_pages_get_different_ids(self) -> None:
        assert source_id_for("https://acme.example/a") != source_id_for(
            "https://acme.example/b"
        )

    def test_an_id_is_safe_to_use_as_a_directory_name(self) -> None:
        # A URL has slashes, dots and percent-escapes in it, and the document
        # store rightly refuses to turn any of those into a path.
        import re

        for url in ("https://acme.example/a/b/c?d=e#f", "http://x.example/%2e%2e/"):
            assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source_id_for(url))


class TestThePortIsSatisfied:
    def test_the_fake_fetcher_is_a_web_fetcher(self) -> None:
        assert isinstance(FakeFetcher(), WebFetcher)
