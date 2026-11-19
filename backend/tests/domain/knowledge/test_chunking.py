"""
Tests for chunking.

The unit of retrieval is the unit of citation, so a chunk has to be something a
visitor can be shown. That rules out fragments, and it is most of the design.
"""

from __future__ import annotations

import pytest

from celeris_bridge.domain.knowledge.chunking import (
    DEFAULT_CHUNK_CHARACTERS,
    chunk_text,
    estimate_tokens,
    split_sentences,
    tidy,
)

PARAGRAPH = (
    "Celeris Bridge gives support teams one inbox. It handles chat, email and voice. "
    "Plans start at 29 per seat per month. Annual billing saves two months."
)


class TestTidying:
    def test_runs_of_spaces_become_one(self) -> None:
        assert tidy("one    two\t\tthree") == "one two three"

    def test_paragraph_breaks_survive(self) -> None:
        # They are the best sentence boundaries in the document: an extracted
        # PDF often has no punctuation at a heading.
        assert tidy("Heading\n\nBody text") == "Heading\n\nBody text"

    def test_a_run_of_blank_lines_becomes_one_break(self) -> None:
        assert tidy("Heading\n\n\n\n\nBody") == "Heading\n\nBody"

    def test_windows_line_endings_are_handled(self) -> None:
        assert tidy("one\r\ntwo") == "one\ntwo"

    def test_leading_and_trailing_whitespace_goes(self) -> None:
        assert tidy("\n\n  text  \n\n") == "text"


class TestSplittingSentences:
    def test_sentences_split_on_their_punctuation(self) -> None:
        assert split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]

    def test_a_paragraph_without_punctuation_is_still_a_unit(self) -> None:
        assert split_sentences("Pricing\n\nIt costs 29") == ["Pricing", "It costs 29"]

    def test_a_single_line_break_does_not_split(self) -> None:
        # A wrapped line is not a sentence boundary.
        assert split_sentences("one\ntwo") == ["one\ntwo"]

    def test_nothing_in_means_nothing_out(self) -> None:
        assert split_sentences("   ") == []


class TestEstimatingTokens:
    def test_roughly_four_characters_to_a_token(self) -> None:
        assert estimate_tokens("x" * 400) == 100

    def test_it_never_rounds_down_to_zero(self) -> None:
        assert estimate_tokens("x") == 1
        assert estimate_tokens("") == 0


class TestChunking:
    def test_a_short_document_is_one_chunk(self) -> None:
        chunks = chunk_text(PARAGRAPH)
        assert len(chunks) == 1
        assert chunks[0].text == PARAGRAPH
        assert chunks[0].index == 0

    def test_an_empty_document_produces_nothing(self) -> None:
        # A blank page uploaded by mistake must not become a passage that can
        # be retrieved and cited.
        assert chunk_text("") == ()
        assert chunk_text("   \n\n  ") == ()

    def test_a_long_document_is_split(self) -> None:
        text = " ".join(f"Sentence number {n} says something useful." for n in range(200))
        chunks = chunk_text(text, characters=400)
        assert len(chunks) > 5
        assert [chunk.index for chunk in chunks] == list(range(len(chunks)))

    def test_no_chunk_is_much_over_the_limit(self) -> None:
        text = " ".join(f"Sentence number {n} says something useful." for n in range(200))
        for chunk in chunk_text(text, characters=400):
            assert chunk.characters <= 400 + 60

    def test_chunks_never_break_mid_sentence(self) -> None:
        # Half a sentence retrieves badly, embeds badly, and reads as a typo
        # when it is shown to a visitor as a source.
        sentences = [f"Sentence number {n} says something useful." for n in range(60)]
        for chunk in chunk_text(" ".join(sentences), characters=200):
            for part in split_sentences(chunk.text):
                assert part in sentences

    def test_consecutive_chunks_overlap(self) -> None:
        # So a fact stated across a boundary is whole in at least one chunk.
        sentences = [f"Fact {n} is that the number is {n}." for n in range(40)]
        chunks = chunk_text(" ".join(sentences), characters=200, overlap_sentences=1)
        first, second = chunks[0], chunks[1]
        tail = split_sentences(first.text)[-1]
        assert tail in second.text

    def test_the_overlap_can_be_turned_off(self) -> None:
        sentences = [f"Fact {n} is that the number is {n}." for n in range(40)]
        chunks = chunk_text(" ".join(sentences), characters=200, overlap_sentences=0)
        tail = split_sentences(chunks[0].text)[-1]
        assert tail not in chunks[1].text

    def test_a_sentence_longer_than_the_limit_is_kept_whole(self) -> None:
        # Cutting it would produce exactly the fragment this module exists to
        # avoid, so the limit yields instead.
        giant = "word " * 500
        chunks = chunk_text(giant.strip(), characters=200)
        assert len(chunks) == 1
        assert chunks[0].characters > 200

    def test_a_scrap_at_the_end_is_not_its_own_chunk(self) -> None:
        # It is already in the previous chunk's overlap, or it is a page number.
        text = " ".join(f"Sentence number {n} is here." for n in range(20)) + "\n\n7"
        chunks = chunk_text(text, characters=300)
        assert all(chunk.characters >= 40 for chunk in chunks)
        assert not any(chunk.text == "7" for chunk in chunks)

    def test_a_chunk_knows_where_it_came_from(self) -> None:
        text = " ".join(f"Sentence number {n} says something useful." for n in range(60))
        chunks = chunk_text(text, characters=200)
        offsets = [chunk.offset for chunk in chunks]
        assert offsets == sorted(offsets)
        assert chunks[0].offset == 0

    def test_the_default_limit_is_a_citable_size(self) -> None:
        # Small enough that a visitor reading the citation sees why it was
        # quoted; large enough to answer something on its own.
        assert 800 <= DEFAULT_CHUNK_CHARACTERS <= 2_000

    @pytest.mark.parametrize("characters", [1, 10, 39])
    def test_an_absurdly_small_limit_does_not_produce_fragments(
        self, characters: int
    ) -> None:
        chunks = chunk_text(PARAGRAPH, characters=characters)
        assert chunks
        for chunk in chunks:
            assert chunk.text.strip() == chunk.text
