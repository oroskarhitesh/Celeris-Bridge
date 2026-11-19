"""
URL policy: what counts as the same page, what counts as a link, and what must
never be fetched.

All pure. The crawler needs every one of these decisions and none of them
needs a socket, which is why they are here and not in the adapter -- the
address rules especially, since a guard that can only be tested by pointing it
at a real metadata endpoint is a guard nobody tests.

**On the safety rules.** A workspace asks the product to fetch a URL it
chose. That makes this project an HTTP client aimed by a stranger, which is
the shape of a server-side request forgery: the obvious target is a cloud
instance's metadata service at ``169.254.169.254``, which answers to anything
inside the network and hands out credentials, and the next is whatever else
sits in the private range -- this project's own Mongo, an internal admin
panel. So the rules here are a deny-list of address ranges applied to *every*
address a name resolves to, and a short allow-list of schemes and ports.
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

__all__ = [
    "ALLOWED_PORTS",
    "UnsafeUrl",
    "is_blocked_address",
    "links_from",
    "normalise",
    "registered_domain",
    "same_site",
    "sitemaps_from_robots",
    "validate_url",
]


class UnsafeUrl(ValueError):
    """A URL this project must not fetch."""


#: ``http`` and ``https`` only. ``file://`` would read the server's disk and
#: ``gopher://`` and friends are protocol-smuggling primitives.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

#: The ports a public website is served on. A workspace pointing the fetcher
#: at port 27017 or 6379 is port-scanning the network it is hosted in, and no
#: legitimate marketing site needs an unusual port.
ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})

#: Dropped when deciding whether two URLs are the same page. Campaign tags
#: change on every link to a page without changing the page, and crawling the
#: same article five times is five embedding bills.
_TRACKING = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "gbraid",
        "wbraid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "igshid",
        "_ga",
        "_gl",
        "yclid",
    }
)

#: Hostnames that resolve inside the network or to the host itself, whatever
#: DNS says. Checked before resolution, so the common case costs nothing.
_BLOCKED_NAMES = frozenset({"localhost", "metadata", "metadata.google.internal"})
_BLOCKED_SUFFIXES = ("localhost", ".localhost", ".local", ".internal", ".home.arpa")


def validate_url(url: str) -> str:
    """Check a URL and return it normalised. Raises :class:`UnsafeUrl`.

    This is the *name*-level half of the guard: scheme, port, credentials and
    hostname. The address-level half needs DNS and lives in the adapter, which
    calls :func:`is_blocked_address` on everything the name resolves to.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError as broken:
        raise UnsafeUrl(f"Not a URL: {url!r}") from broken

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UnsafeUrl(
            f"Only http and https can be fetched, not {parts.scheme or 'a relative URL'!r}"
        )
    if parts.username or parts.password:
        # Credentials in a URL are either an attempt to authenticate to
        # something internal or a secret about to be written to a log.
        raise UnsafeUrl("A URL with a username or password in it will not be fetched")

    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeUrl(f"No host in {url!r}")

    try:
        port = parts.port
    except ValueError as bad:
        raise UnsafeUrl(f"Not a port number in {url!r}") from bad
    if port is not None and port not in ALLOWED_PORTS:
        raise UnsafeUrl(
            f"Port {port} will not be fetched; allowed: "
            f"{', '.join(str(one) for one in sorted(ALLOWED_PORTS))}"
        )

    if host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeUrl(f"{host!r} is not a public address")

    # A literal address skips DNS, so the only check it will ever get is this
    # one. ``http://[::ffff:169.254.169.254]/`` is the metadata service
    # written as IPv6, and ``http://2130706433/`` is 127.0.0.1 as an integer.
    literal = _as_address(host)
    if literal is not None and is_blocked_address(literal):
        raise UnsafeUrl(f"{host!r} is not a public address")

    return normalise(url)


def _as_address(host: str) -> ipaddress._BaseAddress | None:
    """The address a host *is*, if it is written as one rather than as a name.

    ``inet_aton`` rather than a pattern of my own, because it accepts exactly
    what a resolver accepts: dotted quads, but also ``2130706433``,
    ``0x7f000001`` and ``0177.0.0.1``, which are all 127.0.0.1 and all get
    past a guard that only understands the first form. It is pure arithmetic
    on a string -- no name is looked up here.
    """
    stripped = host.strip("[]")
    try:
        return ipaddress.ip_address(stripped)
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(stripped))
    except (OSError, ipaddress.AddressValueError, UnicodeEncodeError):
        return None


def is_blocked_address(address: ipaddress._BaseAddress | str) -> bool:
    """Whether an address is somewhere this project must not send a request.

    Applied to every address a hostname resolves to, because the hostname is
    chosen by a workspace and ``internal.acme.example`` is one DNS record away
    from being ``10.0.0.5``.

    Blocked: loopback, the private ranges, link-local -- which is where a cloud
    instance's metadata service lives and the single most valuable target here
    -- unique-local IPv6, multicast, the unspecified address, and everything
    else reserved. An IPv4 address written as IPv6 is unwrapped first, so
    ``::ffff:127.0.0.1`` does not pass by looking like a global v6 address.
    """
    if isinstance(address, str):
        parsed = _as_address(address)
        if parsed is None:
            return True  # Not an address at all: nothing to approve.
        address = parsed

    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif getattr(address, "sixtofour", None) is not None:
            address = address.sixtofour  # type: ignore[assignment]

    return (
        not address.is_global
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def normalise(url: str) -> str:
    """The canonical form of a URL, for deciding whether two are one page.

    Lower-cases the scheme and host, drops the fragment, drops a default port,
    drops campaign parameters, and resolves dot segments. Query parameters that
    remain keep their order: ``?page=2&sort=date`` and ``?sort=date&page=2``
    are the same page to most sites and different pages to some, and guessing
    wrong in the second case loses content. Dropping the fragment is safe in a
    way that is worth saying: ``#pricing`` is a position in a document the
    server never sees.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    port = parts.port

    authority = host
    if ":" in host and not host.startswith("["):
        authority = f"[{host}]"
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        authority = f"{authority}:{port}"

    # ``urljoin`` against the bare authority is what resolves ``..`` and
    # ``.`` segments: a crawl that treats /a/b/../c and /a/c as two pages
    # fetches and embeds the same page twice.
    squashed = re.sub(r"/{2,}", "/", parts.path) or "/"
    path = urlsplit(urljoin(f"{scheme}://{authority}/", squashed)).path or "/"
    query = "&".join(
        pair
        for pair in parts.query.split("&")
        if pair and pair.split("=", 1)[0].lower() not in _TRACKING
    )
    return urlunsplit((scheme, authority, path, query, ""))


def registered_domain(host: str) -> str:
    """The last two labels of a hostname.

    Deliberately crude, and wrong for ``co.uk``: ``shop.acme.co.uk`` reduces
    to ``co.uk``, which would make every ``.co.uk`` site one site. That is why
    :func:`same_site` does not use this for its decision -- it compares
    suffixes against the start host instead, which needs no list of public
    suffixes to be kept up to date.
    """
    labels = host.lower().rstrip(".").split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host.lower()


def same_site(url: str, *, root: str) -> bool:
    """Whether ``url`` is on the same site as ``root``.

    The same host, or a subdomain of it. ``acme.example`` therefore covers
    ``blog.acme.example`` and not the other way round, which matches what
    someone means by "crawl my website": they give the front page and expect
    the blog, not their parent company.
    """
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    start = (urlsplit(root).hostname or "").lower().rstrip(".")
    if not host or not start:
        return False
    return host == start or host.endswith("." + start)


#: Extensions that are not pages. Fetching them wastes the page budget, and a
#: stylesheet or a font indexed as knowledge is a passage of gibberish that can
#: be retrieved and cited. PDFs are **not** here: they are documents, and
#: extraction reads them.
_NOT_A_PAGE = (
    ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".avif", ".ico", ".bmp",
    ".mp3", ".mp4", ".m4a", ".wav", ".avi", ".mov", ".webm", ".ogg",
    ".zip", ".gz", ".tar", ".rar", ".7z", ".dmg", ".exe", ".msi", ".apk",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
)

_HREF = re.compile(
    r"""<\s*a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
    re.IGNORECASE,
)
_BASE = re.compile(
    r"""<\s*base\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
    re.IGNORECASE,
)


def links_from(markup: str, *, base: str) -> tuple[str, ...]:
    """Every page worth following from this one, normalised and in order.

    In document order, de-duplicated, because order is the only signal a crawl
    has about what a site thinks is important: the navigation comes before the
    footer. Relative URLs are resolved against ``<base href>`` when the page
    declares one and against the page's own URL otherwise, which is what a
    browser does and what the site's own links assume.

    Anything that is not an http(s) page is dropped here rather than fetched
    and rejected: ``mailto:``, ``javascript:``, ``tel:``, and the asset
    extensions. A link marked ``rel="nofollow"`` is followed anyway -- that
    attribute asks search engines not to pass ranking, not to stay away, and
    a customer's own site often marks its internal links with it.
    """
    declared = _BASE.search(markup)
    if declared:
        candidate = _first(declared) or ""
        if candidate:
            base = urljoin(base, html.unescape(candidate.strip()))

    found: list[str] = []
    seen: set[str] = set()
    for match in _HREF.finditer(markup):
        raw = _first(match)
        if not raw:
            continue
        target = html.unescape(raw.strip())
        if not target or target.startswith("#"):
            continue
        absolute = urljoin(base, target)
        parts = urlsplit(absolute)
        if parts.scheme.lower() not in _ALLOWED_SCHEMES:
            continue
        if parts.path.lower().endswith(_NOT_A_PAGE):
            continue
        canonical = normalise(absolute)
        if canonical not in seen:
            seen.add(canonical)
            found.append(canonical)
    return tuple(found)


def _first(match: re.Match[str]) -> str | None:
    return next((group for group in match.groups() if group is not None), None)


_SITEMAP = re.compile(r"^\s*sitemap\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)


def sitemaps_from_robots(text: str) -> tuple[str, ...]:
    """The ``Sitemap:`` lines in a robots.txt.

    Read here because the standard library's robots parser ignores them, and
    because a sitemap is the one place a site states which of its pages matter
    -- far better than guessing from the navigation.
    """
    return tuple(dict.fromkeys(match.group(1).strip() for match in _SITEMAP.finditer(text)))
