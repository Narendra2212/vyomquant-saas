"""
tests/test_strategy_validation_test_prefix_gate.py

``ExecutionEngine._validate_strategy_exists`` must not admit a ``test_``-prefixed
strategy_id outside a test environment.

WHAT WAS WRONG
--------------
``execute_with_idempotency`` — the single funnel every live order passes through — carries a
step it documents as "STRATEGY VALIDATION - Must be real strategy from DB", whose stated
contract is "ALL trades must be tied to a real strategy in the database". It is enforced by
``_validate_strategy_exists``, and that method contained::

    if str(strategy_id).startswith("test_"):
        return True

placed **before any database query**, and repeated as the fallback return after the query
missed::

    return str(strategy_id).startswith("test_")

So the control was bypassable by naming convention, on the live path, with no environment
condition on it at all. Anything that could reach the funnel with a ``test_``-prefixed id
skipped strategy existence AND the ``user_id`` ownership predicate in the same step — the
only place in that funnel where the tenant is checked against the strategy.

WHAT IS ASSERTED
----------------
1. Outside a test environment the prefix buys nothing: a ``test_`` id with no matching row
   is refused, exactly as any other unknown id is.
2. The refusal does not depend on the database being reachable — with the session factory
   raising, the answer is still ``False`` (the method's documented "fail secure").
3. Inside a test environment (``ENV=testing``, which ``tests/conftest.py`` sets for this
   whole suite) the prefix still works, because several hundred tests in this repository
   drive the funnel with synthetic ``test_*`` strategy ids and the bypass is what makes
   that possible. This is the half that keeps the fix from being a mass test rewrite.
4. A real row is still accepted on its own merits, in any environment, and the query that
   accepts it still carries the tenant predicate.
5. The blocklist is unaffected: ``"test"`` exactly (no underscore) stays refused, in every
   environment, because it is in ``blocked_ids``.

WHY A GATE RATHER THAN A DELETION
---------------------------------
Deleting the branch outright is the stricter fix and it is the wrong one to make blind: the
synthetic-id convention is load-bearing across this suite, and a change that reds several
hundred unrelated tests cannot be reviewed for the thing it is actually about. Gating it on
the environment removes the production reachability — which is the defect — and leaves the
test-lane behaviour exactly as it was. Note the gate fails CLOSED: an unset ``ENV`` disables
the bypass, so a deployment that never set it is protected rather than exposed.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.core import execution_engine as ee
from backend_app.core.execution_engine import ExecutionEngine

TENANT = UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT = UUID("22222222-2222-4222-8222-222222222222")

#: A synthetic id of exactly the shape the bypass admitted.
TEST_PREFIXED = "test_mean_reversion"

#: The portfolio state the engine requires. A synthetic paper balance is explicitly allowed
#: by the constructor's contract, as long as it is chosen rather than defaulted.
PORTFOLIO: Dict[str, Any] = {"total_equity": 10_000.0}

#: Environments in which the bypass must NOT apply. "" is the unset case.
PRODUCTION_LIKE = ("production", "staging", "")

#: Environments in which it must still apply, so this suite keeps working.
TEST_LIKE = ("testing", "test")


@pytest.fixture
def engine() -> ExecutionEngine:
    return ExecutionEngine(portfolio_state=dict(PORTFOLIO))


class _Row:
    """A truthy ``fetchone()`` result — the shape the real query returns for a hit."""


class _Cursor:
    def __init__(self, row: Optional[_Row]) -> None:
        self._row = row

    def fetchone(self) -> Optional[_Row]:
        return self._row


class _Session:
    """A SQLAlchemy session double that records the one query the method issues."""

    def __init__(self, row: Optional[_Row]) -> None:
        self._row = row
        self.executed: list = []
        self.closed = False

    def execute(self, statement: Any, params: Any = None) -> _Cursor:
        self.executed.append((str(statement), params))
        return _Cursor(self._row)

    def close(self) -> None:
        self.closed = True


def _install_db(monkeypatch, *, row: Optional[_Row] = None, raises: bool = False) -> list:
    """Replace the session factory the method imports. Returns the sessions it opened.

    ``_validate_strategy_exists`` imports ``SessionLocal`` from
    ``backend_app.core.database`` *inside* its own body, so the patch has to land on that
    module rather than on a name in ``execution_engine``.
    """
    import backend_app.core.database as database

    opened: list = []

    def _factory() -> _Session:
        if raises:
            raise RuntimeError("the database is unreachable")
        session = _Session(row)
        opened.append(session)
        return session

    monkeypatch.setattr(database, "SessionLocal", _factory)
    return opened


# ---------------------------------------------------------------------------
# 1 + 2. Outside a test environment the prefix buys nothing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env", PRODUCTION_LIKE, ids=lambda e: e or "unset")
def test_a_test_prefixed_id_with_no_row_is_refused_outside_a_test_env(
    engine: ExecutionEngine, monkeypatch, env: str
):
    """The defect, stated as a test.

    Before the fix this returns ``True`` without issuing a query at all, in every
    environment including production.
    """
    monkeypatch.setenv("ENV", env)
    opened = _install_db(monkeypatch, row=None)

    assert engine._validate_strategy_exists(TENANT, TEST_PREFIXED) is False, (
        f"ENV={env!r}: a strategy_id prefixed 'test_' was accepted with no matching row. "
        f"That bypasses both the existence check and the user_id predicate in the only "
        f"step of the live funnel that ties a trade to a real, owned strategy."
    )
    assert opened, (
        f"ENV={env!r}: the method returned without querying the database at all, so the "
        f"prefix short-circuited ahead of the check rather than being judged by it"
    )


@pytest.mark.parametrize("env", PRODUCTION_LIKE, ids=lambda e: e or "unset")
def test_an_unreachable_database_still_refuses_a_test_prefixed_id(
    engine: ExecutionEngine, monkeypatch, env: str
):
    """The method's documented "fail secure", which the prefix also defeated.

    A validation that cannot be performed must refuse. With the bypass ahead of the ``try``
    it never reached the failure path, so an unverifiable id was admitted.
    """
    monkeypatch.setenv("ENV", env)
    _install_db(monkeypatch, raises=True)

    assert engine._validate_strategy_exists(TENANT, TEST_PREFIXED) is False, (
        f"ENV={env!r}: an id that could not be verified at all was accepted"
    )


# ---------------------------------------------------------------------------
# 3. Inside a test environment the convention still works
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env", TEST_LIKE)
def test_the_prefix_still_works_in_a_test_environment(
    engine: ExecutionEngine, monkeypatch, env: str
):
    """Several hundred tests in this repository depend on this.

    If this fails, the fix has become a mass test rewrite rather than a production
    hardening, which is not what it is for.
    """
    monkeypatch.setenv("ENV", env)
    _install_db(monkeypatch, row=None)

    assert engine._validate_strategy_exists(TENANT, TEST_PREFIXED) is True, (
        f"ENV={env!r}: the synthetic-id convention this suite relies on no longer works"
    )


# ---------------------------------------------------------------------------
# 4. A real row is accepted on its merits, with the tenant predicate intact
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env", PRODUCTION_LIKE + TEST_LIKE, ids=lambda e: e or "unset")
def test_a_real_row_is_accepted_in_every_environment(
    engine: ExecutionEngine, monkeypatch, env: str
):
    monkeypatch.setenv("ENV", env)
    opened = _install_db(monkeypatch, row=_Row())

    assert engine._validate_strategy_exists(TENANT, "a-real-strategy-uuid") is True
    assert opened, "the accepting answer must come from the database, not from a prefix"


def test_the_query_still_carries_the_tenant_predicate(
    engine: ExecutionEngine, monkeypatch
):
    """The ownership half of this step, pinned.

    This is the only place in ``execute_with_idempotency`` where the strategy is checked
    against the caller's tenant, so the predicate disappearing would be a cross-tenant
    execution defect rather than a tidy-up.
    """
    monkeypatch.setenv("ENV", "production")
    opened = _install_db(monkeypatch, row=_Row())

    engine._validate_strategy_exists(TENANT, "a-real-strategy-uuid")

    assert opened, "no query was issued"
    statement, params = opened[0].executed[0]
    assert "user_id" in statement, (
        f"the strategy validation query no longer filters on user_id: {statement!r}"
    )
    assert params and params.get("tid") == str(TENANT), (
        f"the query was not bound to the calling tenant: {params!r}"
    )


# ---------------------------------------------------------------------------
# 5. The blocklist is unaffected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("env", PRODUCTION_LIKE + TEST_LIKE, ids=lambda e: e or "unset")
@pytest.mark.parametrize(
    "blocked", ["test", "manual_order", "manual", "direct", "ui_order", "fake", "none"]
)
def test_the_blocked_ids_stay_blocked_in_every_environment(
    engine: ExecutionEngine, monkeypatch, env: str, blocked: str
):
    """``"test"`` exactly is in ``blocked_ids`` and must not be reachable through the gate.

    The blocklist runs before the prefix branch, so this also pins that ordering: a gate
    inserted above it would turn ``"test"`` into an accepted id in the test lane.
    """
    monkeypatch.setenv("ENV", env)
    _install_db(monkeypatch, row=_Row())

    assert engine._validate_strategy_exists(TENANT, blocked) is False, (
        f"ENV={env!r}: the blocked id {blocked!r} was accepted"
    )


def test_an_empty_strategy_id_is_refused(engine: ExecutionEngine, monkeypatch):
    monkeypatch.setenv("ENV", "testing")
    assert engine._validate_strategy_exists(TENANT, "") is False
    assert engine._validate_strategy_exists(TENANT, None) is False  # type: ignore[arg-type]
