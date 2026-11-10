"""VERSION-READY-01 -- a strategy saved from the builder must be deployable.

Production held six ``strategy_versions`` rows, every one a ``VALIDATED`` draft. Deploy refused them
(409 ``VERSION_NOT_READY``: "validated but has not been persisted as an immutable version yet - save
it"), a Paper_Session refuses drafts and any state outside READY/DEPLOYED/RUNNING/PAUSED, and the
Marketplace gate counts only non-draft versions. A trader could build a strategy and then do nothing
with it except backtest.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend_app.routers import strategies as router


class _Service:
    def __init__(self):
        self.kwargs = None

    async def create_version(self, user, strategy_id, graph, **kwargs):
        self.kwargs = kwargs
        return {"version": {"id": "v-1", "version": "v1.0"}, "dag_hash": "h", "canonical_persisted": True}


def _persist(monkeypatch, ml_nodes):
    service = _Service()

    async def _get():
        return service

    import backend_app.backend.strategy_service as ss

    monkeypatch.setattr(ss, "get_strategy_service", _get)
    compiled = SimpleNamespace(graph={}, plan=SimpleNamespace(ml_nodes=ml_nodes))
    out = asyncio.run(router._persist_current_version({"id": "u"}, "s-1", compiled))
    return service.kwargs, out


def test_a_graph_with_no_model_block_is_saved_ready_and_not_a_draft(monkeypatch):
    kwargs, out = _persist(monkeypatch, ())
    assert kwargs["lifecycle_state"] == "READY"
    assert kwargs["is_draft"] is False
    assert kwargs["make_current"] is True
    assert out["version_saved"] is True


def test_a_graph_with_a_model_block_is_not_deployable_until_trained(monkeypatch):
    kwargs, _ = _persist(monkeypatch, ("n-9",))
    assert kwargs["lifecycle_state"] == "VALIDATED"
    assert kwargs["is_draft"] is True


def test_the_ready_state_is_one_a_paper_session_and_a_deploy_accept():
    from backend_app.backend.paper.paper_session_service import DEPLOYABLE_LIFECYCLE_STATES

    assert "READY" in DEPLOYABLE_LIFECYCLE_STATES
    assert "VALIDATED" not in DEPLOYABLE_LIFECYCLE_STATES, (
        "if VALIDATED ever becomes deployable this test and the save rule should be revisited"
    )
