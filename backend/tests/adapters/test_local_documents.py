"""
Tests for the document store on disk, and the in-memory one beside it.

One suite, both stores, because they are one contract — the pipeline is written
against the port and a difference between the two is a bug that only shows up
in whichever environment runs the other one.

The disk store gets extra tests of its own for the part that has no in-memory
equivalent: identifiers become directory names, and an identifier that escapes
the root reads or overwrites the rest of the machine.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from celeris_bridge.adapters.in_memory import InMemoryDocumentStore
from celeris_bridge.adapters.local_documents import (
    CONTENT_FILE,
    RECORD_FILE,
    LocalDocumentStore,
    UnsafeIdentifier,
)
from celeris_bridge.ports.documents import DocumentStore, MissingDocument


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


@pytest.fixture(params=["memory", "disk"])
def store(request: Any, tmp_path: Path) -> DocumentStore:
    if request.param == "memory":
        return InMemoryDocumentStore()
    return LocalDocumentStore(tmp_path / "documents")


def put(store: DocumentStore, **changed: Any) -> Any:
    fields: dict[str, Any] = {
        "workspace_id": "w1",
        "source_id": "s1",
        "name": "handbook.pdf",
        "content": b"Plans start at 29 per seat.",
        "content_type": "text/plain",
    }
    fields.update(changed)
    return run(store.put(**fields))


class TestTheContract:
    def test_both_stores_satisfy_the_port(self, store: DocumentStore) -> None:
        assert isinstance(store, DocumentStore)

    def test_what_went_in_comes_out(self, store: DocumentStore) -> None:
        put(store)
        assert run(store.content(workspace_id="w1", source_id="s1")) == (
            b"Plans start at 29 per seat."
        )

    def test_the_record_describes_it_without_reading_it(self, store: DocumentStore) -> None:
        put(store, source_url="https://acme.example/plans", approved=True)
        found = run(store.describe(workspace_id="w1", source_id="s1"))
        assert found.name == "handbook.pdf"
        assert found.content_type == "text/plain"
        assert found.bytes_stored == 27
        assert found.source_url == "https://acme.example/plans"
        assert found.approved is True

    def test_the_checksum_is_of_the_content(self, store: DocumentStore) -> None:
        # What lets the crawler skip a page it has already indexed unchanged,
        # which is most pages on most nights.
        same = put(store, source_id="a")
        again = put(store, source_id="b")
        different = put(store, source_id="c", content=b"Something else.")
        assert same.checksum == again.checksum
        assert same.checksum != different.checksum

    def test_the_agent_binding_survives_a_round_trip(self, store: DocumentStore) -> None:
        # Carried so a re-index cannot widen one agent's document to the
        # whole workspace.
        put(store, agent_instance_ids=["a1", "a2"])
        found = run(store.describe(workspace_id="w1", source_id="s1"))
        assert found.agent_instance_ids == ("a1", "a2")

    def test_putting_it_again_replaces_it(self, store: DocumentStore) -> None:
        put(store)
        put(store, content=b"A newer handbook.", name="handbook-v2.pdf")
        assert run(store.content(workspace_id="w1", source_id="s1")) == b"A newer handbook."
        assert run(store.describe(workspace_id="w1", source_id="s1")).name == "handbook-v2.pdf"

    def test_a_fresh_document_is_not_indexed_yet(self, store: DocumentStore) -> None:
        assert put(store).indexed_model == ""

    def test_being_indexed_is_recorded(self, store: DocumentStore) -> None:
        put(store)
        run(store.mark_indexed(workspace_id="w1", source_id="s1", model="voyage-3:1024", passages=7))
        found = run(store.describe(workspace_id="w1", source_id="s1"))
        assert (found.indexed_model, found.indexed_passages) == ("voyage-3:1024", 7)

    def test_new_bytes_clear_what_was_indexed(self, store: DocumentStore) -> None:
        # The passages in the index are about to be wrong, so nothing may
        # report them as current -- which is also what puts the document back
        # on the re-index work list.
        put(store)
        run(store.mark_indexed(workspace_id="w1", source_id="s1", model="voyage-3:1024", passages=7))
        put(store, content=b"A newer handbook.")
        assert run(store.describe(workspace_id="w1", source_id="s1")).indexed_model == ""

    def test_marking_something_absent_says_so(self, store: DocumentStore) -> None:
        with pytest.raises(MissingDocument):
            run(store.mark_indexed(workspace_id="w1", source_id="nope", model="m", passages=1))

    def test_missing_content_is_not_an_empty_document(self, store: DocumentStore) -> None:
        # Returning b"" would be indexed as a blank document, and the real
        # passages would be deleted in the process.
        with pytest.raises(MissingDocument):
            run(store.content(workspace_id="w1", source_id="never"))

    def test_describing_something_absent_is_none(self, store: DocumentStore) -> None:
        assert run(store.describe(workspace_id="w1", source_id="never")) is None


class TestListing:
    def test_a_workspace_sees_its_own_documents(self, store: DocumentStore) -> None:
        put(store, source_id="s1")
        put(store, source_id="s2")
        assert [one.source_id for one in run(store.list(workspace_id="w1"))] == ["s1", "s2"]

    def test_and_only_its_own(self, store: DocumentStore) -> None:
        put(store, workspace_id="w1", source_id="s1")
        put(store, workspace_id="w2", source_id="s2")
        assert [one.source_id for one in run(store.list(workspace_id="w1"))] == ["s1"]

    def test_an_unknown_workspace_is_empty_and_not_an_error(self, store: DocumentStore) -> None:
        assert run(store.list(workspace_id="w9")) == ()

    def test_stale_is_everything_not_on_this_model(self, store: DocumentStore) -> None:
        put(store, source_id="old")
        put(store, source_id="current")
        run(store.mark_indexed(workspace_id="w1", source_id="old", model="hashing-dev:256", passages=2))
        run(store.mark_indexed(workspace_id="w1", source_id="current", model="voyage-3:1024", passages=2))
        stale = run(store.stale(workspace_id="w1", model="voyage-3:1024"))
        assert [one.source_id for one in stale] == ["old"]

    def test_never_indexed_counts_as_stale(self, store: DocumentStore) -> None:
        # From the caller's side it is the same job: work the index is missing.
        put(store, source_id="pending")
        stale = run(store.stale(workspace_id="w1", model="voyage-3:1024"))
        assert [one.source_id for one in stale] == ["pending"]


class TestDeleting:
    def test_it_goes(self, store: DocumentStore) -> None:
        put(store)
        assert run(store.delete(workspace_id="w1", source_id="s1")) is True
        assert run(store.describe(workspace_id="w1", source_id="s1")) is None
        with pytest.raises(MissingDocument):
            run(store.content(workspace_id="w1", source_id="s1"))

    def test_deleting_twice_is_not_an_error(self, store: DocumentStore) -> None:
        put(store)
        run(store.delete(workspace_id="w1", source_id="s1"))
        assert run(store.delete(workspace_id="w1", source_id="s1")) is False

    def test_one_workspace_cannot_delete_anothers(self, store: DocumentStore) -> None:
        put(store, workspace_id="w1", source_id="s1")
        assert run(store.delete(workspace_id="w2", source_id="s1")) is False
        assert run(store.describe(workspace_id="w1", source_id="s1")) is not None


class TestIdentifiersAreNotPaths:
    """The disk store's own risk: these become directory names."""

    @pytest.mark.parametrize(
        "bad",
        [
            "../../../etc",
            "..",
            "w1/../w2",
            "w1/s1",
            "w1\\s1",
            "C:/Windows",
            "",
            "w" * 65,
            "a b",
            "w1\x00",
            ".",
        ],
    )
    def test_an_identifier_that_could_escape_the_root_is_refused(
        self, tmp_path: Path, bad: str
    ) -> None:
        store = LocalDocumentStore(tmp_path)
        with pytest.raises(UnsafeIdentifier):
            run(store.put(workspace_id=bad, source_id="s1", name="x.txt", content=b"x"))
        with pytest.raises(UnsafeIdentifier):
            run(store.put(workspace_id="w1", source_id=bad, name="x.txt", content=b"x"))

    def test_the_check_is_on_every_way_in(self, tmp_path: Path) -> None:
        # One unchecked method is the whole guard gone.
        store = LocalDocumentStore(tmp_path)
        for call in (
            lambda: store.content(workspace_id="../x", source_id="s1"),
            lambda: store.describe(workspace_id="../x", source_id="s1"),
            lambda: store.list(workspace_id="../x"),
            lambda: store.stale(workspace_id="../x", model="m"),
            lambda: store.mark_indexed(workspace_id="../x", source_id="s1", model="m", passages=1),
            lambda: store.delete(workspace_id="../x", source_id="s1"),
        ):
            with pytest.raises(UnsafeIdentifier):
                run(call())

    def test_a_dangerous_file_name_is_harmless_because_it_is_not_a_path(
        self, tmp_path: Path
    ) -> None:
        # The name is the one field a visitor or an uploader chose. It is
        # display text: it is stored, shown as a citation, and never joined
        # onto a directory.
        store = LocalDocumentStore(tmp_path)
        put(store, name="../../../../etc/passwd")
        found = run(store.describe(workspace_id="w1", source_id="s1"))
        assert found.name == "../../../../etc/passwd"
        assert (tmp_path / "w1" / "s1" / CONTENT_FILE).is_file()
        assert sorted(child.name for child in tmp_path.iterdir()) == ["w1"]


class TestOnDisk:
    def test_the_layout_is_one_directory_per_document(self, tmp_path: Path) -> None:
        store = LocalDocumentStore(tmp_path)
        put(store)
        directory = tmp_path / "w1" / "s1"
        assert sorted(child.name for child in directory.iterdir()) == [
            CONTENT_FILE,
            RECORD_FILE,
        ]

    def test_nothing_half_written_is_left_behind(self, tmp_path: Path) -> None:
        # Written to a neighbour and moved into place, so a crash or a full
        # disk cannot leave a partial document where a whole one is expected.
        store = LocalDocumentStore(tmp_path)
        put(store)
        assert not list((tmp_path / "w1" / "s1").glob("*.part"))

    def test_another_process_can_read_what_this_one_wrote(self, tmp_path: Path) -> None:
        # Two workers behind a load balancer share the directory, so the
        # store must hold no state the files do not.
        put(LocalDocumentStore(tmp_path))
        other = LocalDocumentStore(tmp_path)
        assert run(other.describe(workspace_id="w1", source_id="s1")).name == "handbook.pdf"

    def test_a_corrupt_record_reads_as_absent(self, tmp_path: Path) -> None:
        # Which makes the document get ingested again -- the recoverable
        # outcome. Raising would make one bad file break the whole list.
        store = LocalDocumentStore(tmp_path)
        put(store)
        (tmp_path / "w1" / "s1" / RECORD_FILE).write_text("{ not json", encoding="utf-8")
        assert run(store.describe(workspace_id="w1", source_id="s1")) is None
        assert run(store.list(workspace_id="w1")) == ()

    def test_bytes_are_stored_unchanged(self, tmp_path: Path) -> None:
        # A PDF is not text and must not be decoded, re-encoded, or have its
        # line endings helpfully fixed on the way through.
        store = LocalDocumentStore(tmp_path)
        content = b"%PDF-1.4\r\n\x00\x80\xff binary \r\n"
        put(store, content=content)
        assert run(store.content(workspace_id="w1", source_id="s1")) == content
