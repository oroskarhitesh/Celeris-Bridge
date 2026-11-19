"""
Tests for reading a file.

Pure functions and real bytes: a hand-built PDF, a page with a broken script
tag, a file saved out of Word. No fakes, because there is nothing to fake.

The weight is on :func:`detect_kind` — a format decided wrongly is a document
silently indexed as its own markup — and on the rule that a file which cannot
be read usefully produces empty text rather than an exception.
"""

from __future__ import annotations

import pytest

from celeris_bridge.domain.knowledge.extraction import (
    UnreadableDocument,
    UnsupportedDocument,
    detect_kind,
    extract,
    html_to_text,
)

PDF_HEADER = b"%PDF-1.4\n"


def minimal_pdf(body: str = "The Starter plan is 29 per seat per month.") -> bytes:
    """A real one-page PDF, built by hand.

    Hand-built rather than a fixture file so the test is readable and the
    repository holds no binaries, and so it is obvious that what is being
    tested is a PDF and not a mock of one.
    """
    text = body.replace("(", r"\(").replace(")", r"\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(PDF_HEADER)
    offsets = []
    for number, body_bytes in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body_bytes + b"\nendobj\n"

    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{start}\n%%EOF\n"
    ).encode()
    return bytes(out)


class TestDecidingTheFormat:
    def test_the_bytes_beat_the_name(self) -> None:
        # An upload form is the one place where everything but the content is
        # a claim by whoever is uploading.
        assert detect_kind(name="notes.txt", content=minimal_pdf()) == "pdf"

    def test_the_bytes_beat_the_content_type(self) -> None:
        assert detect_kind(content_type="text/plain", content=minimal_pdf()) == "pdf"

    def test_a_declared_type_is_used_when_the_bytes_say_nothing(self) -> None:
        assert detect_kind(content_type="text/html", content=b"Plans and pricing") == "html"
        assert detect_kind(content_type="text/markdown", content=b"hello") == "markdown"

    def test_the_extension_is_the_last_resort(self) -> None:
        assert detect_kind(name="handbook.md", content=b"hello") == "markdown"
        assert detect_kind(name="page.htm", content=b"hello") == "html"
        assert detect_kind(name="notes.txt", content=b"hello") == "text"

    def test_a_charset_on_the_content_type_is_ignored(self) -> None:
        assert detect_kind(content_type="text/html; charset=utf-8", content=b"x") == "html"

    def test_a_page_with_no_name_at_all_is_still_recognised(self) -> None:
        # The crawler has a URL, not a file name, and plenty of URLs end in a
        # slash.
        assert detect_kind(content=b"<!doctype html><html><body>Hi</body></html>") == "html"

    def test_a_pdf_that_is_not_one_is_refused_not_guessed(self) -> None:
        # Feeding a spreadsheet to the PDF reader produces its complaint, not
        # an explanation, and the admin screen shows the complaint.
        with pytest.raises(UnreadableDocument):
            detect_kind(name="prices.pdf", content=b"PK\x03\x04 a zip file")
        with pytest.raises(UnreadableDocument):
            detect_kind(content_type="application/pdf", content=b"not really")

    def test_a_format_this_build_cannot_read_says_which_it_can(self) -> None:
        with pytest.raises(UnsupportedDocument) as caught:
            detect_kind(name="deck.pptx", content=b"PK\x03\x04")
        assert "PDF" in str(caught.value)

    def test_an_unnamed_blob_is_read_as_text(self) -> None:
        # Better than refusing it: most things with no extension and no tags
        # are text, and text is the one reader that cannot make it worse.
        assert detect_kind(content=b"Plans start at 29 per seat.") == "text"


class TestEncodings:
    def test_a_utf8_byte_order_mark_does_not_become_a_character(self) -> None:
        # Otherwise every document from Notepad begins with an invisible one,
        # which lands in the first chunk and in the citation.
        document = extract("﻿Plans start at 29.".encode("utf-8"), name="a.txt")
        assert document.text == "Plans start at 29."

    def test_utf16_is_read(self) -> None:
        document = extract("Plans start at 29.".encode("utf-16"), name="a.txt")
        assert document.text == "Plans start at 29."

    def test_a_file_exported_from_word_is_read(self) -> None:
        # cp1252, not UTF-8, and it decodes as neither if the fallback is
        # missing -- the single most common real upload after PDF.
        document = extract("Café — résumé".encode("cp1252"), name="a.txt")
        assert document.text == "Café — résumé"

    def test_bytes_that_decode_as_nothing_still_produce_a_document(self) -> None:
        # One mangled character beats rejecting a customer's handbook.
        document = extract(b"price: \xed\xa0\x80 29", name="a.txt")
        assert "29" in document.text


class TestHtml:
    PAGE = (
        "<!doctype html><html><head><title>Pricing &amp; plans</title>"
        "<style>body{color:red}</style><script>var a = 1 < 2;</script></head>"
        "<body><nav><a href='/'>Home</a></nav><h1>Plans</h1>"
        "<p>Starter is 29 per seat.</p><p>Teams is 49.</p></body></html>"
    )

    def test_the_title_is_offered_separately(self) -> None:
        document = extract(self.PAGE.encode(), name="pricing.html")
        assert document.title == "Pricing & plans"
        assert document.kind == "html"

    def test_the_text_is_the_readable_part(self) -> None:
        document = extract(self.PAGE.encode(), name="pricing.html")
        assert "Starter is 29 per seat." in document.text
        assert "Teams is 49." in document.text

    def test_script_and_style_do_not_become_knowledge(self) -> None:
        # Otherwise a passage of CSS is retrievable, citable, and will be
        # quoted at a visitor who asked about pricing.
        document = extract(self.PAGE.encode(), name="pricing.html")
        assert "color:red" not in document.text
        assert "var a" not in document.text

    def test_an_unclosed_script_does_not_leak_its_source(self) -> None:
        # A truncated download is the normal way this happens, and without the
        # unclosed-tag rule the rest of the file is the document.
        _, text = html_to_text("<p>Plans</p><script>var secret = 1;")
        assert "secret" not in text
        assert "Plans" in text

    def test_block_elements_become_paragraph_breaks(self) -> None:
        # They are the only sentence boundaries a page of headings has, and
        # chunking splits on them.
        _, text = html_to_text("<h1>Plans</h1><p>Starter is 29</p><p>Teams is 49</p>")
        assert text == "Plans\n\nStarter is 29\n\nTeams is 49"

    def test_entities_are_decoded(self) -> None:
        _, text = html_to_text("<p>29 &amp; up &mdash; per seat &nbsp;monthly</p>")
        assert text == "29 & up — per seat monthly"

    def test_a_page_of_only_markup_is_empty_rather_than_an_error(self) -> None:
        document = extract(b"<html><head><script>x()</script></head></html>")
        assert document.empty is True


class TestMarkdown:
    DOCUMENT = (
        "# Handbook\n\n"
        "See [our pricing page](https://acme.example/pricing/plans) for details.\n\n"
        "## Plans\n\n"
        "The **Starter** plan is `29` per seat.\n\n"
        "```python\nprint('not prose')\n```\n"
    )

    def test_the_first_heading_is_the_title(self) -> None:
        document = extract(self.DOCUMENT.encode(), name="handbook.md")
        assert document.title == "Handbook"
        assert document.kind == "markdown"

    def test_the_markup_is_stripped_but_the_words_are_kept(self) -> None:
        # "**Starter**" and "Starter" must embed to the same place, and a
        # visitor reading the citation should not see asterisks.
        document = extract(self.DOCUMENT.encode(), name="handbook.md")
        assert "The Starter plan is 29 per seat." in document.text
        assert "*" not in document.text

    def test_a_heading_keeps_its_words(self) -> None:
        # Often the only statement of what the section below is about.
        document = extract(self.DOCUMENT.encode(), name="handbook.md")
        assert "Plans" in document.text
        assert "#" not in document.text

    def test_link_text_is_kept_and_the_url_is_dropped(self) -> None:
        # The text is prose; the URL is a run of slugs that drags the vector
        # somewhere unrelated.
        document = extract(self.DOCUMENT.encode(), name="handbook.md")
        assert "See our pricing page for details." in document.text
        assert "acme.example" not in document.text


class TestPdf:
    def test_the_text_comes_out(self) -> None:
        document = extract(minimal_pdf(), name="pricing.pdf")
        assert "Starter plan is 29 per seat" in document.text
        assert document.kind == "pdf"
        assert document.pages == 1

    def test_a_pdf_is_recognised_by_its_bytes_whatever_it_is_called(self) -> None:
        document = extract(minimal_pdf(), name="pricing")
        assert document.kind == "pdf"

    def test_a_scan_with_no_text_layer_is_empty_and_not_an_error(self) -> None:
        # The pipeline turns this into "needs OCR" rather than a stack trace,
        # which is the only useful thing to tell whoever uploaded it.
        document = extract(minimal_pdf(body=" "), name="scan.pdf")
        assert document.empty is True
        assert document.pages == 1

    def test_a_truncated_pdf_says_so(self) -> None:
        with pytest.raises(UnreadableDocument):
            extract(minimal_pdf()[:60], name="pricing.pdf")


class TestWhitespace:
    def test_paragraph_breaks_survive_and_runs_of_them_do_not(self) -> None:
        # Chunking splits on a blank line, so this decides where passages end.
        document = extract(b"Plans\n\n\n\n\nStarter is 29\n   \nTeams is 49", name="a.txt")
        assert document.text == "Plans\n\nStarter is 29\n\nTeams is 49"

    def test_a_blank_document_is_empty(self) -> None:
        assert extract(b"   \n\n  \t ", name="a.txt").empty is True
