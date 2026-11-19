"""
Uploads, downloads and crawls, over HTTP.

The pipeline itself is tested without a workspace in sight. What is tested
here is what the HTTP layer owns: the cap enforced while reading rather than
after, the source id it generates rather than accepts, the download that
cannot render, and the crawl limits a request may lower and may not raise.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from celeris_bridge.api.routes import knowledge as knowledge_routes
from celeris_bridge.domain.knowledge.crawl import CrawlLimits, CrawlReport

from tests.api.conftest import auth

HTML = (
    b"<html><head><title>About Acme</title></head><body>"
    b"<script>alert('hello')</script>"
    b"<h1>About Acme</h1><p>We are a small team in Leeds building support tools."
    b" We have been going since 2019 and we answer every email ourselves.</p>"
    b"</body></html>"
)


def upload(client, name: str, content: bytes, kind: str = "text/plain", **form):
    return client.post(
        "/v1/knowledge/sources",
        files={"file": (name, content, kind)},
        data=form or None,
        headers=auth("ws"),
    )


class TestUploading:
    def test_a_document_is_stored_and_indexed(self, client) -> None:
        answer = upload(
            client,
            "pricing.txt",
            b"Starter is 29 per seat per month. Growth is 49 per seat per month.",
        )
        assert answer.status_code == 201, answer.text
        body = answer.json()
        assert body["passages"] > 0
        assert body["indexed"] is True
        assert body["kind"] == "text"
        assert body["sourceName"] == "pricing.txt"
        assert body["model"] == "hashing-dev:256"

        listed = client.get("/v1/knowledge/sources", headers=auth("ws")).json()
        assert [one["name"] for one in listed] == ["pricing.txt"]
        assert listed[0]["status"] == "indexed"

    def test_html_is_read_as_text(self, client) -> None:
        answer = upload(client, "about.html", HTML, "text/html")
        assert answer.status_code == 201, answer.text
        assert answer.json()["kind"] == "html"
        assert answer.json()["passages"] > 0

    def test_a_document_with_nothing_in_it_is_not_an_error(self, client) -> None:
        # A blank page or a scan with no text layer. It needs OCR, not a
        # retry, so it is reported as processed and empty rather than failed.
        answer = upload(client, "blank.txt", b"   \n   \n")
        assert answer.status_code == 201, answer.text
        assert answer.json()["indexed"] is False
        assert answer.json()["skipped"]
        source_id = answer.json()["sourceId"]
        shown = client.get(
            f"/v1/knowledge/sources/{source_id}", headers=auth("ws")
        ).json()
        assert shown["status"] == "empty"

    def test_a_source_id_is_generated(self, client) -> None:
        # An id a client chooses is an id a client can collide with somebody
        # else's existing document.
        first = upload(client, "notes.txt", b"One set of notes.").json()
        second = upload(client, "notes.txt", b"Another set of notes.").json()
        assert first["sourceId"] != second["sourceId"]
        assert first["sourceId"].startswith("up-")

    def test_a_given_source_id_replaces_that_document(self, client) -> None:
        upload(client, "faq.txt", b"The old answer about delivery times.", sourceId="faq")
        again = upload(
            client, "faq.txt", b"The new answer about delivery times.", sourceId="faq"
        )
        assert again.json()["sourceId"] == "faq"
        listed = client.get("/v1/knowledge/sources", headers=auth("ws")).json()
        assert len(listed) == 1
        assert b"new answer" in client.get(
            "/v1/knowledge/sources/faq/original", headers=auth("ws")
        ).content

    def test_a_source_id_that_is_a_path_is_refused(self, client) -> None:
        # The store checks this again. The layer that would be exploited is
        # not the right place for the only check.
        answer = upload(client, "faq.txt", b"Anything.", sourceId="../../etc/passwd")
        assert answer.status_code == 422
        assert answer.json()["error"]["code"] == "VALIDATION_FAILED"

    def test_a_file_name_that_is_a_path_becomes_a_name(self, client) -> None:
        answer = upload(client, "../../etc/passwd", b"Not actually a password file.")
        assert answer.status_code == 201, answer.text
        assert answer.json()["sourceName"] == "passwd"

    def test_too_much_is_refused_while_it_is_arriving(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The cap is enforced per chunk, so the whole body never has to be
        # buffered in order to decide that it was too much.
        monkeypatch.setattr(knowledge_routes, "MAX_UPLOAD_BYTES", 1024)
        monkeypatch.setattr(knowledge_routes, "_CHUNK", 256)
        answer = upload(client, "big.txt", b"x" * 4096)
        assert answer.status_code == 413
        assert answer.json()["error"]["code"] == "DOCUMENT_TOO_LARGE"
        assert client.get("/v1/knowledge/sources", headers=auth("ws")).json() == []


class TestDownloadingTheOriginal:
    def test_it_comes_back_byte_for_byte(self, client) -> None:
        # What makes a file that would not parse diagnosable: whoever looks at
        # it gets what the extractor was given.
        content = "Prix: 29 € par siège.".encode("utf-8")
        source_id = upload(client, "prix.txt", content).json()["sourceId"]
        answer = client.get(
            f"/v1/knowledge/sources/{source_id}/original", headers=auth("ws")
        )
        assert answer.status_code == 200
        assert answer.content == content

    def test_it_downloads_and_never_renders(self, client) -> None:
        # An uploaded HTML file served inline from the admin origin is stored
        # cross-site scripting against the admin application.
        source_id = upload(client, "about.html", HTML, "text/html").json()["sourceId"]
        answer = client.get(
            f"/v1/knowledge/sources/{source_id}/original", headers=auth("ws")
        )
        assert answer.headers["content-type"] == "application/octet-stream"
        assert answer.headers["content-disposition"].startswith("attachment")
        assert answer.headers["x-content-type-options"] == "nosniff"

    def test_a_name_cannot_break_out_of_the_header(self, client) -> None:
        # The name is a customer's text being written into a header, so the
        # two characters that could end the quoted string early are removed.
        source_id = upload(
            client, 'in"voice\2024.txt', b"Anything."
        ).json()["sourceId"]
        answer = client.get(
            f"/v1/knowledge/sources/{source_id}/original", headers=auth("ws")
        )
        disposition = answer.headers["content-disposition"]
        assert disposition.startswith('attachment; filename="')
        assert disposition.endswith('"')
        assert '"' not in disposition[len('attachment; filename="') : -1]
        assert "\\" not in disposition


class TestDeletingAndReindexing:
    def test_deleting_takes_the_passages_with_it(self, client) -> None:
        source_id = upload(
            client, "pricing.txt", b"Starter is 29 per seat per month."
        ).json()["sourceId"]
        deleted = client.delete(
            f"/v1/knowledge/sources/{source_id}", headers=auth("ws")
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["passagesRemoved"] > 0
        assert client.get("/v1/knowledge/sources", headers=auth("ws")).json() == []

    def test_deleting_something_that_is_not_there(self, client) -> None:
        answer = client.delete("/v1/knowledge/sources/nope", headers=auth("ws"))
        assert answer.status_code == 404
        assert answer.json()["error"]["code"] == "SOURCE_NOT_FOUND"

    def test_reindexing_does_nothing_when_everything_is_current(self, client) -> None:
        upload(client, "pricing.txt", b"Starter is 29 per seat per month.")
        answer = client.post("/v1/knowledge/reindex", headers=auth("ws"))
        assert answer.status_code == 200, answer.text
        assert answer.json() == []


class StubKnowledge:
    """Records what the route asked for, and crawls nothing.

    The route's own rule is what is under test -- that a request may lower a
    limit and never raise one -- and reaching a real web server to check it
    would be a test that fails when somebody's wifi does.
    """

    def __init__(self, limits: CrawlLimits) -> None:
        self.limits = limits
        self.asked: list[CrawlLimits] = []

    async def crawl(self, *, workspace_id, start_url, limits, agent_instance_ids=()):
        self.asked.append(limits)
        return CrawlReport(start_url=start_url)

    async def robots_for(self, url: str) -> str | None:
        return "User-agent: *\nDisallow: /admin\n"


@pytest.fixture
def stub(client):
    """The app, with the crawl replaced by a recorder."""
    application = client.app.state.application
    stubbed = StubKnowledge(application.knowledge.limits)
    client.app.state.application = replace(application, knowledge=stubbed)
    return stubbed


class TestCrawling:
    def test_a_private_address_is_refused_before_anything_is_fetched(
        self, client
    ) -> None:
        answer = client.post(
            "/v1/knowledge/crawl",
            json={"startUrl": "http://127.0.0.1:8000/admin"},
            headers=auth("ws"),
        )
        assert answer.status_code == 400
        assert answer.json()["error"]["code"] == "UNSAFE_URL"

    def test_a_request_cannot_raise_the_configured_cap(self, client, stub) -> None:
        answer = client.post(
            "/v1/knowledge/crawl",
            json={
                "startUrl": "https://acme.test/",
                "maxPages": 500,
                "maxDepth": 9,
                "delaySeconds": 0.0,
            },
            headers=auth("ws"),
        )
        assert answer.status_code == 200, answer.text
        asked = stub.asked[-1]
        assert asked.max_pages == stub.limits.max_pages
        assert asked.max_depth == stub.limits.max_depth
        # The delay is the other way round: waiting longer is the polite
        # direction, so the configured value is a floor.
        assert asked.delay_seconds == stub.limits.delay_seconds

    def test_a_request_may_ask_for_less(self, client, stub) -> None:
        client.post(
            "/v1/knowledge/crawl",
            json={
                "startUrl": "https://acme.test/",
                "maxPages": 3,
                "maxDepth": 1,
                "delaySeconds": 2.0,
            },
            headers=auth("ws"),
        )
        asked = stub.asked[-1]
        assert (asked.max_pages, asked.max_depth, asked.delay_seconds) == (3, 1, 2.0)

    def test_robots_is_readable_on_its_own(self, client, stub) -> None:
        # "The crawler indexed four pages" is much easier to explain when the
        # admin can see that the site's own rules disallow the rest.
        answer = client.get(
            "/v1/knowledge/robots?url=https://acme.test/", headers=auth("ws")
        )
        assert answer.status_code == 200
        assert "Disallow: /admin" in answer.json()["text"]
