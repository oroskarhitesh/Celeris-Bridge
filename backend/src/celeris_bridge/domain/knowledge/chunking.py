"""
Splitting a document into passages worth retrieving.

The unit of retrieval is the unit of citation, which settles most of the design
questions. A chunk has to be small enough that a visitor reading the citation
sees why it was quoted, and large enough to answer a question on its own.

Split on sentence boundaries, never mid-sentence: half a sentence retrieves
badly, embeds badly, and reads as a typo when shown as a source. Consecutive
chunks overlap by a sentence or two, so a fact stated across a boundary is
whole in at least one of them.

This module is pure. It knows nothing about embeddings or storage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Sequence

__all__ = [
    "DEFAULT_CHUNK_CHARACTERS",
    "DEFAULT_OVERLAP_SENTENCES",
    "Chunk",
    "chunk_text",
    "estimate_tokens",
    "split_sentences",
    "tidy",
]

#: About 250 words. Long enough to answer something, short enough to show.
DEFAULT_CHUNK_CHARACTERS = 1_400
#: Carried into the next chunk so a fact split across a boundary survives whole.
DEFAULT_OVERLAP_SENTENCES = 1
#: Below this a chunk is not worth an embedding call or a citation.
MIN_CHUNK_CHARACTERS = 40

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n{2,}")
_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Chunk:
    """One passage, with where it came from in the document."""

    text: str
    #: Position in the document, from 0. The only ordering a citation can use.
    index: int
    #: Character offset in the tidied document, for highlighting a source later.
    offset: int = 0

    @property
    def characters(self) -> int:
        return len(self.text)


def estimate_tokens(text: str) -> int:
    """Roughly four characters to a token.

    Good enough for sizing a prompt and refusing a document that will not fit.
    A real tokeniser is a dependency, a download and a per-model answer, for a
    number only ever compared against a limit with slack in it.
    """
    return (len(text) + 3) // 4


def tidy(text: str) -> str:
    """Normalise whitespace without losing paragraph structure.

    Paragraph breaks survive because they are the best sentence boundaries in
    the document — an extracted PDF often has no punctuation at a heading.
    """
    lines = [_WHITESPACE.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def split_sentences(text: str) -> list[str]:
    """Sentences, and paragraphs that do not end in punctuation."""
    return [part.strip() for part in _SENTENCE_END.split(text) if part.strip()]


def _packed(
    sentences: Sequence[str], limit: int, overlap: int
) -> Iterator[list[str]]:
    """Group sentences into runs no longer than ``limit``, overlapping."""
    current: list[str] = []
    length = 0
    for sentence in sentences:
        # A single sentence longer than the limit still goes out whole: cutting
        # it would produce the fragment this module exists to avoid.
        if current and length + len(sentence) + 1 > limit:
            yield current
            current = current[-overlap:] if overlap else []
            length = sum(len(part) + 1 for part in current)
        current.append(sentence)
        length += len(sentence) + 1
    if current:
        yield current


def chunk_text(
    text: str,
    *,
    characters: int = DEFAULT_CHUNK_CHARACTERS,
    overlap_sentences: int = DEFAULT_OVERLAP_SENTENCES,
) -> tuple[Chunk, ...]:
    """
    Split a document into chunks.

    Returns nothing for a document with nothing in it — a blank page uploaded
    by mistake must not become a passage that can be retrieved and cited.
    """
    tidied = tidy(text)
    if not tidied:
        return ()

    sentences = split_sentences(tidied)
    if not sentences:
        return ()

    chunks: list[Chunk] = []
    searched_from = 0
    for group in _packed(sentences, max(characters, MIN_CHUNK_CHARACTERS), max(overlap_sentences, 0)):
        body = " ".join(group)
        if len(body) < MIN_CHUNK_CHARACTERS and chunks:
            # Tail too short to stand alone: it is already in the previous
            # chunk's overlap, or it is a page number.
            continue
        found = tidied.find(group[0], searched_from)
        offset = found if found >= 0 else searched_from
        searched_from = offset + 1
        chunks.append(Chunk(text=body, index=len(chunks), offset=offset))
    return tuple(chunks)
