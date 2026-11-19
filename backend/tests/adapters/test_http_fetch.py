"""
Tests for the web fetcher.

No network: httpx takes a ``MockTransport`` and DNS is a function that is
handed in, so the request the adapter builds, the redirect chain it walks and
the guard it applies are all real and only the wire is replaced.

The guard gets the most attention here, and the cases are chosen for how they
are actually bypassed. A URL that looks public is not enough: the hostname has
to resolve to a public address, *every* address it resolves to has to be
public, and the same has to hold at every redirect hop. Each of those three is
a complete bypass on its own.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import httpx
import pytest

from celeris_bridge.adapters.http_fetch import MAX_REDIRECTS, HttpFetcher
from celeris_bridge.domain.knowledge.urls import UnsafeUrl
from celeris_bridge.ports.web import FetchFailed, WebFetcher

PAGE = b"<html><head><title>Pricing</title></head><body><p>29 per seat.</p></body></html>"


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class Site:
    """A scripted server, and a record of what was asked of it."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler or self._default

    def _default(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=PAGE, headers={"content-type": "text/html", "etag": 'W/"v1"'}
        )

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self._handler(request)

        return httpx.MockTransport(handle)


def fetcher(
    site: Site | None = None,
    *,
    addresses: dict[str, list[str]] | None = None,
    **changed: Any,
) -> HttpFetcher:
    site = site or Site()

    async def resolve(host: str) -> list[str]:
        if addresses is None:
            return ["93.184.216.34"]
        if host not in addresses:
            raise OSError(f"no such host: {host}")
        return addresses[host]

    return HttpFetcher(
        client=httpx.AsyncClient(transport=site.transport(), follow_redirects=False),
        resolve=resolve,
        **changed,
    )


class TestFetchingAPage:
    def test_the_port_is_satisfied(self) -> None:
        assert isinstance(fetcher(), WebFetcher)

    def test_a_page_comes_back(self) -> None:
        page = run(fetcher().fetch("https://acme.example/pricing"))
        assert page.status == 200
        assert page.content == PAGE
        assert page.is_html is True
        assert page.ok is True

    def test_the_url_is_normalised_on_the_way_out(self) -> None:
        page = run(fetcher().fetch("HTTPS://Acme.Example/pricing#plans"))
        assert page.url == "https://acme.example/pricing"

    def test_the_etag_is_carried_back(self) -> None:
        # So the next crawl can ask whether anything has changed.
        assert run(fetcher().fetch("https://acme.example/p")).etag == 'W/"v1"'

    def test_the_crawler_says_who_it_is(self) -> None:
        # A request with no user agent is indistinguishable from an attack
        # and gets blocked as one.
        site = Site()
        run(fetcher(site, user_agent="TestBot/9 (+https://x.example)").fetch("https://acme.example/p"))
        assert site.requests[0].headers["user-agent"] == "TestBot/9 (+https://x.example)"

    def test_an_etag_is_sent_as_a_conditional_request(self) -> None:
        site = Site()
        run(fetcher(site).fetch("https://acme.example/p", etag='W/"v1"'))
        assert site.requests[0].headers["if-none-match"] == 'W/"v1"'

    def test_a_304_is_a_result_and_not_a_failure(self) -> None:
        site = Site(lambda request: httpx.Response(304))
        page = run(fetcher(site).fetch("https://acme.example/p", etag='W/"v1"'))
        assert page.unchanged is True
        assert page.content == b""

    def test_a_404_comes_back_as_a_404(self) -> None:
        # A missing page in the middle of a crawl is ordinary and must not
        # stop it, so the status is a value and not an exception.
        site = Site(lambda request: httpx.Response(404, content=b"gone"))
        page = run(fetcher(site).fetch("https://acme.example/p"))
        assert page.status == 404
        assert page.ok is False

    def test_a_pdf_is_fetched_like_anything_else(self) -> None:
        site = Site(
            lambda request: httpx.Response(
                200, content=b"%PDF-1.4 ...", headers={"content-type": "application/pdf"}
            )
        )
        page = run(fetcher(site).fetch("https://acme.example/report.pdf"))
        assert page.content.startswith(b"%PDF-")
        assert page.is_html is False


class TestTheGuard:
    def test_a_forbidden_url_never_reaches_the_client(self) -> None:
        site = Site()
        with pytest.raises(UnsafeUrl):
            run(fetcher(site).fetch("http://169.254.169.254/latest/meta-data/"))
        assert site.requests == []

    def test_a_public_name_pointing_at_a_private_address_is_refused(self) -> None:
        # The bypass the URL check alone cannot see: nothing about
        # "evil.example" says it resolves to 10.0.0.5.
        site = Site()
        guarded = fetcher(site, addresses={"evil.example": ["10.0.0.5"]})
        with pytest.raises(UnsafeUrl) as caught:
            run(guarded.fetch("https://evil.example/"))
        assert "10.0.0.5" in str(caught.value)
        assert site.requests == []

    def test_the_metadata_service_behind_a_name_is_refused(self) -> None:
        guarded = fetcher(addresses={"ssrf.example": ["169.254.169.254"]})
        with pytest.raises(UnsafeUrl):
            run(guarded.fetch("https://ssrf.example/"))

    def test_every_address_is_checked_and_not_only_the_first(self) -> None:
        # A name with one public and one private record is a deliberate
        # attempt at exactly this, and which one a connection gets is up to
        # the resolver.
        guarded = fetcher(addresses={"mixed.example": ["93.184.216.34", "127.0.0.1"]})
        with pytest.raises(UnsafeUrl):
            run(guarded.fetch("https://mixed.example/"))

    def test_an_ipv6_private_record_is_caught_too(self) -> None:
        guarded = fetcher(addresses={"six.example": ["fd00::1"]})
        with pytest.raises(UnsafeUrl):
            run(guarded.fetch("https://six.example/"))

    def test_a_public_name_is_allowed(self) -> None:
        # A guard that refuses real sites is a guard someone turns off.
        guarded = fetcher(addresses={"acme.example": ["93.184.216.34", "2606:2800:220:1::"]})
        assert run(guarded.fetch("https://acme.example/p")).status == 200

    def test_a_name_that_does_not_resolve_is_a_failure_not_a_refusal(self) -> None:
        # Different things: one is a typo, the other is an attack.
        guarded = fetcher(addresses={"acme.example": ["93.184.216.34"]})
        with pytest.raises(FetchFailed):
            run(guarded.fetch("https://nowhere.example/"))

    def test_a_name_resolving_to_nothing_is_a_failure(self) -> None:
        guarded = fetcher(addresses={"empty.example": []})
        with pytest.raises(FetchFailed):
            run(guarded.fetch("https://empty.example/"))


class TestRedirects:
    def redirecting(self, location: str, *, status: int = 302) -> Site:
        def handle(request: httpx.Request) -> httpx.Response:
            if str(request.url).endswith("/start"):
                return httpx.Response(status, headers={"location": location})
            return httpx.Response(
                200, content=PAGE, headers={"content-type": "text/html"}
            )

        return Site(handle)

    def test_a_redirect_is_followed(self) -> None:
        site = self.redirecting("/pricing")
        page = run(fetcher(site).fetch("https://acme.example/start"))
        assert page.url == "https://acme.example/pricing"
        assert page.status == 200

    def test_the_url_recorded_is_where_the_content_came_from(self) -> None:
        # Two URLs redirecting to one page are one page; recording the
        # requested one would embed and cite it twice.
        site = self.redirecting("/pricing")
        page = run(fetcher(site).fetch("https://acme.example/start"))
        assert page.url == "https://acme.example/pricing"
        assert page.requested_url == "https://acme.example/start"

    def test_a_redirect_to_a_forbidden_address_is_refused(self) -> None:
        # The reason redirects are followed by hand. Letting the client follow
        # them means a public URL reaching the metadata service with every
        # check already passed.
        site = self.redirecting("http://169.254.169.254/latest/meta-data/")
        with pytest.raises(UnsafeUrl):
            run(fetcher(site).fetch("https://acme.example/start"))

    def test_a_redirect_to_a_name_that_resolves_privately_is_refused(self) -> None:
        site = self.redirecting("https://internal.example/admin")
        guarded = fetcher(
            site,
            addresses={"acme.example": ["93.184.216.34"], "internal.example": ["10.1.1.1"]},
        )
        with pytest.raises(UnsafeUrl):
            run(guarded.fetch("https://acme.example/start"))

    def test_a_redirect_to_another_scheme_is_refused(self) -> None:
        site = self.redirecting("file:///etc/passwd")
        with pytest.raises(UnsafeUrl):
            run(fetcher(site).fetch("https://acme.example/start"))

    def test_a_redirect_loop_ends(self) -> None:
        site = Site(lambda request: httpx.Response(302, headers={"location": "/start"}))
        with pytest.raises(FetchFailed) as caught:
            run(fetcher(site).fetch("https://acme.example/start"))
        assert "redirects" in str(caught.value)
        assert len(site.requests) <= MAX_REDIRECTS + 1

    def test_a_redirect_with_no_location_is_a_failure(self) -> None:
        site = Site(lambda request: httpx.Response(302))
        with pytest.raises(FetchFailed):
            run(fetcher(site).fetch("https://acme.example/start"))

    def test_the_conditional_header_is_not_resent_after_a_redirect(self) -> None:
        # It was an etag for the old URL. Offering it for a different page
        # invites a 304 for content never seen.
        site = self.redirecting("/pricing")
        run(fetcher(site).fetch("https://acme.example/start", etag='W/"v1"'))
        assert "if-none-match" not in site.requests[-1].headers


class TestSize:
    def big(self, size: int) -> Site:
        return Site(
            lambda request: httpx.Response(
                200, content=b"x" * size, headers={"content-type": "text/html"}
            )
        )

    def test_a_page_past_the_cap_is_refused(self) -> None:
        with pytest.raises(FetchFailed) as caught:
            run(fetcher(self.big(50_000), max_bytes=10_000).fetch("https://acme.example/p"))
        assert "limit" in str(caught.value)

    def test_a_page_inside_the_cap_is_fine(self) -> None:
        page = run(fetcher(self.big(5_000), max_bytes=10_000).fetch("https://acme.example/p"))
        assert len(page.content) == 5_000

    def test_a_declared_length_past_the_cap_is_refused_before_reading(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"x" * 100,
                headers={"content-type": "text/html", "content-length": "99999999"},
            )

        with pytest.raises(FetchFailed) as caught:
            run(fetcher(Site(handle), max_bytes=1_000).fetch("https://acme.example/p"))
        assert "99,999,999" in str(caught.value)

    def test_a_lying_content_length_does_not_get_past_the_real_count(self) -> None:
        # Content-Length is a claim. The bytes are what is enforced.
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"x" * 50_000,
                headers={"content-type": "text/html", "content-length": "10"},
            )

        with pytest.raises(FetchFailed):
            run(fetcher(Site(handle), max_bytes=1_000).fetch("https://acme.example/p"))


class TestWhenTheHostMisbehaves:
    def test_a_timeout_is_a_fetch_failure(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("too slow")

        with pytest.raises(FetchFailed) as caught:
            run(fetcher(Site(handle)).fetch("https://acme.example/p"))
        assert "acme.example" in str(caught.value)

    def test_a_refused_connection_is_a_fetch_failure(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(FetchFailed):
            run(fetcher(Site(handle)).fetch("https://acme.example/p"))
