"""
Tests for reading robots.txt.

Thin, because the parsing is the standard library's. What is worth testing is
the part that is ours: the three different things "nothing is forbidden" can
mean, that a crawl-delay is read, and that this module never fetches anything
itself.
"""

from __future__ import annotations

from celeris_bridge.domain.knowledge.robots import DEFAULT_USER_AGENT, RobotsRules

AGENT = DEFAULT_USER_AGENT

SITE = """
User-agent: *
Disallow: /admin
Disallow: /cart
Allow: /admin/public
Crawl-delay: 2

User-agent: BadBot
Disallow: /
"""


class TestWhatASiteAllows:
    def test_a_disallowed_path_is_refused(self) -> None:
        rules = RobotsRules.parse(SITE)
        assert rules.allows("https://acme.example/admin/settings") is False

    def test_an_ordinary_page_is_allowed(self) -> None:
        rules = RobotsRules.parse(SITE)
        assert rules.allows("https://acme.example/pricing") is True

    def test_a_longer_allow_beats_a_shorter_disallow(self) -> None:
        # What a site means by that pair, and what the standard library's
        # parser gets wrong -- see this module's docstring.
        rules = RobotsRules.parse(SITE)
        assert rules.allows("https://acme.example/admin/public/faq") is True

    def test_the_query_string_is_part_of_the_path(self) -> None:
        rules = RobotsRules.parse("User-agent: *\nDisallow: /search?\n")
        assert rules.allows("https://acme.example/search?q=x") is False
        assert rules.allows("https://acme.example/searching") is True

    def test_a_rule_for_another_crawler_does_not_apply(self) -> None:
        rules = RobotsRules.parse(SITE)
        assert rules.allows("https://acme.example/pricing", user_agent="BadBot") is False
        assert rules.allows("https://acme.example/pricing", user_agent=AGENT) is True

    def test_a_crawl_delay_is_read(self) -> None:
        assert RobotsRules.parse(SITE).crawl_delay == 2.0

    def test_no_crawl_delay_is_zero_and_not_a_guess(self) -> None:
        assert RobotsRules.parse("User-agent: *\nAllow: /\n").crawl_delay == 0.0


class TestTheThreeKindsOfNothingForbidden:
    def test_no_file_allows_everything(self) -> None:
        # A site with no robots.txt has not asked for anything.
        rules = RobotsRules.absent()
        assert rules.present is False
        assert rules.allows("https://acme.example/anything") is True

    def test_an_empty_file_allows_everything_but_is_present(self) -> None:
        # Behaviourally identical, factually different, and a report should be
        # able to say which happened.
        rules = RobotsRules.parse("")
        assert rules.present is True
        assert rules.allows("https://acme.example/anything") is True

    def test_a_file_that_allows_all_is_present(self) -> None:
        rules = RobotsRules.parse("User-agent: *\nDisallow:\n")
        assert rules.present is True
        assert rules.allows("https://acme.example/admin") is True


class TestItNeverFetchesAnything:
    def test_rules_are_parsed_from_text_that_was_handed_in(self) -> None:
        # The module takes text and nothing else, so robots.txt is fetched
        # through the one client that has this project's safety rules on it.
        # A parser that can fetch its own file is a way round that guard.
        rules = RobotsRules.parse("User-agent: *\nDisallow: /x\n")
        assert rules.allows("https://anything.example/x") is False
        assert rules.allows("https://anything.example/y") is True


class TestThePatternRules:
    """RFC 9309 matching, which is why this is not a wrapper any more."""

    def test_a_star_matches_any_run_of_characters(self) -> None:
        rules = RobotsRules.parse("User-agent: *\nDisallow: /*/private\n")
        assert rules.allows("https://acme.example/team/private/notes") is False
        assert rules.allows("https://acme.example/team/public") is True

    def test_a_trailing_dollar_anchors_the_end(self) -> None:
        rules = RobotsRules.parse("User-agent: *\nDisallow: /*.json$\n")
        assert rules.allows("https://acme.example/data/report.json") is False
        assert rules.allows("https://acme.example/data/report.json.html") is True

    def test_a_question_mark_is_a_question_mark(self) -> None:
        # Not a regular expression metacharacter, and not something to drop.
        rules = RobotsRules.parse("User-agent: *\nDisallow: /search?\n")
        assert rules.allows("https://acme.example/search?q=x") is False
        assert rules.allows("https://acme.example/searching") is True

    def test_the_longest_rule_wins_whatever_order_it_is_in(self) -> None:
        # Both orders, because file order is exactly what the standard library
        # went by and what sites do not expect to matter.
        forward = RobotsRules.parse("User-agent: *\nDisallow: /a\nAllow: /a/b\n")
        backward = RobotsRules.parse("User-agent: *\nAllow: /a/b\nDisallow: /a\n")
        for rules in (forward, backward):
            assert rules.allows("https://acme.example/a/b/c") is True
            assert rules.allows("https://acme.example/a/x") is False

    def test_a_tie_goes_to_allow(self) -> None:
        rules = RobotsRules.parse("User-agent: *\nDisallow: /p\nAllow: /p\n")
        assert rules.allows("https://acme.example/p") is True

    def test_an_empty_disallow_forbids_nothing(self) -> None:
        # It means "nothing is disallowed", not "every path is".
        rules = RobotsRules.parse("User-agent: *\nDisallow:\n")
        assert rules.allows("https://acme.example/anything") is True

    def test_comments_and_blank_lines_inside_a_group_are_ignored(self) -> None:
        # A blank line does not end a group, which sites rely on constantly.
        rules = RobotsRules.parse(
            "User-agent: *   # everyone\n"
            "Disallow: /admin\n"
            "\n"
            "Disallow: /cart   # no baskets\n"
        )
        assert rules.allows("https://acme.example/cart") is False
        assert rules.allows("https://acme.example/shop") is True

    def test_several_user_agents_can_share_one_group(self) -> None:
        rules = RobotsRules.parse(
            "User-agent: GoogleBot\nUser-agent: CelerisBridgeBot\nDisallow: /x\n"
        )
        assert rules.allows("https://acme.example/x") is False

    def test_a_named_group_beats_the_wildcard_in_either_order(self) -> None:
        named_last = RobotsRules.parse(
            "User-agent: *\nDisallow: /\n\nUser-agent: CelerisBridgeBot\nDisallow: /admin\n"
        )
        named_first = RobotsRules.parse(
            "User-agent: CelerisBridgeBot\nDisallow: /admin\n\nUser-agent: *\nDisallow: /\n"
        )
        for rules in (named_last, named_first):
            assert rules.allows("https://acme.example/pricing") is True
            assert rules.allows("https://acme.example/admin") is False

    def test_a_crawl_delay_for_another_crawler_is_not_ours(self) -> None:
        rules = RobotsRules.parse(
            "User-agent: BadBot\nCrawl-delay: 30\n\nUser-agent: *\nCrawl-delay: 1\n"
        )
        assert rules.crawl_delay == 1.0

    def test_a_nonsense_crawl_delay_is_ignored_not_fatal(self) -> None:
        assert RobotsRules.parse("User-agent: *\nCrawl-delay: soon\n").crawl_delay == 0.0

    def test_junk_does_not_raise(self) -> None:
        # robots.txt files in the wild contain HTML error pages, base64, and
        # people's shopping lists. None of that may stop a crawl.
        rules = RobotsRules.parse("<html><body>404 not found</body></html>")
        assert rules.allows("https://acme.example/anything") is True
