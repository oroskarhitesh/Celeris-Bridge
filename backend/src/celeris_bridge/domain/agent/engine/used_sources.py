"""
Which sources a reply actually used.

An agent that searches six passages and then shows six citations is lying by
implication: the visitor reads it as "this answer came from these six things".
A citation should appear when the reply and the passage share enough real
words that the answer plainly came from it.

Word overlap, not a model call: this runs on every turn, and asking a model
whether a model used a source costs a second round trip to learn something a
set intersection already knows.

This module is pure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["Citation", "sources_used_by", "words_of"]

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'.-]*")
_NUMBER_RE = re.compile(r"^\d+([.,]\d+)?$")

#: Words that two unrelated texts share anyway, so sharing them proves nothing.
_STOP_WORDS = frozenset(
    """
    the and for are but not you your yours our ours their they them this that these
    those with from into onto over under about what when where which while who whom
    whose why how can could would should will shall may might must have has had
    having was were been being does did doing just also than then there here very
    more most some such only own same other any each all both few many much per via
    its it's i'm we're you're let's yes okay sure please thanks thank hello help
    like get got make made use used using want need know see one two three
    """.split()
)


@dataclass(frozen=True)
class Citation:
    """A source the reply drew on, with how much of it showed up."""

    source: Mapping[str, Any]
    shared: int


def words_of(text: Any) -> set[str]:
    """The words worth comparing: no stop words, no fragments, numbers kept.

    Numbers survive whole, because "29" appearing in both a reply and a pricing
    page is the strongest evidence there is that one came from the other.
    """
    found: set[str] = set()
    for raw in _WORD_RE.findall(str(text or "").lower()):
        word = raw.rstrip(".'-")
        if _NUMBER_RE.match(word):
            found.add(word)
            continue
        if len(word) < 3 or word in _STOP_WORDS:
            continue
        # Crude singularisation, so "seats" matches "seat". A stemmer would be
        # more correct and would also need a dependency and a language.
        found.add(word[:-1] if len(word) > 4 and word.endswith("s") else word)
    return found


def sources_used_by(
    reply: str,
    sources: Iterable[Mapping[str, Any]],
    *,
    min_shared_words: int = 3,
    limit: int | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """
    The sources this reply used, most-used first.

    ``min_shared_words`` is relaxed for a short reply: "Yes — 29 per seat" has
    only three words worth comparing, and it should still be able to show where
    the 29 came from.
    """
    reply_words = words_of(reply)
    if not reply_words:
        return ()

    needed = min(min_shared_words, max(1, (len(reply_words) + 1) // 2))
    scored: list[Citation] = []
    for source in sources:
        shared = len(reply_words & words_of(source.get("text")))
        if shared >= needed:
            scored.append(Citation(source=source, shared=shared))

    scored.sort(
        key=lambda citation: (
            -citation.shared,
            -float(citation.source.get("score") or 0),
        )
    )
    chosen: Sequence[Citation] = scored if limit is None else scored[:limit]
    return tuple(citation.source for citation in chosen)
