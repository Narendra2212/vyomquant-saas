"""PAPER-04 -- a Paper_Session's loop no longer dies when its starting JWT expires.

THE DEFECT
    ``POST /api/paper/sessions`` built ``supabase = _persistence(user)`` -- a client carrying
    the CALLER's own Supabase access token, which expires in about an hour -- and handed that
    SAME object into ``_session_loop_spawner(supabase)``, which closes over it inside
    ``asyncio.create_task(session_loop(...))``. That task can run for the life of the session:
    hours or days. Once the token expired, every statement the loop made started failing.

    Two different silences followed, both ending at ``session_state`` staying ``RUNNING``
    forever for a session that had already died:

    * Inside ``step_session``, the loop's own containment for Requirement 14.7 ("one bad bar
      must not halt the runtime") is a bare ``except Exception: continue`` around EVERY failure
      a bar can produce -- an auth failure included. So the loop kept "running", producing
      nothing, forever.
    * ``repo.read_session(supabase, user_id, session_id)`` sits OUTSIDE that try, at the top of
      every iteration. If IT is what first hits the expired token, the exception escapes the
      loop body entirely and kills the ``asyncio.Task`` with nothing awaiting it -- "Task
      exception was never retrieved" on stderr and nothing else an operator would see.

THE FIX
    ``start_paper_session`` now resolves a SEPARATE, non-expiring service-role client
    (``get_service_supabase()``) and hands THAT to ``_session_loop_spawner`` -- never the
    caller's. The caller's own RLS-scoped client is untouched for the rest of the request (the
    entitlement pipeline, the creates). This is safe for the loop specifically because every
    statement it reaches already carries an explicit ``user_id``/``session_id`` predicate that
    is RE-CHECKED against the row returned -- ``paper_repository.read_session``'s own docstring
    states this of every read in that module -- so tenant isolation here is the application
    predicate, not RLS, and the session/user id the loop closes over were fixed at spawn time
    from a row this session already created, never from later caller input.

    A missing service-role credential now refuses the start outright (503
    ``PAPER_PERSISTENCE_UNAVAILABLE``) rather than silently falling back to the caller's
    expiring client, which would be this exact defect again.

WHAT THIS FILE PROVES, AND WHAT IT DOES NOT
    This file is about the 503 refusal path and its ordering relative to request-shape
    validation. The wiring claim itself -- that the loop's own ``supabase`` argument IS the
    service-role handle and not the caller's -- is proved by identity in
    ``tests/test_task_28_1_session_routes.py::TestTheStartWiring::
    test_the_loop_steps_with_the_pipelines_own_config_and_account``, which this file does not
    re-prove.

    NOT claimed: that the loop now survives a worker restart, or that a ``RUNNING`` session
    orphaned by one is ever resumed or marked anything else. That half of PAPER-04's remediation
    needs a dedicated runtime worker service (RT-23) that does not exist yet, and remains open.
"""
from __future__ import annotations

import inspect
from typing import Any

from tests.test_task_28_1_session_routes import (
    EXCHANGE,
    _client,
    _double,
    _patch_start,
    _start_body,
)


def _no_service_role(monkeypatch: Any) -> None:
    from backend_app.routers import paper_trading

    monkeypatch.setattr(paper_trading, "get_service_supabase", lambda: None)


class TestNoServiceRoleClientIsACleanRefusal:
    def test_missing_service_role_client_refuses_before_creating_anything(
        self, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        calls = _patch_start(monkeypatch)
        _no_service_role(monkeypatch)
        client = _client(_double())

        response = client.post("/api/paper/sessions", json=_start_body())

        assert response.status_code == 503, response.text
        body = response.json()["error"]
        assert body["code"] == "PAPER_PERSISTENCE_UNAVAILABLE"
        assert body["details"]["reason"] == "SERVICE_ROLE_CLIENT_UNAVAILABLE"
        # The pipeline was never entered: no session, no paper order, no market-data
        # subscription was created for a start this server could not safely drive a loop for.
        assert calls == []

    def test_a_malformed_request_still_422s_even_with_no_service_role_client(
        self, monkeypatch: Any
    ) -> None:
        """NEGATIVE CONTROL. The 503 above must come from the service-role check, not from
        some blanket early-exit that would also eat a request-shape refusal.
        """
        _no_service_role(monkeypatch)
        client = _client(_double())

        response = client.post(
            "/api/paper/sessions", json=_start_body(listing_id="not-a-uuid")
        )

        assert response.status_code == 422, response.text

    def test_a_well_formed_request_with_a_service_role_client_is_not_refused_this_way(
        self, monkeypatch: Any
    ) -> None:
        """NEGATIVE CONTROL for the first test: the 503 is conditional on the missing client,
        not unconditional.
        """
        monkeypatch.setenv("DEFAULT_EXCHANGE", EXCHANGE)
        calls = _patch_start(monkeypatch)  # installs a default, non-None service double
        client = _client(_double())

        response = client.post("/api/paper/sessions", json=_start_body())

        assert response.status_code == 201, response.text
        assert len(calls) == 1


class TestTheOldSharedClientWiringIsActuallyGone:
    def test_the_route_no_longer_hands_the_loop_the_requests_own_persistence_handle(self) -> None:
        """Pins the specific line-level regression this fix removes.

        Before the fix, the source read ``spawn_loop=_session_loop_spawner(supabase)`` -- the
        same ``supabase`` the request's entitlement pipeline uses, built from the caller's own
        access token. A reader who re-introduced that line would reintroduce PAPER-04 even if
        every other assertion in this file still passed, because this file's doubles do not
        model token expiry at all -- only the source itself proves which handle is used.
        """
        from backend_app.routers import paper_trading

        source = inspect.getsource(paper_trading.start_paper_session)
        assert "get_service_supabase" in source, (
            "start_paper_session no longer resolves a service-role client for the loop"
        )
        assert "_session_loop_spawner(supabase)" not in source, (
            "the loop is wired with the caller's own RLS-scoped handle again -- this is "
            "PAPER-04's exact defect: a long-lived asyncio.Task closing over a client whose "
            "JWT expires in about an hour"
        )
        assert "_session_loop_spawner(loop_supabase)" in source
