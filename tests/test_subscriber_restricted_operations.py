"""
tests/test_subscriber_restricted_operations.py

Task 17.5 of ``marketplace-subscriptions-paper-trading``: the SERVER half of Requirement 12.4.

Requirements 7.1, 7.8, 7.9, 7.12, 12.7, 23.3.

WHAT THIS FILE ASSERTS
----------------------
1. **All thirteen restricted operations answer 403 with a stable code — not 404 — to a
   subscriber holding an ``ACTIVE`` Subscription**, "even when the client offers no such
   affordance" (Requirement 12.7). The thirteen are not re-listed here: they are read from
   ``library_entries.SUBSCRIBER_FORBIDDEN_ACTIONS``, which task 17.4 established and
   ``tests/test_my_strategies_ownership_and_actions.py`` already holds to Requirement 12.4's
   wording. A second copy of the list is a second thing to drift.

2. **The Signal Trace read path returns only the Requirement 23.2 fields to a subscriber**, and
   excludes node-level trace detail, indicator values, feature values, ML inference detail and
   risk-rule internals (Requirements 7.9, 23.3).

3. **Each refusal writes a ``MARKETPLACE_ACCESS_REFUSED`` Audit_Log entry within 5 seconds
   carrying no Protected_Logic** (Requirement 7.12). ``library.py``'s
   ``_audit_marketplace_refusal`` (task 17.3) is the reference for the entry's shape, and
   ``listing_projection.assert_contains_no_protected_logic`` is the oracle for the no-logic half.

WHY 403 AND NOT 404, AND WHY THAT IS THE WHOLE POINT
----------------------------------------------------
``design.md`` → "Server-side artifact resolution": *"Edit, rename, re-version, recompile, export
and download of a subscribed strategy are refused with 403 … the subscriber's ``strategies`` row
does not exist, so the ownership predicate already fails; the change is to make the refusal a 403
with a stable code rather than a 404, per Requirement 12.7, for the specific case where the caller
holds an ``ACTIVE`` Subscription to the Listing that owns the strategy."*

A 404 says the strategy does not exist. For this caller that is FALSE: the subscriber can see the
strategy on their own Strategies page, served by ``GET /api/library/my-strategies`` as a
``SUBSCRIBED`` entry, and :class:`TestThePermittedOperationsAreReachable` below proves they can.
Answering "no such thing" to a caller who is looking at it is the fabricated fact Requirement 28.5
forbids in general and Requirement 12.7 forbids here specifically, and it is a worse answer than
403 in practice: a client cannot distinguish it from a deleted Listing, so it cannot render
Requirement 12.8's ``unavailable-strategy`` state correctly.

The distinction between 404 and 403 is also the distinction between two DIFFERENT properties. A
404 that is identical to a stranger's 404 is Requirement 20.2 of
``.kiro/specs/trading-lifecycle-integration/`` — cross-tenant existence non-disclosure, asserted by
``tests/sandbox_lifecycle/test_cross_tenant_ownership_matrix.py``. That property is about a
STRANGER. This file is about a SUBSCRIBER, to whom the Listing's existence is already disclosed —
they paid for it. Nothing here weakens the stranger's 404; the registry below is scoped to a caller
holding an ``ACTIVE`` Subscription, which is exactly the case the design carves out.

THE PENDING REGISTRY (read this before "fixing" a failure here)
--------------------------------------------------------------
:data:`PENDING` is a list of known defects, not a list of exemptions. Each entry names the clauses
a probe fails and is marked ``xfail(strict=True)``: it must keep failing until the fix lands, and
the moment one starts passing THIS FILE FAILS with ``XPASS`` — which is the signal to DELETE its
registry entry, not to loosen an assertion. This is the pattern
``tests/test_marketplace_error_surface.py`` established, applied unchanged.

It held fourteen entries of one shape — an owner-scoped read or UPDATE whose empty result was
reported as non-existence — and task 17.9 fixed all fourteen, so those fourteen entries are gone.
:data:`PENDING` now holds ONE: ``view_risk_config.risk_metrics``, whose route resolves no
ownership at all and therefore needs a different fix (see the entry).

Do NOT weaken an assertion to make a 404 pass, and do NOT add a probe to :data:`PENDING` to
silence it.

THE THREE-WAY DISTINCTION, AND WHY THE STRANGER'S 404 IS NOT A DEFECT
---------------------------------------------------------------------
Task 17.9 resolves three callers, not two:

* **owner** — unchanged, exactly as before. :class:`TestTheOwnerIsUnaffected` asserts that per
  endpoint, and asserts it as an EQUALITY against the same request run with the guard removed.
* **entitled subscriber** — 403 ``MARKETPLACE_OPERATION_NOT_PERMITTED`` plus one audited
  refusal. That is what the clauses below assert.
* **unrelated stranger** — **still 404**, indistinguishable from a non-existent artifact.
  :class:`TestTheStrangerStillCannotTellExistenceApart` asserts that per endpoint, because it is
  what a careless fix breaks: turning the stranger's 404 into a 403 would make every one of these
  endpoints an existence oracle, which Requirement 21.4 forbids.

NON-VACUITY — FOUR SEPARATE GUARDS
----------------------------------
A file full of refusals is worthless if everything refuses, if the refused thing does not exist, or
if the routes probed serve nothing.

* **The strategy genuinely carries what must not leak.** The seeded owner strategy has DAG nodes
  (``buy_logic`` / ``sell_logic`` with node ids), indicator parameters, a risk configuration and a
  bound ML model, and its version carries the blueprint and graph. So "the response contains none
  of it" is a statement about a strategy that has it.
  :meth:`TestTheProbeSurfaceIsReal.test_the_seed_carries_protected_logic_to_leak` asserts the token
  set is non-empty, so the containment oracle cannot pass by having nothing to look for.
* **The subscriber can reach what they are permitted.**
  :class:`TestThePermittedOperationsAreReachable` drives ``GET /api/library/my-strategies`` and
  ``POST /api/library/{id}/deploy`` as the same subscriber, with the same token, against the same
  database, and both succeed. So the thirteen refusals are refusals, not a broken harness.
* **The owner can reach the restricted operations.**
  :class:`TestTheOwnerIsNotRefused` drives a sample of the same probes as the OWNER and requires a
  different answer, so each probed route really does reach the seeded row.
* **Every probed path resolves to the handler the probe names.**
  :class:`TestTheProbeSurfaceIsReal` resolves each URL through Starlette's own matcher, because
  ``/api/strategies/{id}`` is declared by BOTH ``strategies.py`` and ``strategy_operations.py`` and
  first-match-wins. A shadowed route cannot pass as tested.

WHAT IS REAL AND WHAT IS A DOUBLE
---------------------------------
Real: the mounted ``backend_app.main.app``, its routing and mounting order, every request model,
``entitlement_resolver``, ``library_entries``, ``listing_projection``, ``StrategyService``,
``SignalService``, ``strategy_archive`` and the ownership predicate of every handler probed.

Doubles: :class:`~tests.sandbox_lifecycle.harness.SandboxDatabase` — the PostgREST client the
Requirement 26 suite already established, imported rather than re-written because it *applies* the
predicates the production code sends, so a handler whose ``user_id`` filter went missing would
select the owner's row and be caught here rather than passing on a policy this host cannot run.
``library.py`` holds a SYNCHRONOUS service-role client while ``strategies.py`` awaits an
asynchronous one, so :class:`SyncServiceClient` is a thin sync facade over the SAME row store —
one world, two calling conventions, no second set of rows to keep in step.

Row-level security is NOT exercised: there is no PostgreSQL here. That is the strict direction —
enforcement has to be in the application layer, and a handler leaning on RLS alone fails here.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from starlette.routing import Match

from backend_app.backend.marketplace import errors
from backend_app.backend.marketplace import library_entries as le
from backend_app.backend.marketplace import listing_projection
from backend_app.backend.marketplace import subscriber_operation_guard as _guard
from backend_app.core.audit_trail import StrategyAuditAction
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.core.subscription_dependencies import (
    check_strategy_quota,
    require_marketplace_access,
)
from backend_app.main import app

from tests.sandbox_lifecycle.harness import SandboxDatabase

# ══════════════════════════════════════════════════════════════════════════
# Identities and identifiers
# ══════════════════════════════════════════════════════════════════════════

OWNER_ID = "aa000000-0000-4000-8000-000000000001"
SUBSCRIBER_ID = "bb000000-0000-4000-8000-000000000002"
#: A third identity that owns nothing here and holds no Subscription to the Listing. The caller
#: Requirement 21.4 is about, and the one a careless fix breaks: see
#: :class:`TestTheStrangerStillCannotTellExistenceApart`.
STRANGER_ID = "cd000000-0000-4000-8000-000000000009"

LISTING_ID = "cc000000-0000-4000-8000-000000000003"
OWNER_STRATEGY_ID = "dd000000-0000-4000-8000-000000000004"
OWNER_VERSION_ID = "ee000000-0000-4000-8000-000000000005"
OWNER_VERSION_LABEL = "3"
OWNER_MODEL_VERSION_ID = "ff000000-0000-4000-8000-000000000006"
OWNER_SIGNAL_ID = "a1000000-0000-4000-8000-000000000007"
SUBSCRIPTION_ID = "b1000000-0000-4000-8000-000000000008"
OWNER_NODE_ID = "node_zzq_entry"

OWNER_USER: Dict[str, Any] = {
    "id": OWNER_ID,
    "email": "listing-owner@test.example",
    "role": "authenticated",
    "access_token": "token-listing-owner",
    "app_metadata": {"role": "authenticated"},
    "user_metadata": {},
}

SUBSCRIBER_USER: Dict[str, Any] = {
    "id": SUBSCRIBER_ID,
    "email": "active-subscriber@test.example",
    "role": "authenticated",
    "access_token": "token-active-subscriber",
    "app_metadata": {"role": "authenticated"},
    "user_metadata": {},
}

#: Authenticated, and related to the seeded Listing in no way at all: not its author, and holding
#: no ``library_subscriptions`` row for it. Requirement 21.4 requires this caller's answer to stay
#: byte-identical to the answer for an identifier that exists in no tenant.
STRANGER_USER: Dict[str, Any] = {
    "id": STRANGER_ID,
    "email": "unrelated-stranger@test.example",
    "role": "authenticated",
    "access_token": "token-unrelated-stranger",
    "app_metadata": {"role": "authenticated"},
    "user_metadata": {},
}

#: Every module that holds its own reference to the request-scoped client factory. The same list
#: ``tests/sandbox_lifecycle/conftest.py`` patches — a module missing from it would silently reach
#: for a real database.
_CLIENT_SEAM_MODULES: Tuple[str, ...] = (
    "backend_app.core.dependencies",
    "backend_app.routers.strategies",
    "backend_app.backend.strategy_service",
    "backend_app.backend.signal_service",
    "backend_app.backend.backtest_service",
)


# ══════════════════════════════════════════════════════════════════════════
# The seed — a strategy that genuinely carries Protected_Logic
# ══════════════════════════════════════════════════════════════════════════
#
# Distinctive tokens throughout, so a leak is unambiguous rather than a coincidence with ordinary
# prose. ``listing_projection.protected_logic_tokens`` walks these documents' scalars with a
# three-character floor, which is why every value below is longer than that.

#: The owner's DAG, indicator parameters, risk configuration and bound ML model — the four things
#: task 17.5 requires the seed genuinely carry.
PROTECTED_STRATEGY_ROW: Dict[str, Any] = {
    "buy_logic": {
        OWNER_NODE_ID: {
            "indicator": "rsi_zzq_secret_period",
            "threshold": "27.5001",
            "operator": "crosses_above_zzq",
        }
    },
    "sell_logic": {
        "node_zzq_exit": {
            "indicator": "atr_zzq_secret_multiplier",
            "threshold": "3.2502",
            "operator": "crosses_below_zzq",
        }
    },
    "indicators": {
        "rsi_zzq_secret_period": 14,
        "atr_zzq_secret_multiplier": "3.2502",
        "ema_zzq_secret_window": 233,
    },
    "risk": {
        "stop_loss_pct_zzq_secret": "2.7503",
        "max_position_zzq_secret": 12500,
        "max_drawdown_pct_zzq_secret": "11.25",
    },
    "ml_model_path": "models/private/zzq_secret_model_artifact.pkl",
}

#: The owner's version documents. Separate from the strategy row because
#: ``listing_projection.VERSION_PROTECTED_LOGIC_COLUMNS`` names these two, and the oracle is fed
#: both halves.
PROTECTED_VERSION_ROW: Dict[str, Any] = {
    "blueprint": {
        "nodes": [
            {"id": OWNER_NODE_ID, "block_id": "rsi_zzq_secret_block"},
            {"id": "node_zzq_exit", "block_id": "atr_zzq_secret_block"},
        ],
        "edges": [{"from": OWNER_NODE_ID, "to": "node_zzq_exit", "id": "wire_zzq_1"}],
    },
    "graph_json": {"graph_zzq_hash": "graph_zzq_fingerprint_deadbeef"},
}


def protected_tokens() -> FrozenSet[str]:
    """The oracle's token set, computed from the same documents the world is seeded with.

    Computed rather than written out, so the check and the seed cannot drift apart: a token added
    to the seed is a token the containment assertions immediately look for.
    """
    return listing_projection.protected_logic_tokens(
        strategy_row=PROTECTED_STRATEGY_ROW,
        version_row=PROTECTED_VERSION_ROW,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _owner_strategy_row() -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": OWNER_STRATEGY_ID,
        "user_id": OWNER_ID,
        "name": "Momentum Breakout (the owner's)",
        "description": "The strategy behind the Listing the subscriber holds.",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "status": "stopped",
        "is_active": True,
        "archived_at": None,
        "created_at": "2026-05-01T00:00:00+00:00",
        "updated_at": "2026-06-01T00:00:00+00:00",
        "current_version_id": OWNER_VERSION_ID,
        "ml_model_version_id": OWNER_MODEL_VERSION_ID,
    }
    row.update(PROTECTED_STRATEGY_ROW)
    return row


def _owner_version_row() -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": OWNER_VERSION_ID,
        "strategy_id": OWNER_STRATEGY_ID,
        "user_id": OWNER_ID,
        "version": OWNER_VERSION_LABEL,
        "is_draft": False,
        "is_current": True,
        "lifecycle_state": "READY",
        "dag_hash": "zzq_dag_hash_00000001",
        "created_at": "2026-05-15T00:00:00+00:00",
    }
    row.update(PROTECTED_VERSION_ROW)
    return row


def _listing_row(*, source_cloning_enabled: bool = False) -> Dict[str, Any]:
    """The Listing, with the two embedded resources the Entitlement_Resolver's one round trip
    asks PostgREST for, and every Protected_Logic column of the backing strategy on top."""
    row: Dict[str, Any] = {
        "id": LISTING_ID,
        "author_id": OWNER_ID,
        "source_strategy_id": OWNER_STRATEGY_ID,
        "source_cloning_enabled": source_cloning_enabled,
        "name": "Momentum Breakout",
        "description": "A subscribed strategy card.",
        "category": "momentum",
        "difficulty": "intermediate",
        "tags": ["trend", "breakout"],
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "exchange_id": "binance",
        "price": "9.99",
        "price_minor": 999,
        "currency": "USD",
        "subscriber_count": 42,
        "avg_rating": 4.5,
        "rating_count": 12,
        "published_at": "2026-06-01T00:00:00+00:00",
        "verification_status": "verified",
        "is_active": True,
        "moderation_status": "approved",
        "clone_count": 0,
        "supported_timeframes": ["1h", "4h"],
        "market_type": "spot",
        "condition_count": 3,
        "marketplace_submissions": [{"submission_state": "PUBLISHED"}],
        "library_subscriptions": [_embedded_subscription()],
    }
    row.update(PROTECTED_STRATEGY_ROW)
    row.update(PROTECTED_VERSION_ROW)
    return row


def _embedded_subscription() -> Dict[str, Any]:
    """The caller's own Subscription row as the resolver's embed returns it: ``ACTIVE`` and
    unexpired, which is the precondition Requirement 12.7 is scoped to."""
    return {
        "id": SUBSCRIPTION_ID,
        "user_id": SUBSCRIBER_ID,
        "status": "active",
        "period_expiry": (_now() + timedelta(days=20)).isoformat(),
    }


def _subscription_row() -> Dict[str, Any]:
    """The ``library_subscriptions`` row ``my_strategies`` reads, with the Listing embedded."""
    row = dict(_embedded_subscription())
    row.update(
        {
            "library_id": LISTING_ID,
            "renewal_enabled": True,
            "library_strategies": _listing_row(),
        }
    )
    return row


def _owner_signal_row() -> Dict[str, Any]:
    """One of the owner's signals, carrying exactly the internals Requirement 23.3 excludes.

    ``indicators``, ``market_info`` and ``ml_info`` are the three JSONB columns the design names,
    and ``risk_reason`` / ``exposure`` / ``expected_loss`` / ``expected_reward`` are the risk-rule
    internals. Every one of them is populated, so "the subscriber's view has none of them" is a
    statement about a signal that does.
    """
    return {
        "id": OWNER_SIGNAL_ID,
        "user_id": OWNER_ID,
        "strategy_id": OWNER_STRATEGY_ID,
        "strategy_version": OWNER_VERSION_LABEL,
        "deployment_id": None,
        "exchange_id": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "worker_id": "worker-1",
        "decision": "BUY",
        "side": "BUY",
        "status": "executed",
        "order_lifecycle_state": "EXECUTED",
        "risk_passed": True,
        "risk_reason": "within limits: max_drawdown_pct_zzq_secret not breached",
        "position_size": "0.25",
        "capital": "1000.00",
        "exposure": "250.00",
        "expected_loss": "27.50",
        "expected_reward": "82.50",
        "order_id": "order-zzq-1",
        "order_status": "filled",
        "quantity": "0.25",
        "filled": "0.25",
        "remaining": "0",
        "average_price": "43000.00",
        "fees": "0.43",
        "slippage": "0.01",
        "latency_ms": 42,
        "trade_id": "trade-zzq-1",
        "pnl": "12.00",
        "realized_pnl": "12.00",
        "generated_at": "2026-06-20T10:00:00+00:00",
        "risk_evaluated_at": "2026-06-20T10:00:01+00:00",
        "order_updated_at": "2026-06-20T10:00:02+00:00",
        "executed_at": "2026-06-20T10:00:03+00:00",
        "signal_source": "dag_engine",
        "indicators": dict(PROTECTED_STRATEGY_ROW["indicators"]),
        "market_info": {"regime": "trending_zzq", "atr": "3.2502"},
        "ml_info": {
            "model_path": PROTECTED_STRATEGY_ROW["ml_model_path"],
            "features": {"rsi_zzq_secret_period": 61.2},
            "inference": {"score": "0.81", "node": OWNER_NODE_ID},
        },
    }


def _model_version_row() -> Dict[str, Any]:
    return {
        "id": OWNER_MODEL_VERSION_ID,
        "user_id": OWNER_ID,
        "strategy_id": OWNER_STRATEGY_ID,
        "status": "READY",
        "artifact_uri": PROTECTED_STRATEGY_ROW["ml_model_path"],
        "artifact_checksum": "sha256:zzqsecretchecksum",
        "artifact_bytes": 4096,
        "hyperparameters": {"rsi_zzq_secret_period": 14},
        "created_at": "2026-05-20T00:00:00+00:00",
    }


# ══════════════════════════════════════════════════════════════════════════
# The synchronous facade over the one row store
# ══════════════════════════════════════════════════════════════════════════


class _SyncResult:
    def __init__(self, data: Any):
        self.data = data
        self.error = None


class _SyncQuery:
    """``SandboxQuery`` with a synchronous ``execute()`` and PostgREST's ``.single()`` shape.

    ``library.py``'s service-role client is the supabase-py SYNC client: ``clone_strategy`` reads
    ``lib_resp.data["author_id"]`` off a ``.single()``, and ``entitlement_resolver`` reads
    ``response.data`` without awaiting. Wrapping rather than re-implementing keeps ONE predicate
    engine: ``.eq``/``.in_``/``.is_`` mean the same thing to both callers, so a filter that selects
    the wrong rows for the async caller selects the wrong rows for the sync one too.
    """

    def __init__(self, query: Any):
        self._query = query
        self._single = False

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._query, name)
        if not callable(attribute):
            return attribute

        def _chained(*args: Any, **kwargs: Any) -> Any:
            outcome = attribute(*args, **kwargs)
            return self if outcome is self._query else outcome

        return _chained

    @property
    def not_(self) -> "_SyncQuery":
        self._query.not_
        return self

    def single(self) -> "_SyncQuery":
        self._single = True
        self._query.single()
        return self

    def maybe_single(self) -> "_SyncQuery":
        return self.single()

    def execute(self) -> _SyncResult:
        result = self._query.db.run(self._query)
        rows = result.data if isinstance(result.data, list) else result.data
        if self._single:
            return _SyncResult(rows[0] if rows else None)
        return _SyncResult(rows)


class SyncServiceClient:
    """The service-role client ``routers/library.py`` holds, over the shared row store."""

    def __init__(self, db: SandboxDatabase):
        self.db = db

    def table(self, name: str) -> _SyncQuery:
        return _SyncQuery(self.db.table(name))

    def from_(self, name: str) -> _SyncQuery:
        return self.table(name)


# ══════════════════════════════════════════════════════════════════════════
# The Audit_Log recorder
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AuditEntry:
    """One recorded Audit_Log write, with the instant it was recorded at.

    The instant is what makes Requirement 7.12's "within 5 seconds" measurable rather than
    assumed: it is read off the same monotonic clock the request start is.
    """

    action: Any
    at: float
    kwargs: Mapping[str, Any]

    @property
    def is_access_refusal(self) -> bool:
        return self.action == StrategyAuditAction.MARKETPLACE_ACCESS_REFUSED


class RecordingAuditLogger:
    """Stands in for ``StrategyAuditLogger`` at its own factory, so an entry written by ANY
    router is observed — including the entries that are not written at all, which is the half
    the registry below records."""

    def __init__(self) -> None:
        self.entries: List[AuditEntry] = []

    async def log(self, action: Any, **kwargs: Any) -> None:
        self.entries.append(
            AuditEntry(action=action, at=time.monotonic(), kwargs=dict(kwargs))
        )
        return None

    async def record_or_raise(self, action: Any, **kwargs: Any) -> None:
        return await self.log(action, **kwargs)

    def refusals(self) -> List[AuditEntry]:
        return [entry for entry in self.entries if entry.is_access_refusal]


# ══════════════════════════════════════════════════════════════════════════
# The world
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class World:
    db: SandboxDatabase
    audit: RecordingAuditLogger


def _reset_module_probes() -> None:
    """Forget every per-process verdict a previous test's answer would otherwise decide.

    The same six ``tests/sandbox_lifecycle/conftest.py`` resets, for the same reason: each is a
    module global holding the answer to a "does this migration's column exist?" probe, and a
    cached answer from another suite's double would decide this suite's reads.
    """
    from backend_app.backend import deployment_binding as db_binding
    from backend_app.backend import signal_service as svc
    from backend_app.backend import strategy_archive as archive
    from backend_app.core.rate_limit import limiter

    archive.reset_archive_column_support()
    db_binding.reset_binding_column_support()
    db_binding.reset_venue_timeframe_cache()
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()
    limiter.reset()


def install_world(monkeypatch) -> World:
    """Seed one row store and point every seam at it. Returns the :class:`World`.

    Extracted from the ``world`` fixture so a single test can build a SECOND, identically seeded
    world and re-point the seams at it. That is what
    :func:`test_the_owners_answer_is_identical_with_the_guard_disabled` needs: the same request,
    twice, against two freshly seeded stores — once with the subscriber guard live and once with
    it removed — which is the only way to state "an owner's behaviour did not change" as an
    equality rather than as a claim. Re-``setattr``ing through the same ``monkeypatch`` simply
    overwrites the earlier binding and is undone once at teardown.
    """
    import importlib

    from backend_app.routers import library as library_router

    _reset_module_probes()

    db = SandboxDatabase()
    db.seed("strategies", [_owner_strategy_row()])
    db.seed("strategy_versions", [_owner_version_row()])
    db.seed("library_strategies", [_listing_row()])
    db.seed("library_subscriptions", [_subscription_row()])
    db.seed("model_versions", [_model_version_row()])
    db.seed("signals", [_owner_signal_row()])
    db.seed("paper_sessions", [])

    async def _async_client(_token: Any = None) -> SandboxDatabase:
        return db

    for module_path in _CLIENT_SEAM_MODULES:
        module = importlib.import_module(module_path)
        monkeypatch.setattr(
            module, "create_request_supabase_async", _async_client, raising=False
        )

    sync_client = SyncServiceClient(db)
    monkeypatch.setattr(library_router, "_build_service_client", lambda: sync_client)
    monkeypatch.setattr(library_router, "_get_service_client", lambda: sync_client)

    # The Audit_Log factory, at the source module AND at every module that holds its own binding
    # to the name. The source patch catches the lazy importers (`eligibility_gate`,
    # `submission_service`, `execution_environment`, all of which import it inside the function);
    # the other two do `from … import get_strategy_audit_logger` at module scope and would
    # otherwise keep the real one.
    #
    # `routers/strategies.py` and `routers/strategy_operations.py` are deliberately NOT in this
    # list, and adding them would be a no-op: neither imports the name. Their refusals are
    # audited through `subscriber_operation_guard._refuse`, which imports
    # `library._audit_marketplace_refusal` lazily and therefore reads
    # `backend_app.routers.library.get_strategy_audit_logger` — patched above — at call time.
    # There is ONE MARKETPLACE_ACCESS_REFUSED writer in this codebase, so one patch observes
    # every entry, whichever router the refusal came from.
    audit = RecordingAuditLogger()
    for module_path in (
        "backend_app.core.audit_trail",
        "backend_app.routers.library",
        "backend_app.backend.strategy_lifecycle",
    ):
        module = importlib.import_module(module_path)
        monkeypatch.setattr(module, "get_strategy_audit_logger", lambda: audit)

    # The request-scoped client dependency is installed HERE rather than in `_overrides` so that
    # installing a second world re-points it too. Left in `_overrides` it would keep answering
    # with the first world's row store while the module seams answered with the second's — one
    # request reading two databases, which is the sort of harness fault that makes a comparison
    # look meaningful when it is not.
    app.dependency_overrides[get_request_supabase] = lambda: db

    return World(db=db, audit=audit)


@pytest.fixture
def world(monkeypatch) -> World:
    """One row store, both calling conventions, every seam this file needs and no more."""
    try:
        yield install_world(monkeypatch)
    finally:
        _reset_module_probes()


class CallerClient(TestClient):
    """A ``TestClient`` that re-installs ITS OWN caller identity on every request.

    Why not simply set ``app.dependency_overrides[get_current_user]`` in each fixture: the
    override map belongs to the application, not to a client, so two client fixtures in one test
    would silently share whichever identity was installed last — and the owner/subscriber
    comparison in :class:`TestTheOwnerIsNotRefused` would then be a caller compared with itself.
    Re-installing per request makes the two identities coexist, which is the one variable this
    file is about.
    """

    def __init__(self, caller: Mapping[str, Any], **kwargs: Any) -> None:
        super().__init__(app, raise_server_exceptions=False, **kwargs)
        self._caller = dict(caller)

    def request(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        app.dependency_overrides[get_current_user] = lambda: dict(self._caller)
        return super().request(*args, **kwargs)


@pytest.fixture
def _overrides(world: World):
    """The dependency overrides that are the same for every caller.

    Two here, plus the request-scoped client dependency :func:`install_world` installs: the
    strategy save-quota gate and the marketplace-feature gate, each of which reaches a
    subscription store this host does not have. The AUTHORISATION under test is the ownership
    predicate inside each handler, and none of the three touches it.
    """
    assert world.db is not None
    app.dependency_overrides[check_strategy_quota] = lambda: None
    app.dependency_overrides[require_marketplace_access] = lambda: True
    try:
        yield
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def subscriber_client(_overrides) -> CallerClient:
    """The caller Requirement 12.7 is about: authenticated, and holding an ``ACTIVE``
    Subscription to the Listing that owns the strategy being probed."""
    return CallerClient(SUBSCRIBER_USER)


@pytest.fixture
def owner_client(_overrides) -> CallerClient:
    """The positive control. Same app, same rows, one identity swapped."""
    return CallerClient(OWNER_USER)


@pytest.fixture
def stranger_client(_overrides) -> CallerClient:
    """The caller Requirement 21.4 is about. Same app, same rows, a third identity."""
    return CallerClient(STRANGER_USER)


# ══════════════════════════════════════════════════════════════════════════
# The thirteen probes
# ══════════════════════════════════════════════════════════════════════════

_STRATEGIES = "backend_app.routers.strategies"
_OPS = "backend_app.routers.strategy_operations"


@dataclass(frozen=True)
class Probe:
    """One restricted operation, and everything needed to attempt it over HTTP."""

    #: The action name, which MUST be a member of ``le.SUBSCRIBER_FORBIDDEN_ACTIONS``.
    action: str
    #: Distinguishes two probes of the same action. Used in the pytest id.
    variant: str
    method: str
    #: The path as the real app has it mounted, ``{}``-formatted from :data:`IDS`.
    path: str
    #: ``module:function`` of the handler this probe believes answers. Asserted, because
    #: ``/api/strategies/{id}`` is declared by two modules and first-match-wins.
    handler: str
    query: Mapping[str, str] = None
    body: Optional[Mapping[str, Any]] = None
    #: Why this route is the one through which this action would be performed.
    rationale: str = ""
    #: ``False`` when the OWNER's own answer is legitimately the same as the subscriber's, so no
    #: positive control can be drawn from this route. Always paired with a stated reason.
    owner_control: bool = True
    owner_control_note: str = ""

    @property
    def key(self) -> str:
        return f"{self.action}.{self.variant}"


IDS: Dict[str, str] = {
    "strategy_id": OWNER_STRATEGY_ID,
    "version": OWNER_VERSION_LABEL,
    "node_id": OWNER_NODE_ID,
    "model_version_id": OWNER_MODEL_VERSION_ID,
    "signal_id": OWNER_SIGNAL_ID,
    "library_id": LISTING_ID,
}


#: The thirteen, mapped onto the concrete routes a subscriber would reach for.
#:
#: Several actions share a route, and that is a fact about the platform rather than a shortcut
#: here: this repository's strategy surface is coarse-grained. ONE read of
#: ``GET /api/strategies/{id}`` returns the graph, the indicator parameters, the risk
#: configuration and the model path together, and ONE ``PUT`` edits all four — there is no
#: per-facet route to probe separately, and there is no dedicated export or download route for a
#: strategy definition at all. So the mapping names, for each action, the route through which that
#: action is actually performed today, with the request body that performs it. Where two actions
#: land on one route they are still probed separately, because the bodies differ and a future fix
#: could refuse one and admit the other.
PROBES: Tuple[Probe, ...] = (
    Probe(
        action="edit",
        variant="update",
        method="PUT",
        path="/api/strategies/{strategy_id}",
        handler=f"{_STRATEGIES}:update_strategy",
        body={"name": "renamed by a subscriber"},
        rationale="the edit path; Requirement 7.8 names edit and rename together",
    ),
    Probe(
        action="edit",
        variant="rename",
        method="PUT",
        path="/api/strategies/{strategy_id}/rename",
        handler=f"{_STRATEGIES}:rename_strategy",
        body={"name": "renamed by a subscriber"},
        rationale="the dedicated rename route Requirement 7.8 names explicitly",
    ),
    Probe(
        action="open_in_builder",
        variant="load",
        method="GET",
        path="/api/strategies/{strategy_id}",
        handler=f"{_STRATEGIES}:get_strategy_route",
        rationale=(
            "opening the Strategy Builder IS this read: the canvas is rebuilt from the row's "
            "lifted DAG fields, so admitting it hands over the definition"
        ),
    ),
    Probe(
        action="view_graph",
        variant="version_history",
        method="GET",
        path="/api/strategies/{strategy_id}/versions",
        handler=f"{_OPS}:get_version_history",
        rationale="each version carries its blueprint and canvas block — the graph, per version",
    ),
    Probe(
        action="edit_blocks",
        variant="update_logic",
        method="PUT",
        path="/api/strategies/{strategy_id}",
        handler=f"{_STRATEGIES}:update_strategy",
        body={
            "buy_logic": {"node_injected_by_subscriber": {"indicator": "sma", "threshold": 1}}
        },
        rationale="a block-level edit is a write of the logic columns through the same route",
    ),
    Probe(
        action="view_indicator_params",
        variant="node_preview",
        method="POST",
        path="/api/strategy-operations/strategies/{strategy_id}/nodes/{node_id}/preview",
        handler=f"{_OPS}:preview_node",
        body={},
        rationale=(
            "previewing one of the owner's nodes evaluates it and returns its indicator values "
            "and parameters"
        ),
    ),
    Probe(
        action="edit_indicator_params",
        variant="update_indicators",
        method="PUT",
        path="/api/strategies/{strategy_id}",
        handler=f"{_STRATEGIES}:update_strategy",
        body={"indicators": {"rsi_period": 7}},
        rationale="the indicator parameters live in the row's ``indicators`` column",
    ),
    Probe(
        action="view_risk_config",
        variant="risk_metrics",
        method="GET",
        path="/api/strategies/{strategy_id}/risk-metrics",
        handler=f"{_OPS}:get_strategy_risk_metrics",
        rationale="the risk surface of the owner's strategy, derived from its risk configuration",
        owner_control=False,
        owner_control_note=(
            "This route resolves NO ownership, so the owner's answer and the subscriber's are "
            "the same answer and no control can be drawn from them: "
            "strategy_operations.get_strategy_risk_metrics reads ``strategy_risk`` by "
            "``strategy_id`` alone and never filters on the caller. It is not that the route "
            "fails to reach the seeded row - it reaches it for BOTH callers. (Until the "
            "signature mismatch was fixed the two answers were identical for a different "
            "reason: the call raised before any read. Fixing that changed which fact makes the "
            "control unavailable, not whether it is available.) The missing refusal is recorded "
            "in this probe's PENDING entry; the companion ``view_risk_config.strategy_read`` "
            "probe below carries the control for this action."
        ),
    ),
    Probe(
        action="view_risk_config",
        variant="strategy_read",
        method="GET",
        path="/api/strategies/{strategy_id}",
        handler=f"{_STRATEGIES}:get_strategy_route",
        rationale=(
            "the risk configuration itself is the row's ``risk`` column, so reading the row "
            "reads the configuration"
        ),
    ),
    Probe(
        action="edit_risk_config",
        variant="update_risk",
        method="PUT",
        path="/api/strategies/{strategy_id}",
        handler=f"{_STRATEGIES}:update_strategy",
        body={"risk": {"stop_loss_pct": 99}},
        rationale="the risk configuration lives in the row's ``risk`` column",
    ),
    Probe(
        action="export_definition",
        variant="version_compare",
        method="POST",
        path="/api/strategies/{strategy_id}/versions/compare",
        handler=f"{_OPS}:compare_versions",
        query={"version_a": "{version}", "version_b": "{version}"},
        rationale=(
            "the comparison returns two versions' blueprints in one response — an export of the "
            "definition in all but name"
        ),
    ),
    Probe(
        action="download_definition",
        variant="strategy_read",
        method="GET",
        path="/api/strategies/{strategy_id}",
        handler=f"{_STRATEGIES}:get_strategy_route",
        rationale=(
            "there is no dedicated download route; a definition is downloaded by reading the "
            "row, so this read is the operation"
        ),
    ),
    Probe(
        action="view_model_params",
        variant="model_artifact_link",
        method="GET",
        path="/api/strategy-operations/models/{model_version_id}/download",
        handler=f"{_OPS}:create_model_artifact_link",
        rationale="the bound ML model's artifact and its hyperparameters",
    ),
    Probe(
        action="re_version",
        variant="restore_version",
        method="POST",
        path="/api/strategies/{strategy_id}/versions/restore",
        handler=f"{_OPS}:restore_version",
        query={"version": "{version}"},
        rationale="restoring a prior version mints a NEW version of the owner's strategy",
    ),
    Probe(
        action="delete",
        variant="archive",
        method="DELETE",
        path="/api/strategies/{strategy_id}",
        handler=f"{_STRATEGIES}:delete_strategy",
        rationale="the delete action, which this platform implements as a soft archive",
    ),
)


def url_for(probe: Probe) -> str:
    return probe.path.format(**IDS)


def params_for(probe: Probe) -> Dict[str, str]:
    return {k: v.format(**IDS) for k, v in (probe.query or {}).items()}


def attempt(client: TestClient, probe: Probe):
    return client.request(
        probe.method,
        url_for(probe),
        params=params_for(probe),
        json=dict(probe.body) if probe.body is not None else None,
    )


#: Keys the platform's error envelope fills per request, and which therefore differ between two
#: otherwise identical answers.
#:
#: ``path`` earns its place twice over. It differs per request, and it is the caller's OWN request
#: line echoed back — which matters for the containment oracle: the node-preview probe names a node
#: in its URL, so the echoed path contains a token the oracle harvests from the seeded blueprint.
#: A caller cannot be leaked something they themselves supplied, and
#: ``tests/test_marketplace_error_surface.py`` makes exactly the same allowance for the identifier
#: it puts in the path. Every OTHER field of the body is asserted, so nothing the server chose to
#: say is exempt.
_VOLATILE_BODY_KEYS: FrozenSet[str] = frozenset(
    {"timestamp", "path", "request_id", "audit_id", "as_of"}
)


def normalised(payload: Any) -> Any:
    """``payload`` with every :data:`_VOLATILE_BODY_KEYS` key removed at any depth."""
    if isinstance(payload, dict):
        return {
            key: normalised(value)
            for key, value in payload.items()
            if key not in _VOLATILE_BODY_KEYS
        }
    if isinstance(payload, list):
        return [normalised(item) for item in payload]
    return payload


def observable(response: Any) -> Tuple[int, Any]:
    """The part of an answer two callers can be compared on."""
    try:
        body = response.json() if response.content else None
    except ValueError:  # pragma: no cover - a non-JSON body is itself a finding
        body = response.text
    return response.status_code, normalised(body)


def resolve(method: str, path: str):
    """Every route that FULLY matches, in the app's own declaration order."""
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }
    return [
        route
        for route in app.routes
        if route.matches(scope)[0] == Match.FULL
    ]


# ══════════════════════════════════════════════════════════════════════════
# The pending registry — known Requirement 12.7 defects, per clause
# ══════════════════════════════════════════════════════════════════════════

STATUS = "status"    # answers 403, not 404 and not a success
CODE = "code"        # carries a stable machine-readable code
AUDIT = "audit"      # writes a MARKETPLACE_ACCESS_REFUSED entry within 5 seconds
LEAK = "leak"        # carries no Protected_Logic


@dataclass(frozen=True)
class Pending:
    """A probe that does not yet satisfy Requirement 12.7, and why."""

    clauses: FrozenSet[str]
    owner: str
    defect: str


#: WHAT THIS REGISTRY HELD, AND WHAT REMAINS
#: -----------------------------------------
#: It held FOURTEEN entries of one shape: a read or an UPDATE filtered
#: ``.eq("id", …).eq("user_id", caller)`` whose empty result was reported as non-existence — a
#: 404, or a 200 carrying an empty collection for the version history. All fourteen are now
#: FIXED, by task 17.9's ``marketplace/subscriber_operation_guard.py`` and the nine call sites
#: that invoke it in ``routers/strategies.py`` and ``routers/strategy_operations.py``, and their
#: entries have been DELETED — which is this registry's own mechanism for "this defect is
#: fixed", ``xfail(strict=True)`` having turned each into an XPASS failure the moment it started
#: answering 403. Nothing was loosened to get there.
#:
#: ONE entry remains, and it is a different defect with a different fix:
#: ``view_risk_config.risk_metrics`` reads a telemetry table keyed on ``strategy_id`` with no
#: ownership predicate and no RLS behind it, so the three-way resolution the other fourteen use
#: — which refines a refusal the owner-scoped predicate had ALREADY decided — has nothing to
#: refine. It is deliberately left registered and its route untouched.
#:
#: The fourteen were "unowned" while they stood: ``design.md`` → "Server-side artifact
#: resolution" specified the change but no task carried it. Task 17.9 now does.
_ALL_THREE = frozenset({STATUS, CODE, AUDIT})
_UNOWNED = (
    "unowned (design.md → Server-side artifact resolution; no task in tasks.md implements it)"
)

PENDING: Mapping[str, Pending] = {
    "view_risk_config.risk_metrics": Pending(
        clauses=_ALL_THREE,
        owner=_UNOWNED,
        defect=(
            'answers 200 {"strategy_id": …, "risk_metrics": {…}} — the OWNER\'s own risk read, '
            "to a subscriber (``risk_metrics`` is empty in this environment, which runs no "
            "QuestDB; the figures are there wherever the telemetry store is). This entry used "
            "to describe TWO defects in sequence; the first is "
            "gone. ``strategy_operations.get_strategy_risk_metrics`` called "
            "``MetricsService.get_risk_metrics(user=…)`` against a method declared "
            "``(self, user_id, strategy_id)``, so the route raised ``TypeError`` and answered "
            "500 to every caller, owner included, before reaching any ownership decision — and "
            "echoed ``str(e)`` in the body. That call site now passes ``user_id=user['id']`` "
            "and the body carries a fixed sentence "
            "(``tests/test_strategy_risk_metrics_endpoint.py`` is its revert-detector). "
            "WHAT REMAINS is the second defect, and it is the Requirement 12.7 gap: the route "
            "resolves no ownership at all. ``MetricsService.get_risk_metrics`` takes the "
            "caller's id and does not use it — the QuestDB read is keyed on ``strategy_id`` "
            "alone — so a subscriber is answered the owner's drawdown, exposure, position "
            "limits and kill-switch state rather than 403 with "
            "``MARKETPLACE_OPERATION_NOT_PERMITTED`` and an audited refusal. This one is the "
            "worst-answering of the fifteen: the other fourteen fabricated a 404, this one "
            "discloses. It is a read of a telemetry table, not of ``strategies``, so no RLS "
            "predicate stands behind the missing handler-level check — and no owner-scoped "
            "refusal for task 17.9's guard to refine either, which is why that fix does not "
            "reach this route and this entry stays"
        ),
    ),
}


def _cases(clause: str):
    """``pytest.param`` per probe, ``xfail(strict=True)`` where :data:`PENDING` says so."""
    params = []
    for probe in PROBES:
        pending = PENDING.get(probe.key)
        marks: Any = ()
        if pending is not None and clause in pending.clauses:
            marks = pytest.mark.xfail(
                strict=True,
                reason=(
                    f"{probe.key} does not yet satisfy Requirement 12.7 ({clause}); "
                    f"owner: {pending.owner}. Defect: {pending.defect}"
                ),
            )
        params.append(pytest.param(probe, marks=marks, id=probe.key))
    return params


# ══════════════════════════════════════════════════════════════════════════
# 0. The probe surface is real and the seed is not empty
# ══════════════════════════════════════════════════════════════════════════


class TestTheProbeSurfaceIsReal:
    """Guards on the guards. Every one of these can make the rest of the file vacuous."""

    def test_the_probes_cover_exactly_the_thirteen_forbidden_actions(self) -> None:
        """The list is task 17.4's, not a second copy: Requirement 12.4's thirteen are the
        operations Requirement 12.7 requires the server refuse."""
        covered = {probe.action for probe in PROBES}
        forbidden = set(le.SUBSCRIBER_FORBIDDEN_ACTIONS)
        assert covered == forbidden, (
            "the probe set and Requirement 12.4's forbidden-action list disagree.\n"
            f"  probed but not forbidden: {sorted(covered - forbidden)}\n"
            f"  forbidden but not probed: {sorted(forbidden - covered)}"
        )
        assert len(le.SUBSCRIBER_FORBIDDEN_ACTIONS) == 13

    def test_no_probe_names_a_permitted_action(self) -> None:
        """A probe for a permitted action would assert the opposite of Requirement 12.3."""
        overlap = {probe.action for probe in PROBES} & set(le.SUBSCRIBER_PERMITTED_ACTIONS)
        assert not overlap, f"probes name permitted action(s) {sorted(overlap)}"

    def test_the_seed_carries_protected_logic_to_leak(self) -> None:
        """The containment oracle is only meaningful if it has something to look for."""
        tokens = protected_tokens()
        assert tokens, "the seeded strategy carries no Protected_Logic tokens at all"
        for expected in (
            OWNER_NODE_ID,
            "rsi_zzq_secret_period",
            "graph_zzq_fingerprint_deadbeef",
        ):
            assert expected in tokens, (
                f"{expected!r} is seeded but is not in the oracle's token set, so a response "
                f"carrying it would not be noticed"
            )

    def test_the_seeded_strategy_carries_all_four_kinds_of_protected_logic(self) -> None:
        """Task 17.5's non-vacuity requirement, asserted rather than assumed."""
        row = _owner_strategy_row()
        assert row["buy_logic"] and row["sell_logic"], "no DAG nodes"
        assert row["indicators"], "no indicator parameters"
        assert row["risk"], "no risk configuration"
        assert row["ml_model_path"], "no bound ML model"
        assert _owner_version_row()["blueprint"]["nodes"], "no version blueprint"

    @pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.key)
    def test_the_path_resolves_on_the_real_app(self, probe: Probe) -> None:
        matched = resolve(probe.method, url_for(probe))
        assert matched, (
            f"{probe.key}: {probe.method} {probe.path} resolves to NO route on the real app, so "
            f"probing it would assert nothing"
        )

    @pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.key)
    def test_it_is_served_by_the_handler_the_probe_names(self, probe: Probe) -> None:
        """First-match-wins, and ``/api/strategies/{id}`` is declared by two modules."""
        served = resolve(probe.method, url_for(probe))[0].endpoint
        name = f"{served.__module__}:{getattr(served, '__name__', served)}"
        assert name == probe.handler, (
            f"{probe.key}: {probe.method} {probe.path} is served by {name}, not by "
            f"{probe.handler}. A shadowed route cannot pass as tested."
        )

    def test_the_pending_registry_names_only_real_probes(self) -> None:
        known = {probe.key for probe in PROBES}
        unknown = sorted(set(PENDING) - known)
        assert not unknown, (
            f"PENDING names probes that do not exist: {unknown}. Delete the stale entries."
        )
        for key, pending in PENDING.items():
            assert pending.clauses <= {STATUS, CODE, AUDIT, LEAK}, (
                f"PENDING[{key!r}] names a clause this file does not check: "
                f"{sorted(pending.clauses)}"
            )


# ══════════════════════════════════════════════════════════════════════════
# 1. The subscriber CAN reach what they are permitted (non-vacuity)
# ══════════════════════════════════════════════════════════════════════════


class TestThePermittedOperationsAreReachable:
    """If every request from this caller failed, the thirteen refusals would prove nothing.

    Same client, same token, same rows, same request graph — only the operation differs.
    """

    def test_the_subscriber_sees_the_strategy_on_their_own_strategies_page(
        self, subscriber_client: TestClient
    ) -> None:
        """This is precisely why a 404 is the wrong answer: the strategy is right here."""
        response = subscriber_client.get("/api/library/my-strategies")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["subscribed_total"] == 1, body
        entry = next(e for e in body["items"] if e["ownership"] == "SUBSCRIBED")
        assert entry["subscription"]["state"] == "ACTIVE"
        assert entry["entitling"] is True, entry

    def test_that_entry_offers_the_permitted_actions_and_none_of_the_thirteen(
        self, subscriber_client: TestClient
    ) -> None:
        body = subscriber_client.get("/api/library/my-strategies").json()
        entry = next(e for e in body["items"] if e["ownership"] == "SUBSCRIBED")
        actions = set(entry["allowed_actions"])
        assert actions, "the entry offers no action at all"
        assert not actions & set(le.SUBSCRIBER_FORBIDDEN_ACTIONS)
        assert actions <= set(le.SUBSCRIBER_PERMITTED_ACTIONS)

    def test_the_subscriber_can_deploy_the_listing_they_are_entitled_to(
        self, subscriber_client: TestClient, world: World
    ) -> None:
        """The permitted execution path of Requirement 7.5, through the real route."""
        response = subscriber_client.post(
            "/api/library/{}/deploy".format(LISTING_ID),
            json={"symbol": "BTC/USDT", "timeframe": "1h", "capital": 500},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["granted_via"] == "SUBSCRIBED"
        assert body["deployment_id"]
        # The subscriber-safe path writes a deployment bound to the owner's version and creates no
        # `strategies` row (task 17.3, Requirement 7.1) — so the permitted path is genuinely
        # permitted AND still holds the line this file is about.
        assert len(world.db.rows("strategy_deployments")) == 1
        assert world.db.rows("strategies") == [_owner_strategy_row()]

    def test_the_permitted_paths_disclose_no_protected_logic_either(
        self, subscriber_client: TestClient
    ) -> None:
        """Requirement 7.1 is about EVERY response, not only the refusals."""
        tokens = protected_tokens()
        listing = subscriber_client.get("/api/library/my-strategies")
        listing_projection.assert_contains_no_protected_logic(listing.json(), tokens)
        deployment = subscriber_client.post(
            "/api/library/{}/deploy".format(LISTING_ID), json={"symbol": "BTC/USDT"}
        )
        listing_projection.assert_contains_no_protected_logic(deployment.json(), tokens)


# ══════════════════════════════════════════════════════════════════════════
# 2. All thirteen answer 403 with a stable code, not 404 (Requirement 12.7)
# ══════════════════════════════════════════════════════════════════════════


def _code_of(body: Any) -> str:
    """The stable code a structured body carries, or ``""``.

    A ``MarketplaceError`` answers ``{"error": {"code": …}, …}``. An ``HTTPException`` puts either
    a sentence or a legacy ``{"error": "CODE"}`` dict in ``detail``; both are read here, so a
    route that already carries a stable code passes without being rewritten to the marketplace
    envelope, and a route that carries only a sentence reads as "no stable code".
    """
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    if isinstance(error, str):
        return error
    detail = body.get("detail")
    if isinstance(detail, dict):
        inner = detail.get("error")
        if isinstance(inner, dict) and isinstance(inner.get("code"), str):
            return inner["code"]
        if isinstance(inner, str):
            return inner
    return ""


@pytest.mark.parametrize("probe", _cases(STATUS))
def test_a_restricted_operation_answers_403_and_never_404(
    probe: Probe, subscriber_client: TestClient, world: World
) -> None:
    """Requirement 12.7's status, for a caller holding an ``ACTIVE`` Subscription.

    403, not 404: the subscriber can see this strategy on their Strategies page (asserted above),
    so "no such strategy" is a false statement about a resource this caller demonstrably knows
    exists. And not a success either — the operation must not be performed.
    """
    response = attempt(subscriber_client, probe)

    assert response.status_code != 404, (
        f"{probe.key}: {probe.method} {url_for(probe)} answered 404 to a subscriber holding an "
        f"ACTIVE Subscription to the Listing that owns this strategy. Requirement 12.7 requires "
        f"403 with a stable code: the strategy DOES exist and this caller can see it on their "
        f"Strategies page, so 404 is a fabricated fact. Body: {response.text[:400]!r}"
    )
    assert response.status_code == 403, (
        f"{probe.key}: {probe.method} {url_for(probe)} answered {response.status_code}, not 403. "
        f"({probe.rationale}.) Body: {response.text[:400]!r}"
    )


@pytest.mark.parametrize("probe", _cases(CODE))
def test_a_restricted_operation_carries_a_stable_machine_readable_code(
    probe: Probe, subscriber_client: TestClient
) -> None:
    """The catalogue's code for this refusal is ``MARKETPLACE_OPERATION_NOT_PERMITTED`` (403).

    A sentence is not a code: a client that must render Requirement 12.8's
    ``expired-subscription`` and ``unavailable-strategy`` states apart from "not permitted on a
    subscribed strategy" has to branch on something stable.
    """
    response = attempt(subscriber_client, probe)
    code = _code_of(response.json() if response.content else None)

    assert code == errors.MARKETPLACE_OPERATION_NOT_PERMITTED, (
        f"{probe.key}: answered {response.status_code} carrying code {code!r}, not the stable "
        f"{errors.MARKETPLACE_OPERATION_NOT_PERMITTED!r}. Body: {response.text[:400]!r}"
    )
    assert response.status_code in errors.ALLOWED_HTTP_STATUS_FOR_CODE.get(
        errors.MARKETPLACE_OPERATION_NOT_PERMITTED,
        frozenset({errors.HTTP_STATUS_FOR_CODE[errors.MARKETPLACE_OPERATION_NOT_PERMITTED]}),
    ), (
        f"{probe.key}: carried {code!r} with status {response.status_code}, which the catalogue "
        f"does not permit for that code"
    )


@pytest.mark.parametrize("probe", _cases(LEAK))
def test_a_restricted_operation_discloses_no_protected_logic(
    probe: Probe, subscriber_client: TestClient
) -> None:
    """Requirement 7.1's second half: Protected_Logic is omitted from EVERY field of EVERY
    response returned to a non-owner, "including error and diagnostic responses"."""
    response = attempt(subscriber_client, probe)
    _status, body = observable(response)
    listing_projection.assert_contains_no_protected_logic(body, protected_tokens())


@pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.key)
def test_a_restricted_operation_changes_nothing(
    probe: Probe, subscriber_client: TestClient, world: World
) -> None:
    """A refusal must leave the owner's rows exactly as they were.

    Asserted on the ROWS, not on a call count: an endpoint that answered 403 and wrote anyway
    would pass a mock-based check.
    """
    before = {
        table: world.db.rows(table)
        for table in ("strategies", "strategy_versions", "model_versions", "signals")
    }
    attempt(subscriber_client, probe)
    after = {table: world.db.rows(table) for table in before}
    assert after == before, (
        f"{probe.key}: the refused operation changed the owner's rows.\n"
        f"  before: {before}\n  after:  {after}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 3. The owner is not refused, so the refusals are about something real
# ══════════════════════════════════════════════════════════════════════════


class TestTheOwnerIsNotRefused:
    """The positive control for the read probes: the seeded row IS reachable through them."""

    @pytest.mark.parametrize(
        "probe",
        [p for p in PROBES if p.method == "GET" and p.owner_control],
        ids=lambda p: p.key,
    )
    def test_the_owner_reaches_the_route_a_subscriber_is_refused(
        self, probe: Probe, owner_client: CallerClient, subscriber_client: CallerClient
    ) -> None:
        """Compared on the NORMALISED answer: the envelope carries a per-request timestamp, and
        two 404s that differ only in that are the same answer."""
        as_owner = observable(attempt(owner_client, probe))
        as_subscriber = observable(attempt(subscriber_client, probe))
        assert as_owner != as_subscriber, (
            f"{probe.key}: the OWNER gets the same answer as the subscriber "
            f"({as_owner[0]}, {str(as_owner[1])[:200]!r}), so the refusal assertion for this "
            f"probe is vacuous — the route is not reaching the seeded row at all. Either the seed "
            f"is not reachable here, or this probe needs owner_control=False with a stated reason."
        )

    def test_every_probe_without_an_owner_control_says_why(self) -> None:
        """A probe may opt out of the control, but never silently."""
        for probe in PROBES:
            if not probe.owner_control:
                assert probe.owner_control_note, (
                    f"{probe.key} has owner_control=False and no stated reason"
                )

    def test_the_owners_own_read_really_does_carry_the_protected_logic(
        self, owner_client: TestClient
    ) -> None:
        """The other end of the containment claim: the definition IS on the wire for the owner,
        so its absence for the subscriber is a projection decision rather than an empty row."""
        response = owner_client.get("/api/strategies/{}".format(OWNER_STRATEGY_ID))
        assert response.status_code == 200, response.text
        assert OWNER_NODE_ID in response.text, (
            "the owner's own strategy read does not carry the seeded DAG node, so this file's "
            "containment assertions are about a row with nothing in it"
        )


# ══════════════════════════════════════════════════════════════════════════
# 3a. AN OWNER'S BEHAVIOUR DID NOT CHANGE — asserted per endpoint, as an equality
# ══════════════════════════════════════════════════════════════════════════
#
# Task 17.9 is an authorisation change on fourteen LIVE endpoints. The assertion that matters
# most is not that a subscriber is now refused; it is that nothing an owner sees moved. A
# regression here breaks working production functionality on the strategy CRUD, version and model
# surface, for every user of the platform.
#
# ``TestTheOwnerIsNotRefused`` above compares the owner with the SUBSCRIBER, which proves the
# route reaches the seeded row but says nothing about whether the owner's own answer changed. The
# two assertions below say exactly that, and they say it two independent ways:
#
#   1. **The guard is never invoked on an owner's request.** Recorded by a spy over the guard's
#      own entry points. This is the structural argument: the guard is the ONLY thing added to
#      these fourteen endpoints, so a request that never enters it cannot have been altered by
#      it. It also proves the placement claim — the guard sits in the branch where the handler
#      was ALREADY about to refuse, so an owner whose row exists never reaches it and pays none
#      of its round trips.
#   2. **The owner's answer is byte-identical with the guard removed.** The same request, twice,
#      against two freshly seeded row stores — once with the guard live, once with both entry
#      points replaced by a no-op. This is the behavioural argument, and it covers the writes
#      (PUT, DELETE, restore) as well as the reads.


class _GuardSpy:
    """Records every call into the guard, then delegates to the real thing.

    Installed over the guard MODULE's attributes, which is where both routers resolve the names
    from (each does ``from … import subscriber_operation_guard as _subscriber_guard`` and calls
    through the module), so one patch observes all nine call sites.
    """

    def __init__(self, monkeypatch) -> None:
        self.calls: List[str] = []
        real_strategy = _guard.refuse_if_entitled_subscriber
        real_model = _guard.refuse_if_entitled_subscriber_for_model_version

        async def _spy_strategy(user, **kwargs):
            self.calls.append("refuse_if_entitled_subscriber")
            return await real_strategy(user, **kwargs)

        async def _spy_model(user, **kwargs):
            self.calls.append("refuse_if_entitled_subscriber_for_model_version")
            return await real_model(user, **kwargs)

        monkeypatch.setattr(_guard, "refuse_if_entitled_subscriber", _spy_strategy)
        monkeypatch.setattr(
            _guard, "refuse_if_entitled_subscriber_for_model_version", _spy_model
        )


def _disable_guard() -> Tuple[Any, Any]:
    """Replace both guard entry points with no-ops. Returns the originals, for restoration.

    A plain ``setattr`` pair rather than ``monkeypatch``, because the caller must restore ONLY
    these two while keeping every other seam the fixture installed in place; ``monkeypatch.undo``
    would revert the row-store seams too and leave the second request reading a real database.
    """
    originals = (
        _guard.refuse_if_entitled_subscriber,
        _guard.refuse_if_entitled_subscriber_for_model_version,
    )

    async def _noop(_user, **_kwargs):
        return None

    _guard.refuse_if_entitled_subscriber = _noop  # type: ignore[assignment]
    _guard.refuse_if_entitled_subscriber_for_model_version = _noop  # type: ignore[assignment]
    return originals


def _restore_guard(originals: Tuple[Any, Any]) -> None:
    _guard.refuse_if_entitled_subscriber = originals[0]  # type: ignore[assignment]
    _guard.refuse_if_entitled_subscriber_for_model_version = originals[1]  # type: ignore[assignment]


#: Keys whose value is minted fresh on every run of a mutating handler, and which therefore differ
#: between two runs of the SAME code against two identically seeded stores. Stripped from the
#: owner-unchanged comparison for that reason and no other: each is a value the server generates
#: from a clock or a UUID factory, so an inequality in one is a statement about the run, not about
#: the guard. Nothing that expresses an authorisation outcome — no status, no code, no message, no
#: field the caller supplied — is in this set.
_PER_RUN_KEYS: FrozenSet[str] = frozenset(
    {
        "updated_at",
        "created_at",
        # `DELETE /api/strategies/{id}` answers `{"status": "archived", "strategy_id": …,
        # "archived_at": …}`, and `archived_at` is `datetime.now()` at the moment of the write.
        "archived_at",
        "version_id",
        "new_version_id",
        "id",
        "expires_at",
        "url",
        "download_url",
        "signed_url",
    }
)


def _comparable(response: Any) -> Tuple[int, Any]:
    """:func:`observable`, with :data:`_PER_RUN_KEYS` removed at any depth as well."""

    def _strip(payload: Any) -> Any:
        if isinstance(payload, dict):
            return {
                key: _strip(value)
                for key, value in payload.items()
                if key not in _PER_RUN_KEYS
            }
        if isinstance(payload, list):
            return [_strip(item) for item in payload]
        return payload

    status, body = observable(response)
    return status, _strip(body)


class TestTheOwnerIsUnaffected:
    """Requirement 12.7 is scoped to a subscriber. This class is the proof it stayed scoped."""

    @pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.key)
    def test_the_guard_is_never_invoked_on_an_owners_request(
        self, probe: Probe, monkeypatch, _overrides, world: World
    ) -> None:
        """The structural half: an owner's request does not enter the new code at all.

        The guard is invoked from the branch each handler takes when its owner-scoped predicate
        matched nothing. For the OWNER that predicate matches, so the branch is not taken. A
        non-empty call list here would mean the guard had been placed BEFORE the ownership
        predicate rather than beside it — which is the shape that could change an owner's answer,
        and the shape that would also cost every owner two extra round trips per request.
        """
        spy = _GuardSpy(monkeypatch)
        response = attempt(CallerClient(OWNER_USER), probe)

        assert spy.calls == [], (
            f"{probe.key}: the subscriber guard was invoked {len(spy.calls)} time(s) on the "
            f"OWNER's own request ({spy.calls}). It must run only where the handler was already "
            f"about to refuse. The owner answered {response.status_code}."
        )

    @pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.key)
    def test_the_owners_answer_is_identical_with_the_guard_disabled(
        self, probe: Probe, monkeypatch, _overrides, world: World
    ) -> None:
        """The behavioural half, and the one that covers the writes.

        Two runs, two freshly seeded row stores, one variable: whether the guard exists. The
        guard-live run goes first so that the fixture's own world is the one measured under the
        code as shipped; the second world is installed by :func:`install_world`, which re-points
        the module seams AND the request-scoped client dependency, so the two runs read two
        separate stores and neither sees the other's writes.
        """
        live = _comparable(attempt(CallerClient(OWNER_USER), probe))

        install_world(monkeypatch)
        originals = _disable_guard()
        try:
            baseline = _comparable(attempt(CallerClient(OWNER_USER), probe))
        finally:
            _restore_guard(originals)

        assert live == baseline, (
            f"{probe.key}: the OWNER's answer changed when the subscriber guard was added.\n"
            f"  with the guard:    {live}\n"
            f"  without the guard: {baseline}\n"
            f"This is an authorisation change on a live endpoint; an owner's behaviour must not "
            f"move."
        )

    def test_the_guard_is_reachable_at_all(
        self, monkeypatch, _overrides, world: World
    ) -> None:
        """Non-vacuity for the spy: it DOES record a call when one happens.

        Without this, ``spy.calls == []`` above would pass just as well against a spy that was
        never installed over the names the routers actually call.
        """
        spy = _GuardSpy(monkeypatch)
        attempt(CallerClient(SUBSCRIBER_USER), PROBES[0])
        assert spy.calls, (
            "the spy recorded no call even for a subscriber, so it is not installed over the "
            "names the routers call and the owner assertions above are vacuous"
        )


# ══════════════════════════════════════════════════════════════════════════
# 3b. THE STRANGER STILL CANNOT TELL EXISTENCE APART (Requirement 21.4)
# ══════════════════════════════════════════════════════════════════════════
#
# This is the case a careless fix breaks. The fourteen endpoints answered 404 to EVERYONE whose
# ownership predicate missed, and for an unrelated caller that 404 is not a defect — it is
# Requirement 21.4: "a response whose status and body are byte-identical in shape and content to
# the response returned for an identifier that exists in no tenant". Answering 403 there would
# tell the caller the identifier exists, turning every one of these endpoints into an existence
# oracle a tenant could enumerate other tenants' strategy ids with.
#
# So each probe is attempted three times — as the owner, as the subscriber, as the stranger — and
# the stranger's answer is required to equal the answer for an identifier belonging to NOBODY,
# rather than merely to "be a 404". Equality against a genuinely absent identifier is the
# requirement's own wording, and it is strictly stronger: a 404 whose body named the strategy, or
# carried a different code, would pass a status-only check and still disclose.

#: An identifier that exists in no tenant at all. Every seeded table is keyed on the ``…000000``
#: family above; this one is seeded nowhere, so an answer for it is the platform's answer for "no
#: such thing".
ABSENT_STRATEGY_ID = "ef000000-0000-4000-8000-0000000000ff"
ABSENT_MODEL_VERSION_ID = "ef000000-0000-4000-8000-0000000000fe"


def _absent_ids() -> Dict[str, str]:
    """:data:`IDS` with the two resource identifiers swapped for ones that exist nowhere.

    ``version``, ``node_id``, ``signal_id`` and ``library_id`` are left alone deliberately: the
    resource whose existence must stay undisclosed is the STRATEGY (and, for the one model route,
    the model version), and holding every other path and query value constant keeps the
    comparison to that single variable.
    """
    ids = dict(IDS)
    ids["strategy_id"] = ABSENT_STRATEGY_ID
    ids["model_version_id"] = ABSENT_MODEL_VERSION_ID
    return ids


def attempt_absent(client: TestClient, probe: Probe):
    """``probe``, aimed at an identifier that exists in no tenant."""
    ids = _absent_ids()
    return client.request(
        probe.method,
        probe.path.format(**ids),
        params={k: v.format(**ids) for k, v in (probe.query or {}).items()},
        json=dict(probe.body) if probe.body is not None else None,
    )


#: What the caller's OWN identifier is replaced by before the two answers are compared.
_SUPPLIED_ID_PLACEHOLDER = "<the-identifier-the-caller-supplied>"


def _blinded(payload: Any, supplied: Mapping[str, str]) -> Any:
    """``payload`` with each of the caller's own supplied identifiers replaced by a placeholder.

    Requirement 21.4 asks for a response "byte-identical in shape and content to the response
    returned for an identifier that exists in no tenant". Taken literally against two DIFFERENT
    identifiers that is unsatisfiable for any body that echoes the identifier back — and echoing
    it back discloses nothing, because it is the caller's own request line coming home. This file
    already makes exactly that allowance for the envelope's ``path`` key (see
    :data:`_VOLATILE_BODY_KEYS`), and ``tests/test_marketplace_error_surface.py`` makes it for the
    identifier it puts in a path.

    So the comparison is made *identifier-blind* rather than weakened: both bodies have the
    identifier the caller themselves supplied rewritten to :data:`_SUPPLIED_ID_PLACEHOLDER`, and
    then EVERY remaining byte must match. A body that named the owner, carried a different code,
    a different sentence, a different key set, a row count, a version list or a timestamp for one
    identifier and not the other still fails — which is the disclosure the property is about.
    """
    if isinstance(payload, str):
        blinded = payload
        for value in supplied:
            blinded = blinded.replace(value, _SUPPLIED_ID_PLACEHOLDER)
        return blinded
    if isinstance(payload, dict):
        return {key: _blinded(value, supplied) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_blinded(item, supplied) for item in payload]
    return payload


#: The identifiers a probe supplies for the strategy that DOES exist, and for one that does not.
#: Only the two resource identifiers the probes vary; ``version``, ``node_id`` and ``library_id``
#: are held constant by :func:`_absent_ids` and so cannot contribute a difference.
_PRESENT_SUPPLIED_IDS: Mapping[str, str] = MappingProxyType(
    {OWNER_STRATEGY_ID: _SUPPLIED_ID_PLACEHOLDER,
     OWNER_MODEL_VERSION_ID: _SUPPLIED_ID_PLACEHOLDER}
)
_ABSENT_SUPPLIED_IDS: Mapping[str, str] = MappingProxyType(
    {ABSENT_STRATEGY_ID: _SUPPLIED_ID_PLACEHOLDER,
     ABSENT_MODEL_VERSION_ID: _SUPPLIED_ID_PLACEHOLDER}
)


class TestTheStrangerStillCannotTellExistenceApart:
    """Requirement 21.4, per endpoint, against the fix that could have broken it."""

    @pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.key)
    def test_a_stranger_is_answered_404_and_never_403(
        self, probe: Probe, stranger_client: CallerClient
    ) -> None:
        """The status half, stated as the two things it must not be.

        Not 403: that would confirm the identifier resolves to something. Not a success: the
        stranger has no relationship to this strategy at all.
        """
        response = attempt_absent(stranger_client, probe)
        absent_status = response.status_code
        present = attempt(stranger_client, probe)

        assert present.status_code != 403, (
            f"{probe.key}: an UNRELATED caller was answered 403, which confirms the strategy "
            f"exists. Requirement 21.4 requires the same answer an identifier that exists in no "
            f"tenant gets ({absent_status}). Body: {present.text[:300]!r}"
        )
        assert present.status_code == absent_status, (
            f"{probe.key}: a stranger referencing the OWNER's strategy was answered "
            f"{present.status_code}, but an identifier that exists in no tenant is answered "
            f"{absent_status}. The two must be indistinguishable. "
            f"Body: {present.text[:300]!r}"
        )

    @pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.key)
    def test_a_strangers_answer_is_identical_to_the_answer_for_a_nonexistent_id(
        self, probe: Probe, stranger_client: CallerClient
    ) -> None:
        """The body half — the part a status-only check misses.

        Compared identifier-blind — see :func:`_blinded` for why that is the property's own
        wording rather than a weakening — and after the per-request envelope keys are dropped
        (see :data:`_VOLATILE_BODY_KEYS`).
        """
        present_status, present_body = observable(attempt(stranger_client, probe))
        absent_status, absent_body = observable(attempt_absent(stranger_client, probe))
        present = (present_status, _blinded(present_body, _PRESENT_SUPPLIED_IDS))
        absent = (absent_status, _blinded(absent_body, _ABSENT_SUPPLIED_IDS))
        assert present == absent, (
            f"{probe.key}: a stranger can tell the OWNER's strategy apart from an identifier "
            f"that exists in no tenant, so this endpoint is an existence oracle "
            f"(Requirement 21.4).\n"
            f"  existing, foreign: {present}\n"
            f"  existing nowhere:  {absent}"
        )

    @pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.key)
    def test_a_strangers_answer_discloses_no_protected_logic(
        self, probe: Probe, stranger_client: CallerClient
    ) -> None:
        """Requirement 7.1 holds for the stranger too, and it is asserted separately from the
        equality above: two answers can be equal to each other and both disclose."""
        response = attempt(stranger_client, probe)
        _status, body = observable(response)
        listing_projection.assert_contains_no_protected_logic(body, protected_tokens())

    @pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.key)
    def test_a_stranger_gets_no_refusal_audit_entry_naming_the_listing(
        self, probe: Probe, stranger_client: CallerClient, world: World
    ) -> None:
        """A ``MARKETPLACE_ACCESS_REFUSED`` entry naming the Listing would disclose in the log
        what the response correctly withholds — and Requirement 7.12's entry is scoped to the
        Criterion 1/3/6/8/10 refusals, none of which a stranger triggers here."""
        attempt(stranger_client, probe)
        refusals = world.audit.refusals()
        assert refusals == [], (
            f"{probe.key}: an unrelated caller's 404 wrote {len(refusals)} "
            f"MARKETPLACE_ACCESS_REFUSED entr(y/ies): {[e.kwargs for e in refusals]}"
        )

    @pytest.mark.parametrize("probe", PROBES, ids=lambda p: p.key)
    def test_a_strangers_attempt_changes_nothing(
        self, probe: Probe, stranger_client: CallerClient, world: World
    ) -> None:
        before = {
            table: world.db.rows(table)
            for table in ("strategies", "strategy_versions", "model_versions", "signals")
        }
        attempt(stranger_client, probe)
        after = {table: world.db.rows(table) for table in before}
        assert after == before, f"{probe.key}: a stranger's refused attempt changed rows"

    def test_the_stranger_holds_no_subscription_to_the_listing(self, world: World) -> None:
        """Non-vacuity: the stranger really is unrelated, so the assertions above are about the
        stranger case rather than about a second subscriber."""
        subscriptions = [
            row
            for row in world.db.rows("library_subscriptions")
            if str(row.get("user_id")) == STRANGER_ID
        ]
        assert subscriptions == [], (
            "the stranger holds a Subscription in the seeded world, so they are not a stranger"
        )
        listings = world.db.rows("library_strategies")
        assert all(str(row.get("author_id")) != STRANGER_ID for row in listings), (
            "the stranger authored a seeded Listing, so they are not a stranger"
        )


# ══════════════════════════════════════════════════════════════════════════
# 4. Every refusal is audited within 5 seconds, with no Protected_Logic
# ══════════════════════════════════════════════════════════════════════════


def _assert_refusal_entry_is_well_formed(
    entry: AuditEntry, *, started_at: float, listing_id: str, actor_id: str
) -> None:
    """Requirement 7.12's five facts, plus the two prohibitions.

    ``library.py``'s ``_audit_marketplace_refusal`` is the reference shape: the authenticated
    caller, the Listing identifier, the attempted operation, the refusal reason code and a UTC
    timestamp — and no Protected_Logic.
    """
    elapsed = entry.at - started_at
    assert elapsed <= 5.0, (
        f"the MARKETPLACE_ACCESS_REFUSED entry was recorded {elapsed:.3f}s after the request "
        f"began; Requirement 7.12 allows 5 seconds"
    )
    assert entry.kwargs.get("actor_id") == actor_id, entry.kwargs
    assert entry.kwargs.get("resource_id") == listing_id, entry.kwargs
    assert entry.kwargs.get("reason"), "the entry names no refusal reason code"
    metadata = entry.kwargs.get("metadata") or {}
    assert metadata.get("operation"), "the entry names no attempted operation"
    assert metadata.get("listing_id") == listing_id, metadata
    listing_projection.assert_contains_no_protected_logic(
        dict(entry.kwargs), protected_tokens()
    )


@pytest.mark.parametrize("probe", _cases(AUDIT))
def test_a_restricted_operations_refusal_is_audited(
    probe: Probe, subscriber_client: TestClient, world: World
) -> None:
    """Requirement 7.12 for the Criterion 8 refusals — the thirteen of Requirement 12.4.

    One refusal, one ``MARKETPLACE_ACCESS_REFUSED`` entry, within 5 seconds, carrying the caller,
    the Listing, the operation and the reason code, and no Protected_Logic.
    """
    started_at = time.monotonic()
    attempt(subscriber_client, probe)

    refusals = world.audit.refusals()
    assert len(refusals) == 1, (
        f"{probe.key}: the refusal wrote {len(refusals)} MARKETPLACE_ACCESS_REFUSED entries; "
        f"Requirement 7.12 requires exactly one. Recorded: "
        f"{[e.action for e in world.audit.entries]}"
    )
    _assert_refusal_entry_is_well_formed(
        refusals[0],
        started_at=started_at,
        listing_id=LISTING_ID,
        actor_id=SUBSCRIBER_ID,
    )


class TestTheAuditReferenceRefusalIsWellFormed:
    """The positive control for the clause above, on a refusal the server DOES audit today.

    Task 17.2's clone gate and task 17.3's deploy path both call
    ``library._audit_marketplace_refusal``, so they are the reference for the entry shape, the
    5-second bound and the no-Protected_Logic rule. Without this class the audit clause above
    would be thirteen xfails and no demonstration that the assertion can ever pass.
    """

    def test_the_clone_refusal_writes_one_entry_within_five_seconds(
        self, subscriber_client: TestClient, world: World
    ) -> None:
        """``source_cloning_enabled`` is false on the seeded Listing (Requirement 7.4's default),
        so an entitled subscriber's clone is refused 403 and audited (Requirements 7.3, 7.12)."""
        started_at = time.monotonic()
        response = subscriber_client.post("/api/library/{}/clone".format(LISTING_ID))

        assert response.status_code == 403, response.text
        assert _code_of(response.json()) == errors.MARKETPLACE_CLONING_DISABLED, response.text

        refusals = world.audit.refusals()
        assert len(refusals) == 1, [e.kwargs for e in world.audit.entries]
        _assert_refusal_entry_is_well_formed(
            refusals[0],
            started_at=started_at,
            listing_id=LISTING_ID,
            actor_id=SUBSCRIBER_ID,
        )
        assert refusals[0].kwargs["metadata"]["operation"] == "clone"
        assert world.db.rows("strategies") == [_owner_strategy_row()], (
            "a refused clone must create no strategies row (Requirement 7.3)"
        )

    def test_the_unaccepted_field_refusal_writes_one_entry_with_no_supplied_value(
        self, subscriber_client: TestClient, world: World
    ) -> None:
        """Requirement 7.6 + 7.12: the 422 names the field and the entry echoes no value."""
        started_at = time.monotonic()
        response = subscriber_client.post(
            "/api/library/{}/deploy".format(LISTING_ID),
            json={"symbol": "BTC/USDT", "buy_logic": PROTECTED_STRATEGY_ROW["buy_logic"]},
        )

        assert response.status_code == 422, response.text
        refusals = world.audit.refusals()
        assert len(refusals) == 1, [e.kwargs for e in world.audit.entries]
        _assert_refusal_entry_is_well_formed(
            refusals[0],
            started_at=started_at,
            listing_id=LISTING_ID,
            actor_id=SUBSCRIBER_ID,
        )
        assert refusals[0].kwargs["metadata"]["unexpected_fields"] == ["buy_logic"]
        listing_projection.assert_contains_no_protected_logic(
            response.text, protected_tokens()
        )


# ══════════════════════════════════════════════════════════════════════════
# 5. The Signal Trace read path (Requirements 7.9, 23.2, 23.3)
# ══════════════════════════════════════════════════════════════════════════

#: Requirement 23.2's field list, as the requirement words it, in one place. Read as the
#: SUBSCRIBER-VISIBLE set by Requirement 23.3: "the read path SHALL return only the fields listed
#: in Criterion 2".
REQUIREMENT_23_2_FIELDS: FrozenSet[str] = frozenset(
    {
        "strategy",              # the strategy
        "strategy_version",      # the strategy version
        "session_or_deployment", # the Paper_Session or deployment identifier as applicable
        "generated_at",          # the generation timestamp
        "decision",              # the decision
        "asset",                 # the asset
        "side",                  # the side
        "quantity",              # the quantity
        "price",                 # the price
        "execution_status",      # the execution status in the Order_Lifecycle_State vocabulary
        "order_id",              # the order identifier once assigned
        "signal_source",         # the signal source
        "execution_environment", # the Execution_Environment
        "safe_reason",           # a safe reason where the signal was not executed
    }
)

#: The internals Requirement 23.3 excludes from a subscriber's view: node-level trace detail,
#: indicator values, feature values, ML inference detail and risk-rule internals. The three JSONB
#: columns and the five risk fields ``design.md`` names, plus the trace sections
#: ``build_signal_trace_detail`` returns.
REQUIREMENT_23_3_EXCLUSIONS: Tuple[str, ...] = (
    "indicators",
    "market_info",
    "ml_info",
    "dag_nodes",
    "ml_inference",
    "risk_reason",
    "drawdown_check",
    "exposure",
    "expected_loss",
    "expected_reward",
)

#: The task that implements the subscriber-safe projection. Named on every xfail below so a
#: reader knows which task's landing should turn these green.
SIGNAL_PROJECTION_OWNER = "task 29.4"


def _keys_at_any_depth(payload: Any) -> FrozenSet[str]:
    found: set = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(str(key))
            found |= _keys_at_any_depth(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= _keys_at_any_depth(item)
    return frozenset(found)


class TestTheSignalTraceReadPathIsSubscriberSafe:
    """Requirement 23.3, and its non-vacuity control.

    The owner's own detail view really does carry node-level trace detail, indicator values, ML
    inference detail and risk-rule internals — asserted first — so "the subscriber's view has none
    of them" is a statement about a trace that has them.
    """

    def test_the_owners_own_trace_carries_every_internal_the_subscriber_must_not_see(
        self, owner_client: TestClient
    ) -> None:
        """The control. Without it the exclusion assertions below could pass on an empty body."""
        response = owner_client.get(
            "/api/signal-trace/signals/{}".format(OWNER_SIGNAL_ID)
        )
        assert response.status_code == 200, response.text
        keys = _keys_at_any_depth(response.json())
        present = [name for name in REQUIREMENT_23_3_EXCLUSIONS if name in keys]
        assert present, (
            "the owner's own trace detail carries none of the internals Requirement 23.3 "
            "excludes, so the subscriber-exclusion assertions below are vacuous. Keys: "
            f"{sorted(keys)[:40]}"
        )

    def test_the_subscribers_view_excludes_every_requirement_23_3_internal(
        self, subscriber_client: TestClient
    ) -> None:
        """Passes today because the subscriber is refused the read outright, and must keep
        passing once task 29.4 admits them with the restricted projection."""
        response = subscriber_client.get(
            "/api/signal-trace/signals/{}".format(OWNER_SIGNAL_ID)
        )
        keys = _keys_at_any_depth(response.json() if response.content else None)
        leaked = [name for name in REQUIREMENT_23_3_EXCLUSIONS if name in keys]
        assert not leaked, (
            f"the subscriber's signal view carries the excluded internal(s) {leaked} "
            f"(Requirement 23.3). Body: {response.text[:400]!r}"
        )

    def test_the_subscribers_view_discloses_no_protected_logic(
        self, subscriber_client: TestClient
    ) -> None:
        response = subscriber_client.get(
            "/api/signal-trace/signals/{}".format(OWNER_SIGNAL_ID)
        )
        listing_projection.assert_contains_no_protected_logic(
            response.text, protected_tokens()
        )

    # The xfail is GONE, not flipped: task 29.4 landed the allow-list, and
    # ``xfail(strict=True)`` turned this into an XPASS failure the moment it did — which is
    # this file's own mechanism for "the defect is fixed" (see the registry note above).
    def test_the_subscriber_allow_list_exists_and_is_requirement_23_2s_field_set(self) -> None:
        """The allow-list ``design.md`` places in ``signal_service.SUBSCRIBER_SIGNAL_FIELDS``.

        Asserted as a SET rather than by spot-checking members: Requirement 23.3 says "only the
        fields listed in Criterion 2", which is an upper bound as well as a lower one.
        """
        from backend_app.backend import signal_service

        allow_list = getattr(signal_service, "SUBSCRIBER_SIGNAL_FIELDS", None)
        assert allow_list is not None, (
            "signal_service.SUBSCRIBER_SIGNAL_FIELDS is absent, so there is no single place the "
            "subscriber-visible field set is written"
        )
        assert set(allow_list), "the allow-list is empty"
        for excluded in REQUIREMENT_23_3_EXCLUSIONS:
            assert excluded not in set(allow_list), (
                f"the allow-list names {excluded!r}, which Requirement 23.3 excludes"
            )
        # An UPPER BOUND on the count, not an equality against the names in
        # REQUIREMENT_23_2_FIELDS: those are the requirement's own words, and task 29.4 will spell
        # them as columns (``generated_at`` for "the generation timestamp", ``paper_session_id`` for
        # "the Paper_Session or deployment identifier"). Holding the allow-list to this file's
        # transcription of the prose would fail 29.4 for a naming choice rather than for a leak,
        # which is not what Requirement 23.3 says. The count bound plus the exclusion check above
        # is the part that is about disclosure.
        assert len(set(allow_list)) <= len(REQUIREMENT_23_2_FIELDS), (
            f"the allow-list has {len(set(allow_list))} fields; Requirement 23.2 lists "
            f"{len(REQUIREMENT_23_2_FIELDS)}, and Criterion 3 admits ONLY those"
        )

    # Same: task 29.4 gave the builder its viewer-role parameter, so the xfail is deleted
    # rather than left to fail as an XPASS.
    def test_the_detail_builder_takes_a_viewer_role(self) -> None:
        """``design.md``: "``build_signal_trace_detail`` gains a viewer-role parameter"."""
        import inspect

        from backend_app.backend import signal_service

        parameters = set(
            inspect.signature(signal_service.build_signal_trace_detail).parameters
        )
        assert parameters & {"viewer_role", "viewer", "is_owner", "owner_view"}, (
            f"build_signal_trace_detail takes {sorted(parameters)}; none of them is the "
            f"viewer-role parameter the subscriber projection is decided by"
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            f"the Signal Trace read path answers a subscriber nothing at all rather than the "
            f"Requirement 23.2 fields; owner: {SIGNAL_PROJECTION_OWNER} (Requirement 23.3)"
        ),
    )
    def test_the_subscriber_is_served_the_requirement_23_2_fields(
        self, subscriber_client: TestClient
    ) -> None:
        """Requirement 23.3's positive half. Today the read is owner-scoped, so a subscriber gets
        a 404 and sees nothing — which is safe but is not what the requirement says."""
        response = subscriber_client.get(
            "/api/signal-trace/signals/{}".format(OWNER_SIGNAL_ID)
        )
        assert response.status_code == 200, (
            f"a subscriber reading a signal produced by the strategy they subscribe to was "
            f"answered {response.status_code}: {response.text[:300]!r}"
        )
        keys = _keys_at_any_depth(response.json())
        missing = sorted(
            name
            for name in ("decision", "quantity", "price", "order_id", "signal_source")
            if name not in keys
        )
        assert not missing, f"the subscriber's view omits Requirement 23.2 field(s) {missing}"
