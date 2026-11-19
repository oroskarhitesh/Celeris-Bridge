"""
The document store, on local disk.

One directory per document, holding the bytes and a small JSON record beside
them::

    <root>/<workspace_id>/<source_id>/content
    <root>/<workspace_id>/<source_id>/record.json

Files rather than the database because a knowledge base is mostly large blobs
that are written once and read rarely, and that is the one thing a filesystem
is better at than Mongo. The reference project does the same, which also makes
this the adapter that could read its storage directory if that is ever wanted.

**Identifiers are not paths.** ``workspace_id`` and ``source_id`` are checked
against a strict pattern before they touch the filesystem, and the document's
*name* -- the only part a visitor or an uploader chose -- is never used to
build a path at all. A store that joins user input onto a root directory is one
``..`` away from reading the rest of the disk, and the check is one line.

Every call hands the blocking work to a thread: an upload is megabytes, the
server is async, and the event loop must not stop while a disk writes.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Sequence

from celeris_bridge.domain.knowledge.ingestion import checksum
from celeris_bridge.ports.documents import MissingDocument, StoredDocument

__all__ = ["LocalDocumentStore", "UnsafeIdentifier"]

#: What an identifier may be. No dots, so no traversal; no separators, so no
#: escaping the root; bounded, so no filesystem name-length surprises.
_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

CONTENT_FILE = "content"
RECORD_FILE = "record.json"


class UnsafeIdentifier(ValueError):
    """An identifier that must not be turned into a path."""


def _safe(label: str, value: str) -> str:
    if not _SAFE.match(value or ""):
        # The value is in the message because this is a programming error or
        # an attack, and in both cases the thing to see is what was sent.
        raise UnsafeIdentifier(
            f"{label} {value!r} is not a safe identifier: letters, digits, "
            "hyphen and underscore only, up to 64 characters."
        )
    return value


class LocalDocumentStore:
    """Documents in a directory tree."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    def _directory(self, workspace_id: str, source_id: str) -> Path:
        return (
            self._root
            / _safe("workspace", workspace_id)
            / _safe("source", source_id)
        )

    async def put(
        self,
        *,
        workspace_id: str,
        source_id: str,
        name: str,
        content: bytes,
        content_type: str = "",
        source_url: str | None = None,
        approved: bool = False,
        agent_instance_ids: Sequence[str] = (),
        etag: str = "",
    ) -> StoredDocument:
        document = StoredDocument(
            workspace_id=workspace_id,
            source_id=source_id,
            name=name,
            content_type=content_type,
            bytes_stored=len(content),
            source_url=source_url,
            checksum=checksum(content),
            etag=etag,
            approved=approved,
            agent_instance_ids=tuple(str(one) for one in agent_instance_ids),
            # Deliberately left empty: these bytes are not in the index yet,
            # and until they are nothing may report that they are.
            indexed_model="",
            indexed_passages=0,
        )
        directory = self._directory(workspace_id, source_id)
        await asyncio.to_thread(_write, directory, content, document)
        return document

    async def content(self, *, workspace_id: str, source_id: str) -> bytes:
        path = self._directory(workspace_id, source_id) / CONTENT_FILE
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as missing:
            raise MissingDocument(
                f"No stored document {source_id!r} in workspace {workspace_id!r}"
            ) from missing

    async def describe(
        self, *, workspace_id: str, source_id: str
    ) -> StoredDocument | None:
        path = self._directory(workspace_id, source_id) / RECORD_FILE
        return await asyncio.to_thread(_read_record, path)

    async def list(self, *, workspace_id: str) -> Sequence[StoredDocument]:
        directory = self._root / _safe("workspace", workspace_id)
        return await asyncio.to_thread(_read_all, directory)

    async def stale(
        self, *, workspace_id: str, model: str
    ) -> Sequence[StoredDocument]:
        documents = await self.list(workspace_id=workspace_id)
        # A document never indexed is stale too: it is work the index is
        # missing, which is the same job from the caller's side.
        return tuple(
            document for document in documents if document.indexed_model != model
        )

    async def mark_indexed(
        self, *, workspace_id: str, source_id: str, model: str, passages: int
    ) -> None:
        directory = self._directory(workspace_id, source_id)
        await asyncio.to_thread(_mark, directory, model, passages)

    async def delete(self, *, workspace_id: str, source_id: str) -> bool:
        directory = self._directory(workspace_id, source_id)
        return await asyncio.to_thread(_delete, directory)


def _write(directory: Path, content: bytes, document: StoredDocument) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    # Written to a neighbour and moved into place, so a crash or a full disk
    # cannot leave half a document where a whole one is expected. The record
    # goes second: content with no record is a document to recover, a record
    # with no content is one that lies about existing.
    content_path = directory / CONTENT_FILE
    temporary = directory / (CONTENT_FILE + ".part")
    temporary.write_bytes(content)
    temporary.replace(content_path)
    _write_record(directory, document)


def _write_record(directory: Path, document: StoredDocument) -> None:
    path = directory / RECORD_FILE
    temporary = directory / (RECORD_FILE + ".part")
    temporary.write_text(
        json.dumps(asdict(document), indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)


def _read_record(path: Path) -> StoredDocument | None:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        # A truncated record. Reporting it as absent means the document gets
        # ingested again, which rewrites the record -- the recoverable
        # outcome. Raising here would make one bad file break the admin list.
        return None
    if not isinstance(raw, dict):
        return None
    return StoredDocument(
        workspace_id=str(raw.get("workspace_id") or ""),
        source_id=str(raw.get("source_id") or ""),
        name=str(raw.get("name") or ""),
        content_type=str(raw.get("content_type") or ""),
        bytes_stored=int(raw.get("bytes_stored") or 0),
        source_url=raw.get("source_url") or None,
        checksum=str(raw.get("checksum") or ""),
        etag=str(raw.get("etag") or ""),
        approved=bool(raw.get("approved")),
        agent_instance_ids=tuple(str(one) for one in raw.get("agent_instance_ids") or ()),
        indexed_model=str(raw.get("indexed_model") or ""),
        indexed_passages=int(raw.get("indexed_passages") or 0),
    )


def _read_all(directory: Path) -> tuple[StoredDocument, ...]:
    if not directory.is_dir():
        return ()
    found = [
        document
        for child in sorted(directory.iterdir())
        if child.is_dir()
        for document in (_read_record(child / RECORD_FILE),)
        if document is not None
    ]
    return tuple(found)


def _mark(directory: Path, model: str, passages: int) -> None:
    document = _read_record(directory / RECORD_FILE)
    if document is None:
        raise MissingDocument(f"No stored document at {directory}")
    _write_record(
        directory, replace(document, indexed_model=model, indexed_passages=passages)
    )


def _delete(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    shutil.rmtree(directory)
    return True
