"""
The port for fetching a page.

One method, because that is all the crawler needs, and behind it the only
adapter in this project that makes a request to an address a *customer* chose.
Everything that makes that safe -- the scheme and port rules, the deny-list of
address ranges, the size cap, the redirect handling -- is the adapter's job and
is stated in its docstring. The port exists so the crawl loop can be tested
without a network, which is also the only way to test the interesting cases: a
redirect loop, a page that never ends, a site that times out on page nine.

Note that a fetch returns a *result*, not raw bytes. A 404 in the middle of a
crawl is ordinary and must not stop it, so the status comes back as a value to
be recorded; only a refusal by policy and a failure to reach the host at all
are exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["FetchFailed", "FetchedPage", "WebFetcher"]


class FetchFailed(RuntimeError):
    """The page could not be fetched: no route, a timeout, a dead host."""


@dataclass(frozen=True)
class FetchedPage:
    """What came back."""

    #: Where the content actually came from, after redirects. The crawl loop
    #: records *this* as the page's URL: two URLs redirecting to one page are
    #: one page, and treating them as two embeds it twice and cites it twice.
    url: str
    status: int
    content: bytes
    content_type: str = ""
    #: ``ETag``, for asking next time whether anything changed.
    etag: str = ""
    #: The URL that was requested, when a redirect moved it. Kept because a
    #: site redirecting every page to its front page is a common and
    #: otherwise baffling way for a crawl to find one page.
    requested_url: str = ""
    #: True when the server answered "nothing has changed" to a conditional
    #: request. ``content`` is empty in that case and means nothing.
    unchanged: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type.lower()


@runtime_checkable
class WebFetcher(Protocol):
    """Fetches one page."""

    async def fetch(self, url: str, *, etag: str = "") -> FetchedPage:
        """
        Get a page.

        An implementation **must** refuse a URL that is not a public http(s)
        address, raising
        :class:`~celeris_bridge.domain.knowledge.urls.UnsafeUrl` -- and must
        apply that check to every address the name resolves to and to every
        redirect hop, not only to the string it was given.

        ``etag`` is sent as ``If-None-Match``. A server answering 304 saves
        the download *and* the re-embedding of a page that has not changed,
        which over a nightly crawl of a large site is most of the cost.
        """
        ...
