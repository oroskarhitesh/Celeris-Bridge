"""
The authenticator, on its own.

Small enough to test directly, and worth it: this is the thing that decides
which workspace every admin request is scoped to, so the way it handles a
malformed configuration should be a decision rather than whatever happens.
"""

from __future__ import annotations

import asyncio
import logging

from celeris_bridge.api.app import build_authenticator
from celeris_bridge.api.principal import DevWorkspace, StaticTokens, parse_tokens
from celeris_bridge.composition.settings import Settings


def identify(authenticator, token: str):
    return asyncio.run(authenticator.identify(token))


class TestReadingTheConfiguration:
    def test_a_token_names_a_workspace(self) -> None:
        tokens = parse_tokens("tok_a:ws_one,tok_b:ws_two")
        assert tokens["tok_a"].workspace_id == "ws_one"
        assert tokens["tok_b"].workspace_id == "ws_two"
        assert tokens["tok_a"].platform_admin is False

    def test_a_third_field_grants_the_platform_role(self) -> None:
        tokens = parse_tokens("tok_p::platform")
        assert tokens["tok_p"].platform_admin is True
        assert tokens["tok_p"].has_workspace is False

    def test_a_token_can_be_both(self) -> None:
        # A support engineer: inside one workspace and holding the platform
        # role. Not a contradiction -- it is what an internal tool needs.
        tokens = parse_tokens("tok_s:ws_one:platform")
        assert tokens["tok_s"].workspace_id == "ws_one"
        assert tokens["tok_s"].platform_admin is True

    def test_whitespace_and_empty_entries_are_ignored(self) -> None:
        assert set(parse_tokens(" tok_a : ws_one , , ")) == {"tok_a"}

    def test_a_malformed_entry_is_dropped_rather_than_fatal(
        self, caplog
    ) -> None:
        # A process that refuses to start because one line of an environment
        # variable is wrong cannot be fixed without a deploy. One that starts
        # without that token refuses its requests, which is visible at once.
        with caplog.at_level(logging.WARNING):
            tokens = parse_tokens("tok_a:ws_one,no_workspace_here,:ws_two")
        assert set(tokens) == {"tok_a"}
        assert len(caplog.records) == 2

    def test_a_malformed_entry_does_not_log_the_token(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            parse_tokens("tok_secret_value")
        assert "tok_secret_value" not in caplog.text


class TestIdentifying:
    def test_a_known_token_is_its_principal(self) -> None:
        authenticator = StaticTokens(parse_tokens("tok_a:ws_one"))
        assert identify(authenticator, "tok_a").workspace_id == "ws_one"

    def test_anything_else_is_nobody(self) -> None:
        authenticator = StaticTokens(parse_tokens("tok_a:ws_one"))
        assert identify(authenticator, "tok_b") is None
        assert identify(authenticator, "") is None
        assert identify(authenticator, "tok_a ") is None

    def test_the_tokens_are_not_kept(self) -> None:
        # The table is keyed on a digest: a dictionary lookup cannot leak a
        # token through timing, and a core dump of this process does not
        # contain anybody's credential.
        authenticator = StaticTokens(parse_tokens("tok_secret_value:ws_one"))
        assert "tok_secret_value" not in repr(authenticator.__dict__)

    def test_the_development_authenticator_needs_no_token(self) -> None:
        who = identify(DevWorkspace("ws_dev"), "")
        assert who.workspace_id == "ws_dev"
        assert who.platform_admin is True


class TestWhichOneGetsBuilt:
    def _options(self, **overrides) -> Settings:
        return Settings(_env_file=None, adapters="memory", **overrides)

    def test_tokens_win_over_the_development_shortcut(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            built = build_authenticator(
                self._options(api_tokens="tok_a:ws_one", dev_workspace="ws_dev")
            )
        assert isinstance(built, StaticTokens)
        assert "ignored" in caplog.text

    def test_the_development_shortcut_warns_every_time(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            built = build_authenticator(self._options(dev_workspace="ws_dev"))
        assert isinstance(built, DevWorkspace)
        assert "not authentication" in caplog.text

    def test_neither_configured_refuses_everything(self, caplog) -> None:
        # The right failure: visible on the first call, fixed without a
        # deploy, and impossible to mistake for working.
        with caplog.at_level(logging.WARNING):
            built = build_authenticator(self._options())
        assert identify(built, "anything") is None
        assert "refuse every request" in caplog.text
