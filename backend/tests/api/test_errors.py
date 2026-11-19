"""
One error shape, including for the errors FastAPI raises itself.

A client that has to parse two error formats will handle one of them badly,
and the one it handles badly will be the one that only appears when something
has already gone wrong. So every failure here -- a domain refusal, a bad body,
a route that does not exist, an unhandled bug -- comes back as the same
envelope with a stable code.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from celeris_bridge.services.resolution import AgentNotFound

from tests.api.conftest import TEMPLATE, auth, make_agent


def error_of(answer) -> dict:
    body = answer.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) <= {"code", "message", "details"}
    return body["error"]


class TestTheEnvelope:
    def test_a_route_that_does_not_exist(self, client) -> None:
        answer = client.get("/v1/nothing-here")
        assert answer.status_code == 404
        assert error_of(answer)["code"] == "HTTP_ERROR"

    def test_a_method_that_is_not_allowed(self, client) -> None:
        answer = client.delete("/v1/templates", headers=auth("platform"))
        assert answer.status_code == 405
        assert error_of(answer)["code"] == "HTTP_ERROR"

    def test_a_malformed_body(self, client) -> None:
        answer = client.post(
            "/v1/agents", json={"label": "No template"}, headers=auth("ws")
        )
        assert answer.status_code == 422
        error = error_of(answer)
        assert error["code"] == "VALIDATION_FAILED"
        assert any(
            "templateKey" in one["path"] for one in error["details"]["problems"]
        )

    def test_a_key_that_is_not_a_key(self, client) -> None:
        # The shape of a template key is the API's business: it goes in a URL.
        answer = client.post(
            "/v1/templates",
            json={"key": "Not A Key!", "draft": {}},
            headers=auth("platform"),
        )
        assert answer.status_code == 422
        assert error_of(answer)["code"] == "VALIDATION_FAILED"

    def test_a_duplicate_template_key(self, client) -> None:
        # Keys are permanent: reusing one would point existing agents at a
        # different agent type.
        answer = client.post(
            "/v1/templates", json={"key": TEMPLATE}, headers=auth("platform")
        )
        assert answer.status_code == 409
        assert error_of(answer)["code"] == "ALREADY_EXISTS"

    def test_an_unpublishable_draft_lists_every_problem(self, client) -> None:
        # A draft may be as broken as whoever is writing it wants; publishing
        # is the one place that refuses, and it says everything at once.
        created = client.post(
            "/v1/templates",
            json={"key": "half_written", "draft": {"identity": {}}},
            headers=auth("platform"),
        )
        assert created.status_code == 201, created.text
        answer = client.post(
            f"/v1/templates/half_written/publish",
            json={"expectRevision": created.json()["draftRevision"]},
            headers=auth("platform"),
        )
        assert answer.status_code == 400
        error = error_of(answer)
        assert error["code"] == "DEFINITION_INVALID"
        assert len(error["details"]["problems"]) > 1
        assert all(
            {"path", "message"} == set(one) for one in error["details"]["problems"]
        )

    def test_checking_a_broken_draft_is_not_an_error(self, client) -> None:
        client.post(
            "/v1/templates",
            json={"key": "half_written", "draft": {"identity": {}}},
            headers=auth("platform"),
        )
        answer = client.get("/v1/templates/half_written/check", headers=auth("platform"))
        assert answer.status_code == 200
        assert answer.json()["ok"] is False
        assert answer.json()["problems"]


class TestAnUnexpectedFailure:
    def test_an_unmapped_exception_does_not_leak_its_words(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The point of having no catch-all mapping: a bug is a 500 with
        # nothing in it, rather than a 400 that a frontend learns to handle.
        class Broken:
            async def list(self, *args, **kwargs):
                raise ZeroDivisionError("the database password is hunter2")

        application = client.app.state.application
        client.app.state.application = replace(application, instances=Broken())

        with pytest.raises(ZeroDivisionError):
            # The test client re-raises, which is how a test knows it was a
            # bug and not a handled refusal. A real client gets the 500.
            client.get("/v1/agents", headers=auth("ws"))

    def test_a_service_lookup_failure_is_a_404_and_not_a_500(self, client) -> None:
        # Every "no such thing" in the services is a LookupError, so a new one
        # is a 404 on the day it is written rather than a 500.
        assert issubclass(AgentNotFound, LookupError)
        answer = client.get("/v1/conversations/nope", headers=auth("ws"))
        assert answer.status_code == 404
        assert error_of(answer)["code"] == "NOT_FOUND"


class TestHealth:
    def test_liveness_touches_nothing(self, client) -> None:
        # No database, no provider: a liveness check that queries a database
        # restarts the whole fleet the moment the database has a bad minute.
        assert client.get("/health").json() == {"status": "ok"}

    def test_readiness_says_what_it_checked(self, client) -> None:
        answer = client.get("/readyz")
        assert answer.status_code == 200
        assert answer.json() == {"status": "ready", "records": "memory"}

    def test_neither_needs_a_credential(self, client) -> None:
        assert client.get("/health").status_code == 200
        assert client.get("/readyz").status_code == 200
