"""tests/test_paper03_client_resolution_is_service_role.py

PAPER-03 (P1) -- the code half of the fix, independent of a real PostgreSQL.

``PaperTradingService._client`` (reached by every read and write through ``persistence_client``/
``_client`` itself, and by every router via ``routers/paper_trading.py::_persistence``) used to
build a per-request client from the caller's own JWT (``create_request_supabase(access_token)``)
whenever nothing had been explicitly bound. That client carried the caller's own identity, and
``auth.uid()`` resolving against it is exactly what let the ``*_owner_access`` RLS policies (and
the ``authenticated`` GRANT migration 043 narrows) admit a trader's own direct write.

This file proves the resolver itself no longer ever reaches for the caller's JWT: with nothing
bound, it asks for the platform's own service-role client, and an ``access_token`` argument - the
several real callers still pass one - makes no difference to which client comes back.
"""
from __future__ import annotations

from unittest.mock import sentinel

import pytest

from backend_app.backend.paper_trading_service import PaperTradingService


@pytest.fixture
def service():
    return PaperTradingService()


def test_with_nothing_bound_it_asks_for_the_service_role_client(monkeypatch, service):
    calls = []

    def _service_supabase():
        calls.append("service")
        return sentinel.service_client

    def _anon_supabase():
        calls.append("anon")
        return sentinel.anon_client

    def _request_supabase(token):
        calls.append(("request", token))
        return sentinel.request_client

    monkeypatch.setattr(
        "backend_app.core.dependencies.get_service_supabase", _service_supabase
    )
    monkeypatch.setattr("backend_app.core.dependencies.get_supabase", _anon_supabase)
    monkeypatch.setattr(
        "backend_app.core.dependencies.create_request_supabase", _request_supabase
    )

    result = service.persistence_client("a_real_callers_own_jwt")

    assert result is sentinel.service_client
    assert calls == ["service"], (
        "the caller's own access token must never select create_request_supabase any more"
    )


def test_an_access_token_makes_no_difference_to_which_client_comes_back(monkeypatch, service):
    monkeypatch.setattr(
        "backend_app.core.dependencies.get_service_supabase",
        lambda: sentinel.service_client,
    )
    monkeypatch.setattr(
        "backend_app.core.dependencies.create_request_supabase",
        lambda token: pytest.fail(
            "create_request_supabase must not be called -- PAPER-03's whole point is that "
            "the caller's JWT no longer selects a Persistence_Layer client"
        ),
    )

    with_token = service.persistence_client("some_jwt")
    without_token = service.persistence_client(None)

    assert with_token is sentinel.service_client
    assert without_token is sentinel.service_client


def test_a_bound_handle_still_wins_over_everything_else(monkeypatch, service):
    """NEGATIVE CONTROL: this fix does not touch ``bind_persistence`` -- every existing test,
    and the bot-driven session loop (PAPER-04), rely on the bound handle taking priority.
    """
    monkeypatch.setattr(
        "backend_app.core.dependencies.get_service_supabase",
        lambda: pytest.fail("a bound handle must win before the service-role client is asked for"),
    )

    service.bind_persistence(sentinel.bound_double)

    assert service.persistence_client("irrelevant_token") is sentinel.bound_double


def test_falls_back_to_the_anon_singleton_when_service_role_credentials_are_unavailable(
    monkeypatch, service
):
    """DEV_MODE's local-run fallback: ``get_service_supabase`` returning/raising "no
    credentials" must not leave the whole paper facade unusable where it previously worked
    anonymously.
    """
    monkeypatch.setattr(
        "backend_app.core.dependencies.get_service_supabase",
        lambda: (_ for _ in ()).throw(RuntimeError("no service-role credentials configured")),
    )
    monkeypatch.setattr(
        "backend_app.core.dependencies.get_supabase", lambda: sentinel.anon_client
    )

    assert service.persistence_client(None) is sentinel.anon_client


def test_raises_paper_persistence_unavailable_when_no_client_can_be_built_at_all(
    monkeypatch, service
):
    from backend_app.backend.paper.errors import PaperError

    monkeypatch.setattr(
        "backend_app.core.dependencies.get_service_supabase", lambda: None
    )
    monkeypatch.setattr("backend_app.core.dependencies.get_supabase", lambda: None)

    with pytest.raises(PaperError):
        service.persistence_client(None)
