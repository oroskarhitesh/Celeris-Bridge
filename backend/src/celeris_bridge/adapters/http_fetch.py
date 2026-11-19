"""
The web fetcher: the one place this project sends a request to an address a
customer chose.

That is worth stating plainly, because it is the whole threat model. A
workspace types a URL into a form and the product fetches it, from inside the
network the product runs in. An attacker who can type a URL gets an HTTP
client with the server's network position, and the first thing they ask for is
``http://169.254.169.254/latest/meta-data/iam/security-credentials/`` -- the
cloud metadata service, which answers without authentication to anything
inside the instance. Next is whatever else is reachable: this project's own
Mongo, an internal dashboard, a neighbour's admin panel.

Four things stand in the way, and all four are needed:

1. **The URL is checked** -- scheme, port, credentials, hostname, and literal
   addresses in every notation (:func:`validate_url`).
2. **The name is resolved here, and every address it returns is checked**
   against the deny-list. A hostname is not an address: ``evil.example`` can
   have an A record of ``10.0.0.5``, and nothing about the string says so.
3. **Redirects are followed by hand**, one hop at a time, with steps 1 and 2
   applied to each. Letting the HTTP client follow them would mean a public
   URL redirecting to the metadata service, with every check already passed.
4. **The response is read with a cap** on bytes, so a URL that streams
   forever is a refusal rather than a server that runs out of memory.

The honest limit: between the check in step 2 and the connection, the name can
be re-resolved to a different address -- DNS rebinding. Closing that needs the
connection pinned to the address that was approved, which means owning the
socket. What is here narrows the window to one request and refuses every
address that comes back; a reverse proxy with an egress allow-list is the
complete answer and belongs in the deployment, not in this file.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from celeris_bridge.domain.knowledge.robots import DEFAULT_USER_AGENT
from celeris_bridge.domain.knowledge.urls import (
    UnsafeUrl,
    is_blocked_address,
    normalise,
    validate_url,
)
from celeris_bridge.ports.web import FetchFailed, FetchedPage

__all__ = ["MAX_REDIRECTS", "HttpFetcher"]

logger = logging.getLogger(__name__)

#: Enough for the usual http->https and no-www->www pair, with room to spare.
#: A site needing more than this has a redirect loop.
MAX_REDIRECTS = 5

#: Read in pieces so the cap is enforced while downloading, not after. A cap
#: checked at the end has already held the whole thing in memory.
_CHUNK = 64 * 1024


class HttpFetcher:
    """Fetches a page over HTTP, with the guard above in front of it."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 15.0,
        max_bytes: int = 4_000_000,
        resolve=None,
    ) -> None:
        self._user_agent = user_agent
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._client = client
        self._resolve = resolve or _resolve

    def _ready(self) -> httpx.AsyncClient:
        """The client, built the first time it is needed.

        Lazily, for a measured reason: constructing one costs the better part
        of a second because it loads the operating system certificate store.
        A process that never crawls should not pay that at start-up, and a
        test suite should not pay it once per test.

        Redirects are never followed by the client: see the module docstring.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=False
            )
        return self._client

    async def fetch(self, url: str, *, etag: str = "") -> FetchedPage:
        requested = validate_url(url)
        target = requested
        for hop in range(MAX_REDIRECTS + 1):
            await self._check_addresses(target)
            response, body = await self._get(target, etag=etag if hop == 0 else "")

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location", "")
                if not location:
                    raise FetchFailed(
                        f"{target} answered {response.status_code} with no Location"
                    )
                # Checked on the way round the loop, which is the point of
                # following redirects by hand.
                target = validate_url(urljoin(target, location))
                continue

            content = b"" if response.status_code == 304 else body
            return FetchedPage(
                url=normalise(str(response.url)),
                status=response.status_code,
                content=content,
                content_type=response.headers.get("content-type", ""),
                etag=response.headers.get("etag", ""),
                requested_url=requested,
                unchanged=response.status_code == 304,
            )

        raise FetchFailed(f"More than {MAX_REDIRECTS} redirects starting at {requested}")

    async def _check_addresses(self, url: str) -> None:
        """Resolve the host and refuse if any address is not public.

        *Any*, not the first: a name with one public and one private A record
        is a deliberate attempt at exactly this, and which one a connection
        gets is up to the resolver.
        """
        host = (urlsplit(url).hostname or "").strip("[]")
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            # Already checked by validate_url, and there is nothing to resolve.
            return

        try:
            addresses = await self._resolve(host)
        except OSError as failure:
            raise FetchFailed(f"Could not resolve {host}: {failure}") from failure
        if not addresses:
            raise FetchFailed(f"Could not resolve {host}")

        for address in addresses:
            if is_blocked_address(address):
                logger.warning(
                    "Refusing %s: %s resolves to %s, which is not a public address",
                    url,
                    host,
                    address,
                )
                raise UnsafeUrl(
                    f"{host} resolves to {address}, which is not a public address"
                )

    async def _get(self, url: str, *, etag: str) -> tuple[httpx.Response, bytes]:
        """The response and its body, read with the cap enforced as it arrives.

        The body is returned alongside rather than left on the response: a
        streamed response has not buffered it, and reaching into httpx to
        pretend otherwise is the kind of thing that breaks on an upgrade.
        """
        headers = {
            "User-Agent": self._user_agent,
            # Asking for what can be read. A server offering a PDF or plain
            # text for the same URL is welcome to.
            "Accept": "text/html,application/xhtml+xml,text/plain,application/pdf;q=0.9,*/*;q=0.5",
            "Accept-Language": "en",
        }
        if etag:
            headers["If-None-Match"] = etag

        try:
            client = self._ready()
            request = client.build_request("GET", url, headers=headers)
            response = await client.send(request, stream=True)
        except httpx.HTTPError as failure:
            raise FetchFailed(f"Could not fetch {url}: {failure}") from failure

        try:
            # Content-Length is a claim, so it is used to refuse early and the
            # real count below is what is enforced.
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > self._max_bytes:
                raise FetchFailed(
                    f"{url} says it is {int(declared):,} bytes, past the "
                    f"{self._max_bytes:,} byte limit"
                )

            body = bytearray()
            async for chunk in response.aiter_bytes(_CHUNK):
                body += chunk
                if len(body) > self._max_bytes:
                    raise FetchFailed(
                        f"{url} is larger than the {self._max_bytes:,} byte limit"
                    )
        except httpx.HTTPError as failure:
            raise FetchFailed(f"Could not read {url}: {failure}") from failure
        finally:
            await response.aclose()

        return response, bytes(body)

    async def aclose(self) -> None:
        """Close the client, if one was ever built."""
        if self._client is not None:
            await self._client.aclose()


async def _resolve(host: str) -> list[str]:
    """Every address a name has, A and AAAA.

    In a thread: ``getaddrinfo`` blocks, and a slow DNS server would otherwise
    stop the event loop for everyone.
    """
    infos = await asyncio.to_thread(
        socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM
    )
    return list(dict.fromkeys(info[4][0] for info in infos))
