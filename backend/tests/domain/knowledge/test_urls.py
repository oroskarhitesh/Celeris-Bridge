"""
Tests for URL policy.

Two subjects, both pure. **Normalisation** decides whether two URLs are one
page, which decides whether a crawl embeds a page once or five times. **The
safety rules** decide what the product will point an HTTP client at, which is
the difference between a crawler and a server-side request forgery.

The address cases are written out as a table on purpose: each row is a real
way this guard gets bypassed, and a table makes a missing one visible.
"""

from __future__ import annotations

import pytest

from celeris_bridge.domain.knowledge.urls import (
    UnsafeUrl,
    is_blocked_address,
    links_from,
    normalise,
    same_site,
    sitemaps_from_robots,
    validate_url,
)


class TestNormalising:
    @pytest.mark.parametrize(
        "url,expected",
        [
            # The scheme and host are case-insensitive; the path is not.
            ("HTTPS://Acme.Example/Pricing", "https://acme.example/Pricing"),
            # A default port is not part of the identity of a page.
            ("https://acme.example:443/p", "https://acme.example/p"),
            ("http://acme.example:80/p", "http://acme.example/p"),
            # A non-default one is.
            ("https://acme.example:8443/p", "https://acme.example:8443/p"),
            # A fragment is a position in a document the server never sees.
            ("https://acme.example/p#pricing", "https://acme.example/p"),
            # An empty path is the front page.
            ("https://acme.example", "https://acme.example/"),
            # Dot segments and doubled slashes resolve, or /a/b/../c and /a/c
            # are fetched and embedded as two pages.
            ("https://acme.example/a//b/../c", "https://acme.example/a/c"),
            ("https://acme.example/x/./y/", "https://acme.example/x/y/"),
            # A trailing dot on a host is legal DNS and the same host.
            ("https://acme.example./p", "https://acme.example/p"),
        ],
    )
    def test_the_same_page_written_differently_is_one_url(
        self, url: str, expected: str
    ) -> None:
        assert normalise(url) == expected

    def test_campaign_parameters_are_dropped(self) -> None:
        # They change on every link to a page without changing the page.
        assert normalise(
            "https://acme.example/p?utm_source=newsletter&utm_campaign=q4&fbclid=abc"
        ) == "https://acme.example/p"

    def test_real_parameters_are_kept(self) -> None:
        assert normalise("https://acme.example/p?page=2&sort=date") == (
            "https://acme.example/p?page=2&sort=date"
        )

    def test_the_order_of_parameters_is_left_alone(self) -> None:
        # Sorting them would be right for most sites and wrong for some, and
        # being wrong here means a page is never fetched at all.
        assert normalise("https://acme.example/p?b=2&a=1") != normalise(
            "https://acme.example/p?a=1&b=2"
        )

    def test_a_trailing_slash_is_a_different_page(self) -> None:
        # Because it is, to a server. The content checksum catches the case
        # where it turns out to be the same page; guessing here would skip a
        # real one.
        assert normalise("https://acme.example/p") != normalise("https://acme.example/p/")


class TestWhatMustNotBeFetched:
    @pytest.mark.parametrize(
        "url,because",
        [
            ("http://169.254.169.254/latest/meta-data/", "the cloud metadata service"),
            ("http://metadata.google.internal/", "the same, by name"),
            ("http://127.0.0.1:8080/admin", "the server itself"),
            ("http://localhost/admin", "the same, by name"),
            ("http://[::1]/", "the same, over IPv6"),
            ("http://10.0.0.5/", "the private range"),
            ("http://192.168.1.1/", "a home router"),
            ("http://172.16.0.1/", "the other private range"),
            ("http://[fd00::1]/", "unique-local IPv6"),
            ("http://0.0.0.0/", "the unspecified address"),
            ("http://2130706433/", "127.0.0.1 written as an integer"),
            ("http://0x7f000001/", "and as hexadecimal"),
            ("http://0177.0.0.1/", "and as octal"),
            ("http://[::ffff:169.254.169.254]/", "metadata as IPv4-mapped IPv6"),
            ("http://[::ffff:127.0.0.1]/", "loopback as IPv4-mapped IPv6"),
            ("http://mongo.internal/", "an internal name"),
            ("http://db.local/", "an mDNS name"),
            ("file:///etc/passwd", "the server's disk"),
            ("gopher://acme.example/", "a protocol-smuggling scheme"),
            ("ftp://acme.example/", "not fetchable here"),
            ("http://user:secret@acme.example/", "credentials in a URL"),
            ("http://acme.example:27017/", "Mongo's port"),
            ("http://acme.example:6379/", "Redis's port"),
            ("http://acme.example:22/", "ssh"),
            ("/pricing", "not absolute"),
            ("https:///pricing", "no host"),
            ("", "nothing at all"),
        ],
    )
    def test_each_of_these_is_refused(self, url: str, because: str) -> None:
        with pytest.raises(UnsafeUrl):
            validate_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://acme.example/pricing",
            "http://acme.example/pricing",
            "https://blog.acme.example/post/1",
            "https://acme.example:8443/pricing",
            "https://acme.example:8080/pricing",
            "https://8.8.8.8/",
        ],
    )
    def test_an_ordinary_public_page_is_allowed(self, url: str) -> None:
        # A guard that refuses real sites is a guard someone turns off.
        assert validate_url(url)

    def test_what_comes_back_is_normalised(self) -> None:
        # So a caller cannot check one string and fetch another.
        assert validate_url("HTTPS://Acme.Example/p#x") == "https://acme.example/p"

    def test_the_message_says_which_ports_are_allowed(self) -> None:
        with pytest.raises(UnsafeUrl) as caught:
            validate_url("http://acme.example:9000/")
        assert "443" in str(caught.value)


class TestAddressRules:
    """Applied to every address a *name* resolves to, in the adapter."""

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "169.254.169.254",
            "10.1.2.3",
            "192.168.0.1",
            "172.20.1.1",
            "0.0.0.0",
            "100.64.0.1",  # carrier-grade NAT
            "224.0.0.1",  # multicast
            "255.255.255.255",
            "::1",
            "fe80::1",
            "fd00::1",
            "::",
            "::ffff:10.0.0.1",
            "2002:7f00:0001::",  # 6to4 wrapping 127.0.0.1
            "not-an-address",
        ],
    )
    def test_blocked(self, address: str) -> None:
        assert is_blocked_address(address) is True

    @pytest.mark.parametrize("address", ["8.8.8.8", "93.184.216.34", "2606:2800:220:1::"])
    def test_allowed(self, address: str) -> None:
        assert is_blocked_address(address) is False


class TestSameSite:
    def test_the_same_host_is_the_same_site(self) -> None:
        assert same_site("https://acme.example/p", root="https://acme.example/") is True

    def test_a_subdomain_is_the_same_site(self) -> None:
        # "Crawl my website" means the blog too.
        assert same_site("https://blog.acme.example/p", root="https://acme.example/") is True

    def test_a_parent_domain_is_not(self) -> None:
        # Starting at the blog does not mean indexing the whole company.
        assert same_site("https://acme.example/p", root="https://blog.acme.example/") is False

    def test_another_site_is_not(self) -> None:
        assert same_site("https://other.example/p", root="https://acme.example/") is False

    def test_a_host_that_merely_ends_the_same_way_is_not(self) -> None:
        # "notacme.example" ends with "acme.example" as a string and is a
        # different company.
        assert same_site("https://notacme.example/p", root="https://acme.example/") is False


class TestLinks:
    PAGE = (
        "<a href='/pricing'>Pricing</a>"
        "<a href=\"docs/start.html\">Start</a>"
        "<a href='https://acme.example/about'>About</a>"
        "<a href='mailto:sales@acme.example'>Email</a>"
        "<a href='javascript:void(0)'>Menu</a>"
        "<a href='tel:+441234'>Call</a>"
        "<a href='#top'>Top</a>"
        "<a href='/style.css'>css</a>"
        "<a href='/logo.png'>logo</a>"
        "<a href='/report.pdf'>report</a>"
        "<a href='/pricing'>Pricing again</a>"
        "<a href='/handbook' rel='nofollow'>Handbook</a>"
    )

    def links(self, markup: str = "", base: str = "https://acme.example/index.html"):
        return links_from(markup or self.PAGE, base=base)

    def test_relative_links_resolve_against_the_page(self) -> None:
        assert "https://acme.example/docs/start.html" in self.links()

    def test_root_relative_links_resolve_against_the_host(self) -> None:
        assert "https://acme.example/pricing" in self.links()

    def test_a_base_element_wins(self) -> None:
        # What a browser does, and what the site's own links assume.
        found = self.links("<base href='/app/'><a href='x.html'>x</a>")
        assert found == ("https://acme.example/app/x.html",)

    def test_schemes_that_are_not_pages_are_dropped(self) -> None:
        found = self.links()
        assert not any(one.startswith(("mailto", "javascript", "tel")) for one in found)

    def test_a_bare_fragment_is_not_a_link(self) -> None:
        # It is the same page. Following it would fetch the page again.
        assert "https://acme.example/index.html" not in self.links()

    def test_assets_are_dropped(self) -> None:
        # They waste the page budget, and a stylesheet indexed as knowledge is
        # a passage of gibberish that can be retrieved and cited.
        found = self.links()
        assert not any(one.endswith((".css", ".png")) for one in found)

    def test_a_pdf_is_not_an_asset(self) -> None:
        # It is a document, and extraction reads it.
        assert "https://acme.example/report.pdf" in self.links()

    def test_each_link_appears_once(self) -> None:
        found = self.links()
        assert len(found) == len(set(found))

    def test_the_order_is_the_order_on_the_page(self) -> None:
        # The only signal a crawl has about what the site thinks matters: the
        # navigation comes before the footer.
        found = self.links()
        assert found[0] == "https://acme.example/pricing"
        assert found.index("https://acme.example/about") > found.index(
            "https://acme.example/docs/start.html"
        )

    def test_nofollow_is_followed_anyway(self) -> None:
        # It asks search engines not to pass ranking, not to stay away, and a
        # customer's own site often marks its internal links with it.
        assert "https://acme.example/handbook" in self.links()

    def test_entities_in_an_href_are_decoded(self) -> None:
        found = self.links("<a href='/search?q=a&amp;page=2'>s</a>")
        assert found == ("https://acme.example/search?q=a&page=2",)

    def test_unquoted_hrefs_are_read(self) -> None:
        # Hand-written HTML is full of them.
        assert self.links("<a href=/pricing >p</a>") == ("https://acme.example/pricing",)

    def test_a_page_with_no_links_is_empty_not_an_error(self) -> None:
        assert self.links("<p>Nothing here.</p>") == ()


class TestSitemaps:
    def test_sitemap_lines_are_found(self) -> None:
        # The standard library's robots parser ignores them, and a sitemap is
        # the one place a site states which of its pages matter.
        text = (
            "User-agent: *\nDisallow: /admin\n"
            "Sitemap: https://acme.example/sitemap.xml\n"
            "sitemap:  https://acme.example/news-sitemap.xml\n"
        )
        assert sitemaps_from_robots(text) == (
            "https://acme.example/sitemap.xml",
            "https://acme.example/news-sitemap.xml",
        )

    def test_none_is_empty(self) -> None:
        assert sitemaps_from_robots("User-agent: *\nAllow: /\n") == ()
