"""
What a workspace has given its agent: uploads, crawls, and the list.

Three things here are the API's own business rather than the service's.

**The upload cap is enforced while reading.** The byte count is checked chunk
by chunk, so a two-gigabyte file is refused after a megabyte of it has
arrived. Checking the size after reading would mean buffering whatever was
sent in order to decide that it was too much, which is the shape of the
problem rather than a fix for it. ``Content-Length`` is not trusted for this:
a client sets it.

**A source id is generated, not accepted -- unless it is given deliberately.**
Replacing a document needs its id, so the field exists; it is pattern-checked
here, because the document store builds a directory name out of it and the
layer that refuses a traversal should not be the last one.

**The original downloads, it never renders.** These are bytes a customer
uploaded, re-served from the admin application's own origin. An HTML file
served inline there is stored cross-site scripting against the admin app, so
everything leaves as an attachment, typed as a byte stream, with sniffing
turned off. The original exists for diagnosis -- what the extractor was
actually given -- and a diagnosis does not need to run.

Known debt, and the honest version is: **a crawl runs inside the request.**
Forty pages at half a second apart is comfortably longer than a proxy will
wait, so a real deployment needs a job runner, a crawl id and a status to
poll. Until then the caps are the protection, the configured ceiling cannot
be raised by a request, and a caller that wants a crawl to finish inside a
request should ask for fewer pages.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import Field

from celeris_bridge.api.dependencies import App, Workspace, require_workspace
from celeris_bridge.api.errors import ApiError
from celeris_bridge.api.models import Payload, View
from celeris_bridge.domain.knowledge.crawl import CrawlLimits, CrawlReport
from celeris_bridge.domain.knowledge.ingestion import IngestionResult
from celeris_bridge.services.knowledge import SourceSummary

router = APIRouter(
    prefix="/v1/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(require_workspace)],
)

#: Largest upload accepted, before extraction. Generous for a document and
#: small enough that a hundred of them do not fill a disk; the character cap
#: in ``ingestion`` is the one that bounds the embedding bill.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
#: Read in chunks so the cap can be enforced on the way in.
_CHUNK = 64 * 1024

#: What a caller-chosen source id may look like. Stated here, in the layer
#: that accepts it, and deliberately the narrowest form any store can hold: an
#: id becomes a directory name on disk and a document key in Mongo. The local
#: store checks its own rule again rather than trusting this one -- the layer
#: that would be exploited is not the right place for the only check.
SOURCE_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
#: The same, or nothing. A browser form sends an empty string for a field
#: nobody filled in, and refusing that as a malformed id would make the
#: optional field mandatory.
_SOURCE_ID_OR_BLANK = r"^(|[A-Za-z0-9_-]{1,64})$"


class CrawlBody(Payload):
    start_url: str = Field(min_length=1, max_length=2000)
    #: Each of these may only *lower* the configured limit. A request that
    #: could raise it would make the limit a suggestion, and the thing being
    #: limited is how hard this service hits somebody else's web server.
    max_pages: int | None = Field(default=None, ge=1, le=500)
    max_depth: int | None = Field(default=None, ge=0, le=10)
    delay_seconds: float | None = Field(default=None, ge=0.0, le=30.0)
    #: Which agents the pages are for. Empty means the whole workspace.
    agent_instance_ids: list[str] = Field(default_factory=list, max_length=20)


class SourceView(View):
    source_id: str
    name: str
    kind: str
    bytes_stored: int
    passages: int
    embedding_model: str
    source_url: str | None
    approved: bool
    #: ``indexed`` | ``empty`` | ``pending``. A document that failed to parse
    #: looks exactly like one not processed yet, which is why this says which:
    #: ``empty`` means it was read and there was nothing in it -- a blank page,
    #: or a scan with no text layer, which needs OCR rather than a retry.
    status: str


class IngestedView(View):
    source_id: str
    source_name: str
    passages: int
    characters: int
    model: str
    kind: str
    indexed: bool
    #: Prose, when nothing was indexed and that is not an error. Shown to
    #: whoever uploaded it, so it says what to do about it.
    skipped: str = ""


class CrawledPageView(View):
    url: str
    depth: int
    title: str
    source_id: str
    passages: int
    #: Fetched, and identical to what is already indexed: nothing was
    #: embedded again. Most pages, most nights.
    unchanged: bool


class SkippedPageView(View):
    url: str
    #: Prose: this is shown to a person deciding whether it matters.
    reason: str


class CrawlReportView(View):
    start_url: str
    robots_present: bool
    #: Set when the crawl ran out of budget rather than out of links -- the
    #: difference between "that is the whole site" and "raise the cap".
    stopped_early: str
    indexed_pages: int
    passages: int
    pages: list[CrawledPageView]
    skipped: list[SkippedPageView]


class RobotsView(View):
    url: str
    #: Null when there is none, or it could not be read -- which allows
    #: everything, deliberately, for a site the customer owns.
    text: str | None


class DeletedView(View):
    source_id: str
    passages_removed: int


def _source(summary: SourceSummary) -> SourceView:
    return SourceView(
        source_id=summary.source_id,
        name=summary.name,
        kind=summary.kind,
        bytes_stored=summary.bytes_stored,
        passages=summary.passages,
        embedding_model=summary.embedding_model,
        source_url=summary.source_url,
        approved=summary.approved,
        status=summary.status,
    )


def _ingested(result: IngestionResult) -> IngestedView:
    return IngestedView(
        source_id=result.source_id,
        source_name=result.source_name,
        passages=result.passages,
        characters=result.characters,
        model=result.model,
        kind=result.kind,
        indexed=result.indexed,
        skipped=result.skipped,
    )


def _report(report: CrawlReport) -> CrawlReportView:
    return CrawlReportView(
        start_url=report.start_url,
        robots_present=report.robots_present,
        stopped_early=report.stopped_early,
        indexed_pages=report.indexed_pages,
        passages=report.passages,
        pages=[
            CrawledPageView(
                url=page.url,
                depth=page.depth,
                title=page.title,
                source_id=page.source_id,
                passages=page.passages,
                unchanged=page.unchanged,
            )
            for page in report.pages
        ],
        skipped=[
            SkippedPageView(url=one.url, reason=one.reason) for one in report.skipped
        ],
    )


async def _read_capped(upload: UploadFile) -> bytes:
    """Every byte, or a refusal before they have all arrived."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise ApiError(
                status=413,
                code="DOCUMENT_TOO_LARGE",
                message=f"A document may be at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _display_name(given: str, filename: str | None) -> str:
    """What a citation will show. Display text, never a path.

    The directory part of an uploaded file name is dropped here even though
    the store treats the name as text and never joins it onto a path: a name
    that reads ``../../etc/passwd`` in a citation is a bug report about
    security either way.
    """
    name = (given or filename or "document").strip()
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return name[:200] or "document"


@router.post("/sources", status_code=201)
async def upload_source(
    application: App,
    workspace_id: Workspace,
    file: Annotated[UploadFile, File()],
    # Aliased one by one, because a form field is not a model field and gets
    # no alias generator: without these the wire name would be snake_case
    # here and camelCase in every JSON body, and a client that sent
    # ``sourceId`` would have it silently ignored.
    name: Annotated[str, Form()] = "",
    source_id: Annotated[str, Form(alias="sourceId", pattern=_SOURCE_ID_OR_BLANK)] = "",
    approved: Annotated[bool, Form()] = False,
    agent_instance_ids: Annotated[list[str], Form(alias="agentInstanceIds")] = [],
) -> IngestedView:
    """Store a document and index it, replacing any earlier version of it.

    ``approved`` marks an official answer -- it outranks ordinary passages at
    retrieval -- so it is a field on the upload rather than something inferred
    from where the text came from.
    """
    content = await _read_capped(file)
    return _ingested(
        await application.knowledge.upload(
            workspace_id=workspace_id,
            # Generated when not given, because an id a client chooses is an
            # id a client can collide with somebody's existing document.
            source_id=source_id or f"up-{uuid.uuid4().hex[:20]}",
            name=_display_name(name, file.filename),
            content=content,
            content_type=file.content_type or "",
            approved=approved,
            agent_instance_ids=tuple(agent_instance_ids),
        )
    )


@router.get("/sources")
async def list_sources(application: App, workspace_id: Workspace) -> list[SourceView]:
    return [_source(one) for one in await application.knowledge.sources(workspace_id)]


@router.get("/sources/{source_id}")
async def get_source(
    application: App, workspace_id: Workspace, source_id: str
) -> SourceView:
    return _source(await application.knowledge.source(workspace_id, source_id))


@router.get("/sources/{source_id}/original")
async def download_original(
    application: App, workspace_id: Workspace, source_id: str
) -> Response:
    """The bytes as they were uploaded. Always a download.

    What makes a file that would not parse diagnosable: whoever is looking at
    it gets exactly what the extractor was given, not this service's idea of
    what it contained.
    """
    summary = await application.knowledge.source(workspace_id, source_id)
    content = await application.knowledge.original(workspace_id, source_id)
    return Response(
        content=content,
        # Never the stored content type: an uploaded HTML file served inline
        # from the admin origin is stored cross-site scripting, and the only
        # safe type for bytes a stranger chose is "bytes".
        media_type="application/octet-stream",
        headers={
            "content-disposition": f'attachment; filename="{_safe_filename(summary.name)}"',
            "x-content-type-options": "nosniff",
        },
    )


def _safe_filename(name: str) -> str:
    """A file name that cannot end the header it is written into."""
    cleaned = "".join(
        character for character in name if character.isprintable() and character not in '"\\'
    )
    return cleaned.strip() or "document"


@router.delete("/sources/{source_id}")
async def delete_source(
    application: App, workspace_id: Workspace, source_id: str
) -> DeletedView:
    """Remove a source and everything retrieved from it."""
    removed = await application.knowledge.delete(workspace_id, source_id)
    return DeletedView(source_id=source_id, passages_removed=removed)


@router.post("/reindex")
async def reindex(
    application: App,
    workspace_id: Workspace,
    limit: int = Query(default=0, ge=0, le=500),
) -> list[IngestedView]:
    """Re-embed whatever is not on the current embedder.

    The recovery path for a change of embedding provider, and the reason the
    originals are kept at all. ``limit`` exists so this can be done in
    batches: a thousand documents is a bill and a long request.
    """
    return [
        _ingested(one)
        for one in await application.knowledge.reindex(workspace_id, limit=limit)
    ]


@router.get("/robots")
async def robots(
    application: App, workspace_id: Workspace, url: str = Query(min_length=1)
) -> RobotsView:
    """A site's robots.txt, through the same guarded fetcher a crawl uses.

    Worth its own endpoint: "the crawler indexed four pages" is much easier to
    explain when the admin can see that the site's own rules disallow the rest.
    """
    return RobotsView(url=url, text=await application.knowledge.robots_for(url))


@router.post("/crawl")
async def crawl(
    application: App, workspace_id: Workspace, body: CrawlBody
) -> CrawlReportView:
    """Crawl a site into this workspace's knowledge.

    The URL is checked before anything is fetched, so an internal address is
    refused here rather than deep inside the loop.
    """
    configured = application.knowledge.limits
    limits = CrawlLimits(
        # ``min`` in both directions: a request may ask for less and never for
        # more. The delay is the reverse -- a longer wait is the polite
        # direction, so that one is a maximum.
        max_pages=min(body.max_pages or configured.max_pages, configured.max_pages),
        max_depth=min(body.max_depth if body.max_depth is not None else configured.max_depth, configured.max_depth),
        same_site_only=configured.same_site_only,
        delay_seconds=max(
            body.delay_seconds if body.delay_seconds is not None else configured.delay_seconds,
            configured.delay_seconds,
        ),
        max_bytes=configured.max_bytes,
        follow_robots=configured.follow_robots,
        user_agent=configured.user_agent,
    )
    return _report(
        await application.knowledge.crawl(
            workspace_id=workspace_id,
            start_url=body.start_url,
            limits=limits,
            agent_instance_ids=tuple(body.agent_instance_ids),
        )
    )
