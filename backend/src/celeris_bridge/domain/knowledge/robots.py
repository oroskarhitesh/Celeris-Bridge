"""
robots.txt, parsed here.

This started as a wrapper over :mod:`urllib.robotparser` on the reasoning that
the format has more corners than it looks like it has and the standard library
would already know them. Two tests said otherwise:

* ``Disallow: /admin`` followed by ``Allow: /admin/public`` blocks
  ``/admin/public/faq`` there, because it applies rules in file order and
  takes the first match. RFC 9309 says the **longest** matching rule wins,
  which is what sites write that pair expecting.
* ``Disallow: /search?`` is reduced to ``Disallow: /search``, which then also
  blocks ``/searching``.

Both make the crawler refuse pages a customer wanted indexed, on their own
site, so the matcher is here. It implements RFC 9309: longest match wins, a
tie goes to ``Allow``, ``*`` matches any run of characters and a trailing
``$`` anchors the end.

**Absent means allowed; unreadable means allowed; forbidden means stop.** The
three are easy to collapse into one and are not the same thing. A site with no
robots.txt has not asked for anything. A 403 on the robots file itself is, by
the strictest reading, a site saying the crawler may not even look -- but this
product fetches a site at the request of the person who owns it, and refusing
their own pages because their CDN hides robots.txt is unhelpful theatre. So a
failure to read it allows everything, and only an explicit rule stops anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

__all__ = ["DEFAULT_USER_AGENT", "RobotsRules"]

#: Identifies the crawler and says who to contact about it. A crawler with no
#: user agent is indistinguishable from an attack and gets blocked as one.
DEFAULT_USER_AGENT = "CelerisBridgeBot/1.0 (+https://celeris.example/bot)"


@dataclass(frozen=True)
class _Rule:
    """One ``Allow`` or ``Disallow`` line, compiled."""

    pattern: str
    allow: bool
    matcher: re.Pattern[str]

    @property
    def length(self) -> int:
        """How specific it is. RFC 9309 ranks by the pattern's length."""
        return len(self.pattern)


@dataclass(frozen=True)
class _Group:
    """One ``User-agent`` block."""

    agents: tuple[str, ...]
    rules: tuple[_Rule, ...] = ()
    crawl_delay: float = 0.0


@dataclass(frozen=True)
class RobotsRules:
    """What a site's robots.txt permits.

    ``present`` is kept separate from "allows everything" so a report can say
    which of the two happened: a site with no robots.txt and a site that
    allows all of it behave identically and are different facts.
    """

    present: bool = False
    groups: tuple[_Group, ...] = field(default=())
    #: From ``Crawl-delay``, for the group that applies. Honoured as a floor on
    #: the delay between requests: a site asking for 10 seconds gets 10.
    crawl_delay: float = 0.0

    @classmethod
    def absent(cls) -> "RobotsRules":
        """No robots.txt, or one that could not be read. Everything allowed."""
        return cls(present=False)

    @classmethod
    def parse(cls, text: str, *, user_agent: str = DEFAULT_USER_AGENT) -> "RobotsRules":
        groups = _parse(text)
        chosen = _group_for(groups, user_agent)
        return cls(
            present=True,
            groups=groups,
            crawl_delay=chosen.crawl_delay if chosen else 0.0,
        )

    def allows(self, url: str, *, user_agent: str = DEFAULT_USER_AGENT) -> bool:
        """Whether this crawler may fetch ``url``."""
        group = _group_for(self.groups, user_agent)
        if group is None or not group.rules:
            return True

        path = _path_of(url)
        best: _Rule | None = None
        for rule in group.rules:
            if not rule.matcher.match(path):
                continue
            # Longest wins; on a tie Allow wins, which is what makes an
            # explicit Allow able to carve a hole in a broader Disallow.
            if best is None or rule.length > best.length or (
                rule.length == best.length and rule.allow and not best.allow
            ):
                best = rule
        return best.allow if best is not None else True


def _path_of(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    return f"{path}?{parts.query}" if parts.query else path


def _compile(pattern: str) -> re.Pattern[str]:
    """A robots path pattern as a regular expression.

    Only ``*`` and a trailing ``$`` are special, so everything else is escaped
    -- including ``?``, which is why a rule written ``/search?`` must not be
    allowed to match ``/searching``.
    """
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    expression = "".join(".*" if piece == "*" else re.escape(piece) for piece in re.split(r"(\*)", body))
    return re.compile(expression + ("$" if anchored else ""))


def _parse(text: str) -> tuple[_Group, ...]:
    groups: list[_Group] = []
    agents: list[str] = []
    rules: list[_Rule] = []
    delay = 0.0
    # A blank line does not end a group -- only a User-agent line after at
    # least one rule does. Sites put blank lines inside groups constantly.
    expecting_agents = True

    def flush() -> None:
        nonlocal agents, rules, delay, expecting_agents
        if agents:
            groups.append(
                _Group(agents=tuple(agents), rules=tuple(rules), crawl_delay=delay)
            )
        agents, rules, delay = [], [], 0.0
        expecting_agents = True

    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped or ":" not in stripped:
            continue
        field_name, _, value = stripped.partition(":")
        name = field_name.strip().lower()
        value = value.strip()

        if name == "user-agent":
            if not expecting_agents:
                flush()
            agents.append(value.lower())
            continue

        expecting_agents = False
        if name in {"allow", "disallow"}:
            if not value:
                # "Disallow:" with nothing after it means nothing is
                # disallowed. Compiling it would match every path.
                continue
            rules.append(
                _Rule(pattern=value, allow=name == "allow", matcher=_compile(value))
            )
        elif name == "crawl-delay":
            try:
                delay = max(float(value), 0.0)
            except ValueError:
                delay = 0.0
    flush()
    return tuple(groups)


def _group_for(groups: tuple[_Group, ...], user_agent: str) -> _Group | None:
    """The group that applies to this crawler.

    The most specific name that matches wins, and ``*`` is the fallback --
    so a site can allow crawlers in general and name this one for an
    exception, in either order in the file.
    """
    token = user_agent.split("/", 1)[0].strip().lower()
    best: _Group | None = None
    fallback: _Group | None = None
    for group in groups:
        for agent in group.agents:
            if agent == "*":
                fallback = fallback or group
            elif agent and (agent in token or token in agent):
                if best is None or len(agent) > max(len(one) for one in best.agents):
                    best = group
    return best or fallback
