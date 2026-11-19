"""
When a draft differs from what visitors are being served.

This is one question with one answer in one place, and it is worth its own
file because the obvious implementation is wrong. "Has it been edited since
the last publish" reads like a revision counter -- and a counter cannot
answer it, because publishing moves the counter itself. So the rule is a
comparison against the published snapshot, and these tests are mostly about
the cases where a counter and a comparison disagree.
"""

from __future__ import annotations

import pytest

from celeris_bridge.domain.agent.overrides import AbilityOverride, Overrides
from celeris_bridge.domain.agent.records import AgentInstance, AgentInstanceVersion


def agent(**overrides) -> AgentInstance:
    fields = {
        "id": "agent_1",
        "workspace_id": "ws_acme",
        "template_key": "marketing_v1",
        "label": "Acme website",
        "draft_template_version": 1,
    }
    fields.update(overrides)
    return AgentInstance(**fields)


def snapshot(**overrides) -> AgentInstanceVersion:
    fields = {
        "id": "ver_1",
        "workspace_id": "ws_acme",
        "instance_id": "agent_1",
        "version": 1,
        "template_key": "marketing_v1",
        "template_version": 1,
    }
    fields.update(overrides)
    return AgentInstanceVersion(**fields)


def settings(**values) -> Overrides:
    return Overrides(settings=dict(values))


class TestNothingPublished:
    def test_everything_is_unpublished_when_there_is_no_snapshot(self) -> None:
        assert agent().differs_from(None) is True

    def test_even_an_untouched_draft(self) -> None:
        # Nobody is being served, so there is nothing the draft agrees with.
        assert agent(draft_revision=0).differs_from(None) is True


class TestAgainstASnapshot:
    def test_a_published_draft_that_nobody_has_touched_does_not_differ(self) -> None:
        # The case the old rule got wrong: publishing bumps the revision, so
        # a counter says "edited" about the publish itself.
        instance = agent(draft_revision=7, published_version=1)
        assert instance.differs_from(snapshot()) is False

    def test_an_edited_setting_differs(self) -> None:
        instance = agent(draft_overrides=settings(greeting="Hello"), published_version=1)
        assert instance.differs_from(snapshot()) is True

    def test_a_changed_value_differs(self) -> None:
        instance = agent(draft_overrides=settings(greeting="Hi"), published_version=1)
        assert instance.differs_from(snapshot(overrides=settings(greeting="Hello"))) is True

    def test_an_edit_that_puts_a_value_back_stops_counting(self) -> None:
        # Edit, then undo. The revision has moved twice and the agent is
        # exactly what visitors have -- so saying "unpublished changes" here
        # would be a lie, and the warning that is sometimes a lie is the one
        # people learn to ignore.
        published = snapshot(overrides=settings(greeting="Hello"))
        instance = agent(
            draft_overrides=settings(greeting="Hello"),
            draft_revision=9,
            published_version=1,
        )
        assert instance.differs_from(published) is False

    def test_an_ability_change_differs(self) -> None:
        instance = agent(
            draft_overrides=Overrides(
                abilities={"book_meeting": AbilityOverride(enabled=False)}
            ),
            published_version=1,
        )
        assert instance.differs_from(snapshot()) is True

    def test_an_un_upgraded_pin_does_not_differ(self) -> None:
        instance = agent(draft_template_version=3, published_version=1)
        assert instance.differs_from(snapshot(template_version=3)) is False

    def test_an_upgrade_that_is_not_published_yet_differs(self) -> None:
        # The pinned template version is part of what a snapshot holds, so
        # accepting a newer agent type is an unpublished change like any
        # other -- visitors are still being served the old one.
        instance = agent(draft_template_version=4, published_version=1)
        assert instance.differs_from(snapshot(template_version=3)) is True


class TestTheOldFieldIsGone:
    def test_there_is_no_property_that_answers_without_a_snapshot(self) -> None:
        # Deleted rather than deprecated: a property that reads right and
        # answers wrong gets used by the next person who needs this.
        with pytest.raises(AttributeError):
            getattr(agent(), "has_unpublished_changes")
