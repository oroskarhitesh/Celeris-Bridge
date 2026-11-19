"""
Tests for citation attribution.

The claim a citation makes is "this answer came from here". Showing everything
that was searched makes that claim about things the reply never used.
"""

from __future__ import annotations

import pytest

from celeris_bridge.domain.agent.engine.used_sources import sources_used_by, words_of


def source(name: str, text: str, score: float = 0.4) -> dict[str, object]:
    return {"sourceId": name, "name": name, "text": text, "score": score}


PRICING = source("pricing.pdf", "The Starter plan is 29 per seat per month, billed annually.")
SECURITY = source("security.md", "We are SOC 2 Type II certified and encrypt data at rest.")


class TestWhichWordsCount:
    def test_stop_words_are_ignored(self) -> None:
        assert words_of("the and for are but not you") == set()

    def test_short_fragments_are_ignored(self) -> None:
        assert "ab" not in words_of("ab cd seat")

    def test_numbers_survive_whole(self) -> None:
        # The strongest evidence there is: "29" in both a reply and a pricing
        # page is not a coincidence.
        assert "29" in words_of("It costs 29 per seat")
        assert "4.99" in words_of("From 4.99")

    def test_plurals_match_singulars(self) -> None:
        assert words_of("seats") == words_of("seat")

    def test_case_and_punctuation_do_not_matter(self) -> None:
        assert words_of("Starter.") == words_of("starter")


class TestAttribution:
    def test_a_source_the_reply_drew_on_is_returned(self) -> None:
        used = sources_used_by(
            "The Starter plan is 29 per seat, billed annually.", [PRICING, SECURITY]
        )
        assert [s["name"] for s in used] == ["pricing.pdf"]

    def test_a_source_the_reply_did_not_use_is_not(self) -> None:
        used = sources_used_by("I am afraid I cannot help with that.", [PRICING])
        assert used == ()

    def test_the_most_used_source_comes_first(self) -> None:
        used = sources_used_by(
            "Starter is 29 per seat per month, and we are SOC 2 certified.",
            [SECURITY, PRICING],
        )
        assert [s["name"] for s in used] == ["pricing.pdf", "security.md"]

    def test_a_tie_is_broken_by_the_better_score(self) -> None:
        one = source("a.pdf", "29 per seat monthly", score=0.3)
        other = source("b.pdf", "29 per seat monthly", score=0.8)
        used = sources_used_by("29 per seat monthly", [one, other])
        assert [s["name"] for s in used] == ["b.pdf", "a.pdf"]

    def test_a_short_reply_can_still_show_its_source(self) -> None:
        # "Yes — 29 per seat" has three comparable words. Holding it to the
        # same threshold as a paragraph would leave it uncited.
        used = sources_used_by("Yes — 29 per seat.", [PRICING])
        assert [s["name"] for s in used] == ["pricing.pdf"]

    def test_an_empty_reply_cites_nothing(self) -> None:
        assert sources_used_by("", [PRICING]) == ()

    def test_a_reply_of_nothing_but_stop_words_cites_nothing(self) -> None:
        assert sources_used_by("Yes, of course!", [PRICING]) == ()

    def test_a_source_with_no_text_cites_nothing(self) -> None:
        assert sources_used_by("29 per seat", [{"name": "x.pdf"}]) == ()

    def test_the_count_is_capped(self) -> None:
        many = [source(f"doc{n}.pdf", "29 per seat per month") for n in range(6)]
        assert len(sources_used_by("29 per seat per month", many, limit=2)) == 2

    def test_a_coincidental_overlap_is_not_enough(self) -> None:
        # One shared word between a long reply and a long passage happens all
        # the time and means nothing.
        reply = "We integrate with Slack, Teams, and about twenty other tools today."
        assert sources_used_by(reply, [SECURITY]) == ()

    @pytest.mark.parametrize("threshold", [1, 3, 5])
    def test_a_stricter_threshold_never_returns_more(self, threshold: int) -> None:
        reply = "The Starter plan is 29 per seat per month, billed annually."
        used = sources_used_by(reply, [PRICING, SECURITY], min_shared_words=threshold)
        assert len(used) <= 2
