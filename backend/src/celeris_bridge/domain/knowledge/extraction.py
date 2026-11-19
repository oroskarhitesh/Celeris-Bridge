"""
Turning an uploaded file into text worth chunking.

This is the one place in ingestion that knows file formats exist. Everything
after it -- chunking, embedding, indexing -- works on a string, which is why a
new format is a function here and nothing else changes.

Two decisions shape the module.

**The format is decided by looking, not by trusting.** A browser's
``content_type`` is often ``application/octet-stream``, an extension is whatever
the uploader typed, and both are attacker-controlled on a public upload form.
So the bytes themselves get the last word: a file claiming to be ``.txt`` that
begins ``%PDF-`` is read as a PDF, because that is what it is.

**Extraction never raises on bad input it can partially read.** A scanned PDF
with no text layer, a mostly-corrupt HTML page, a file in an encoding nobody
has seen since 1998: these produce whatever text is there, possibly none, and
the pipeline reports "nothing to index". The only failures are a format this
build cannot read at all and a document that is locked.

This module is pure apart from one lazy import of ``pypdf``.
"""

from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass

__all__ = [
    "DocumentText",
    "UnreadableDocument",
    "UnsupportedDocument",
    "detect_kind",
    "extract",
    "html_to_text",
]


class UnsupportedDocument(ValueError):
    """A format this build cannot read."""


class UnreadableDocument(ValueError):
    """A format it can read, in a file it cannot -- encrypted, or truncated."""


@dataclass(frozen=True)
class DocumentText:
    """What came out of a file."""

    text: str
    #: A title the document named for itself -- an HTML ``<title>``, a PDF's
    #: metadata, a Markdown heading. Offered to the caller as a better citation
    #: than ``page.html``, never forced on it.
    title: str = ""
    #: ``text`` | ``markdown`` | ``html`` | ``pdf``. Recorded so a surprising
    #: result can be traced to the branch that produced it.
    kind: str = "text"
    #: Pages, for a PDF. Zero for everything else.
    pages: int = 0

    @property
    def empty(self) -> bool:
        return not self.text.strip()


_PDF_MAGIC = b"%PDF-"
_HTML_HINT = re.compile(rb"<\s*(!doctype\s+html|html|head|body|div|p)\b", re.IGNORECASE)

_TEXT_EXTENSIONS = {".txt", ".text", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml"}
_MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdx"}
_HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}


def detect_kind(*, name: str = "", content_type: str = "", content: bytes = b"") -> str:
    """Which reader this file needs.

    The bytes win, then the declared type, then the extension. That order is
    the whole point: the first is a fact and the other two are claims.
    """
    if content.startswith(_PDF_MAGIC):
        return "pdf"

    declared = (content_type or "").split(";")[0].strip().lower()
    if declared == "application/pdf":
        # Claimed but the magic is absent. Say so rather than feeding a
        # spreadsheet to the PDF reader and reporting its complaint.
        raise UnreadableDocument("Declared as a PDF but does not begin with %PDF-")
    if declared in {"text/html", "application/xhtml+xml"}:
        return "html"
    if declared == "text/markdown":
        return "markdown"
    if declared.startswith("text/") or declared == "application/json":
        return "text"

    suffix = _suffix(name)
    if suffix in _HTML_EXTENSIONS:
        return "html"
    if suffix in _MARKDOWN_EXTENSIONS:
        return "markdown"
    if suffix in _TEXT_EXTENSIONS:
        return "text"
    if suffix == ".pdf":
        raise UnreadableDocument("Named as a PDF but does not begin with %PDF-")

    # Nothing said what it is. A document with HTML tags in the first couple
    # of kilobytes is a web page whatever it is called.
    if _HTML_HINT.search(content[:2_000]):
        return "html"
    if suffix:
        raise UnsupportedDocument(
            f"Cannot read {suffix} files. Supported: plain text, Markdown, "
            "HTML and PDF."
        )
    return "text"


def _suffix(name: str) -> str:
    # Deliberately not pathlib: ``name`` is display text that may contain
    # anything, and asking a path library about it invites it to be treated as
    # a path somewhere further down the line.
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    dot = base.rfind(".")
    return base[dot:].lower() if dot > 0 else ""


def extract(content: bytes, *, name: str = "", content_type: str = "") -> DocumentText:
    """Read a file. See :func:`detect_kind` for how the format is decided."""
    kind = detect_kind(name=name, content_type=content_type, content=content)
    if kind == "pdf":
        return _from_pdf(content)
    if kind == "html":
        title, text = html_to_text(_decode(content))
        return DocumentText(text=text, title=title, kind="html")
    if kind == "markdown":
        title, text = _from_markdown(_decode(content))
        return DocumentText(text=text, title=title, kind="markdown")
    return DocumentText(text=_collapse_document(_decode(content)), kind="text")


_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)


def _decode(content: bytes) -> str:
    """Bytes to text, giving up on fidelity before giving up on the document.

    A byte-order mark is believed, then UTF-8, then Windows-1252 -- which
    decodes anything and is what a file exported from Word actually is. The
    last resort replaces bad bytes rather than failing: one mangled quotation
    mark is a better outcome than a customer's handbook being rejected.
    """
    for marker, encoding in _BOMS:
        if content.startswith(marker):
            try:
                return content.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                break
    for encoding in ("utf-8", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


_SCRIPTY = re.compile(
    r"<\s*(script|style|noscript|template|svg|iframe)\b.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_UNCLOSED_SCRIPTY = re.compile(r"<\s*(script|style)\b.*", re.IGNORECASE | re.DOTALL)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TITLE = re.compile(r"<\s*title[^>]*>(.*?)<\s*/\s*title\s*>", re.IGNORECASE | re.DOTALL)
_BREAK = re.compile(
    r"<\s*/?\s*(p|div|section|article|br|li|tr|h[1-6]|blockquote|pre|td|th)\b[^>]*>",
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]*>")


def html_to_text(markup: str) -> tuple[str, str]:
    """A page's title and its readable text.

    Regular expressions rather than a parser, and that is a real limit worth
    naming: this cannot tell a navigation menu from an article, so a crawled
    page arrives with its menu in it. The alternative is a parsing dependency
    and a readability heuristic, and chunking already drops the short
    fragments a menu becomes. If retrieval starts citing "Home About Contact",
    that is the thing to revisit.
    """
    found = _TITLE.search(markup)
    title = _collapse(html.unescape(_TAG.sub(" ", found.group(1)))) if found else ""

    body = _COMMENT.sub(" ", markup)
    body = _SCRIPTY.sub(" ", body)
    # A truncated page can leave a <script> open; without this its source code
    # becomes the document.
    body = _UNCLOSED_SCRIPTY.sub(" ", body)
    # Block elements become paragraph breaks before the tags go, because they
    # are the only sentence boundaries a page without punctuation has.
    body = _BREAK.sub("\n\n", body)
    body = _TAG.sub(" ", body)
    return title, _collapse_document(html.unescape(body))


_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_FENCE = re.compile("^\\s*" + "`" * 3 + ".*$", re.MULTILINE)
_MARKUP = re.compile("[*_`~]{1,3}")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$", re.MULTILINE)


def _from_markdown(text: str) -> tuple[str, str]:
    """Markdown with its punctuation removed, and its first heading as a title.

    The markup is stripped because it is noise to an embedder: a word in bold
    and the same word plain must land in the same place, and a visitor shown
    the citation should not be reading asterisks. Link *text* is kept and the
    URL dropped -- the text is prose, the URL is a string of slugs that drags
    the vector somewhere unrelated.
    """
    first = _HEADING.search(text)
    title = _collapse(_MARKUP.sub("", first.group(2))) if first else ""

    body = _FENCE.sub("", text)
    body = _LINK.sub(r"\1", body)
    # Headings keep their words and lose their hashes: a heading is often the
    # only statement of what the section below it is about.
    body = _HEADING.sub(r"\2", body)
    body = _MARKUP.sub("", body)
    return title, _collapse_document(body)


_SPACES = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")


def _collapse(text: str) -> str:
    """One line, for a title."""
    flattened = text.replace("\r\n", "\n").replace("\n", " ")
    return _SPACES.sub(" ", flattened).strip()


def _collapse_document(text: str) -> str:
    """Tidy whitespace, keeping the paragraph breaks chunking splits on."""
    lines = [
        _SPACES.sub(" ", line).strip()
        for line in text.replace("\r\n", "\n").split("\n")
    ]
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip()


def _from_pdf(content: bytes) -> DocumentText:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as missing:  # pragma: no cover - install-dependent
        raise UnsupportedDocument(
            "Reading PDFs needs the pypdf package; install it, or upload the "
            "text instead."
        ) from missing

    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            # The empty password covers a PDF that is "encrypted" only to
            # forbid printing, which is most of the encrypted ones.
            try:
                reader.decrypt("")
            except Exception as refused:
                raise UnreadableDocument("This PDF is password protected.") from refused
        pages = [(page.extract_text() or "") for page in reader.pages]
    except UnreadableDocument:
        raise
    except Exception as broken:
        raise UnreadableDocument(f"Could not read this PDF: {broken}") from broken

    # A blank line between pages, so chunking treats a page break as a
    # paragraph break -- in a PDF it usually is one, and a heading at the top
    # of a page rarely ends in a full stop.
    body = _collapse_document("\n\n".join(pages))
    try:
        title = _collapse(str((reader.metadata or {}).get("/Title") or ""))
    except Exception:
        # Metadata is decoration. A PDF with a broken info dictionary still
        # has its text, which is the thing that was asked for.
        title = ""
    return DocumentText(text=body, title=title, kind="pdf", pages=len(pages))
