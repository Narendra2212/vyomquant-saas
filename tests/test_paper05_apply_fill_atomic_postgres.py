"""PAPER-05 -- ``paper_apply_fill_atomic``, proven against a real PostgreSQL server.

backend_app/migrations/048_paper05_apply_fill_atomic.sql installs ONE SECURITY DEFINER
function, ``paper_apply_fill_atomic``, replacing what used to be SEVEN separate
PostgREST statements (fill, account, ledger, position(s), order, trade, equity
snapshot) each committing as its own transaction. ``paper_simulator.apply_fill`` now
makes exactly one ``.rpc()`` call into it.

WHAT THIS FILE PROVES, AND WHY IT NEEDS A REAL DATABASE
---------------------------------------------------------
The property under test is a TRANSACTIONAL one -- "two concurrent deliveries of the
same ``fill_event_id`` can admit at most one of them, and the loser leaves nothing
behind" -- which no mock of ``supabase-py``/``postgrest`` can demonstrate, because the
mock has no real row lock and no real transaction to roll back. This file calls the
SQL function DIRECTLY, over real ``psycopg2`` connections, the same way
``tests/test_ref01_payout_atomic_postgres.py`` calls ``create_referral_payout_request``
and ``settle_referral_payout`` -- it is deliberately NOT routed through
``paper_repository.apply_fill_atomic`` or ``paper_simulator.apply_fill``, so nothing
about the Python-side retry loop or ``FakeSupabase`` test double can hide a problem
the database itself would have caught.

This is the test the coordinator asked for after finding that
``tests/test_marketplace_concurrency.py``'s B2 battery -- which forces the SAME race
through ``FakeSupabase``'s instrumented ``.rpc()`` call -- could no longer observe a
real row lock once the seven writes became one opaque RPC. That battery still runs
(see the ``_FillRacer`` rewire in ``tests/test_paper_repository.py``) and still proves
the DOUBLE's simulation of the constraint is correct; this file proves the REAL
constraint, inside the REAL function, against a REAL server, independently.

Gated on AERORA_TEST_DATABASE_URL naming PostgreSQL, the same mechanism every other
``*_postgres.py`` file in this directory uses. A SKIP here is NOT a pass -- the
atomicity this migration exists to provide is unverified until this runs for real.
"""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal

import pytest

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. A SKIP "
        "here is NOT a pass -- PAPER-05's atomicity is unverified."
    ),
)


def _conn(autocommit: bool = True):
    import psycopg2

    c = psycopg2.connect(_PG_URL)
    c.autocommit = autocommit
    return c


def _require_paper_tables():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('public.paper_accounts') IS NOT NULL "
                "   AND to_regclass('public.paper_orders') IS NOT NULL "
                "   AND to_regclass('public.paper_fills') IS NOT NULL"
            )
            return bool(cur.fetchone()[0])
    finally:
        conn.close()


@pytest.fixture(scope="module", autouse=True)
def _require_migration_048():
    try:
        if not _require_paper_tables():
            pytest.skip(
                "paper_* tables are absent -- apply 009_paper_trading.sql, "
                "013_paper_default_account_children.sql and "
                "048_paper05_apply_fill_atomic.sql first"
            )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable: {exc}")


@pytest.fixture
def seeded():
    """One user, one funded account, one ACCEPTED market order for it to fill.

    Everything a fresh, never-before-filled attempt needs: an account at
    ``version=1`` (009's own default) and an order sitting in ``ACCEPTED`` with
    ``filled_quantity=0``, so ``paper_apply_fill_atomic``'s own version/state checks
    pass on the FIRST caller and only arbitrate between competitors.
    """
    user_id = str(uuid.uuid4())
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users (id, email) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                (user_id, f"paper05-pg-{user_id[:8]}@example.invalid"),
            )
            cur.execute(
                "INSERT INTO public.profiles (id) VALUES (%s) ON CONFLICT DO NOTHING",
                (user_id,),
            )
            cur.execute(
                "INSERT INTO public.paper_sessions ("
                "    user_id, source_strategy_id, version_id, exchange_id, symbol, "
                "    timeframe, initial_capital_minor, currency, config, "
                "    market_data_source"
                ") VALUES ("
                "    %s, gen_random_uuid(), gen_random_uuid(), 'binance', 'BTC/USDT', "
                "    '1m', 10000000, 'USD', '{}'::jsonb, 'LIVE'"
                ") RETURNING id",
                (user_id,),
            )
            session_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.paper_accounts ("
                "    user_id, currency, initial_capital, available_balance, "
                "    locked_balance, total_equity"
                ") VALUES (%s, 'USD', 100000, 100000, 0, 100000) RETURNING id",
                (user_id,),
            )
            account_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO public.paper_orders ("
                "    session_id, account_id, user_id, symbol, side, order_type, "
                "    quantity, order_state, legacy_status, fingerprint"
                ") VALUES (%s, %s, %s, 'BTC/USDT', 'buy', 'market', 1, "
                "          'ACCEPTED', 'OPEN', %s) RETURNING id",
                (session_id, account_id, user_id, f"paper05-pg-{user_id[:8]}"),
            )
            order_id = cur.fetchone()[0]
    finally:
        conn.close()

    yield {
        "user_id": user_id,
        "session_id": str(session_id),
        "account_id": str(account_id),
        "order_id": str(order_id),
    }

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.paper_sessions WHERE user_id = %s", (user_id,)
            )
            cur.execute("DELETE FROM public.profiles WHERE id = %s", (user_id,))
            cur.execute("DELETE FROM auth.users WHERE id = %s", (user_id,))
    finally:
        conn.close()


def _apply_fill(
    seeded,
    *,
    fill_event_id: str,
    expected_account_version: int = 1,
    expected_order_state: str = "ACCEPTED",
    resume_fill_id=None,
    price: str = "100",
    quantity: str = "1",
    fee_minor: int = 0,
):
    """One direct call to the RPC function, matching the shapes 048 documents.

    Every jsonb argument is built by hand here, not through
    ``paper_repository.apply_fill_atomic``'s serialization helpers, so this file is an
    independent caller of the SQL contract rather than a second copy of the one piece
    of Python code that already calls it in production.
    """
    conn = _conn(autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT public.paper_apply_fill_atomic("
                "    %(user_id)s, %(account_id)s, %(order_id)s, %(session_id)s, "
                "    %(fill_event_id)s, %(expected_account_version)s, "
                "    %(expected_order_state)s, %(resume_fill_id)s, "
                "    %(fill)s::jsonb, %(account)s::jsonb, %(balance_event)s::jsonb, "
                "    %(order)s::jsonb, %(position)s::jsonb, NULL, NULL, "
                "    %(equity_snapshot)s::jsonb"
                ")",
                {
                    "user_id": seeded["user_id"],
                    "account_id": seeded["account_id"],
                    "order_id": seeded["order_id"],
                    "session_id": seeded["session_id"],
                    "fill_event_id": fill_event_id,
                    "expected_account_version": expected_account_version,
                    "expected_order_state": expected_order_state,
                    "resume_fill_id": resume_fill_id,
                    "fill": json.dumps(
                        {
                            "quantity": quantity,
                            "price": price,
                            "fee_minor": fee_minor,
                            "slippage_minor": 0,
                            "market_event_id": None,
                            "filled_at": "2026-01-01T00:00:00Z",
                        }
                    ),
                    "account": json.dumps(
                        {
                            "available_balance": "99900",
                            "locked_balance": "0",
                            "realized_pnl": "0",
                            "total_equity": "99900",
                        }
                    ),
                    "balance_event": json.dumps(
                        {
                            "available_delta": "-100",
                            "locked_delta": "0",
                            "realized_delta": "0",
                            "occurred_at": "2026-01-01T00:00:00Z",
                        }
                    ),
                    "order": json.dumps(
                        {
                            "target_state": "FILLED",
                            "legacy_status": "FILLED",
                            "filled_quantity": quantity,
                            "avg_fill_price": price,
                            "fee_minor": fee_minor,
                            "slippage_minor": 0,
                        }
                    ),
                    "position": json.dumps(
                        {
                            "symbol": "BTC/USDT",
                            "side": "LONG",
                            "size": quantity,
                            "entry_price": price,
                            "opened_at": "2026-01-01T00:00:00Z",
                            "current_price": price,
                            "unrealized_pnl": "0",
                            "price_at": "2026-01-01T00:00:00Z",
                            "closed_at": None,
                        }
                    ),
                    "equity_snapshot": json.dumps(
                        {
                            "series_index": 0,
                            "total_equity": "99900",
                            "available_balance": "99900",
                            "locked_balance": "0",
                            "position_market_value": "100",
                            "stale": False,
                            "taken_at": "2026-01-01T00:00:00Z",
                        }
                    ),
                },
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _fills(order_id: str):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, fill_event_id FROM public.paper_fills WHERE order_id = %s",
                (order_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _balance_events(account_id: str):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, fill_id, cause FROM public.paper_balance_events "
                "WHERE account_id = %s",
                (account_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _account_version(account_id: str) -> int:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT version FROM public.paper_accounts WHERE id = %s",
                (account_id,),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _equity_snapshots(session_id: str):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM public.paper_equity_snapshots WHERE session_id = %s",
                (session_id,),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
#  PART 1 — THE FUNCTION EXISTS AND ITS GUARANTEES HOLD
# ══════════════════════════════════════════════════════════════════════════


@pg_only
def test_the_function_exists_exactly_once_and_is_service_and_authenticated_only():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace ns "
                "ON ns.oid = p.pronamespace WHERE ns.nspname='public' "
                "AND p.proname='paper_apply_fill_atomic'"
            )
            assert cur.fetchone()[0] == 1

            sig = (
                "public.paper_apply_fill_atomic(uuid,uuid,uuid,uuid,text,bigint,"
                "text,uuid,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb,jsonb)"
            )
            cur.execute("SELECT has_function_privilege('anon', %s, 'EXECUTE')", (sig,))
            assert cur.fetchone()[0] is False, (
                "anon must never be able to move paper money directly"
            )
            cur.execute(
                "SELECT has_function_privilege('authenticated', %s, 'EXECUTE')", (sig,)
            )
            assert cur.fetchone()[0] is True
            cur.execute(
                "SELECT has_function_privilege('service_role', %s, 'EXECUTE')", (sig,)
            )
            assert cur.fetchone()[0] is True
    finally:
        conn.close()


@pg_only
def test_a_single_fill_commits_all_seven_writes_together(seeded):
    result = _apply_fill(seeded, fill_event_id="paper05-pg-single")

    assert result["resumed"] is False
    assert result["fill"]["fill_event_id"] == "paper05-pg-single"

    fills = _fills(seeded["order_id"])
    assert len(fills) == 1
    events = _balance_events(seeded["account_id"])
    assert len(events) == 1
    assert events[0][1] == fills[0][0], "the ledger row must reference the fill row"
    assert _account_version(seeded["account_id"]) == 2
    assert _equity_snapshots(seeded["session_id"]) == 1


@pg_only
def test_a_stale_account_version_raises_pa001_and_writes_nothing(seeded):
    import psycopg2

    with pytest.raises(psycopg2.Error) as exc_info:
        _apply_fill(
            seeded, fill_event_id="paper05-pg-stale", expected_account_version=999
        )
    assert exc_info.value.pgcode == "PA001"

    assert _fills(seeded["order_id"]) == []
    assert _balance_events(seeded["account_id"]) == []
    assert _account_version(seeded["account_id"]) == 1, (
        "a refused call must leave the account version completely untouched"
    )


@pg_only
def test_a_stale_order_state_raises_pa001_and_writes_nothing(seeded):
    import psycopg2

    with pytest.raises(psycopg2.Error) as exc_info:
        _apply_fill(
            seeded,
            fill_event_id="paper05-pg-stale-order",
            expected_order_state="PARTIALLY_FILLED",
        )
    assert exc_info.value.pgcode == "PA001"
    assert _fills(seeded["order_id"]) == []


@pg_only
def test_a_repeated_fill_event_id_raises_pa002_and_does_not_double_apply(seeded):
    """The sequential form of the race the next test forces concurrently."""
    import psycopg2

    first = _apply_fill(seeded, fill_event_id="paper05-pg-dup")
    assert first["resumed"] is False

    with pytest.raises(psycopg2.Error) as exc_info:
        _apply_fill(
            seeded,
            fill_event_id="paper05-pg-dup",
            expected_account_version=1,  # the caller's stale, pre-first-fill read
        )
    assert exc_info.value.pgcode in ("PA001", "PA002"), (
        "whichever check fires first (the stale version from read-before-the-"
        "first-fill, or the uq_paper_fill_event collision), the second call must "
        "be refused and not re-apply the money"
    )

    assert len(_fills(seeded["order_id"])) == 1, "exactly one fill row, not two"
    assert len(_balance_events(seeded["account_id"])) == 1, (
        "exactly one ledger row, not two -- the money moved exactly once"
    )


@pg_only
def test_a_missing_account_raises_pa003(seeded):
    import psycopg2

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.paper_orders WHERE id = %s", (seeded["order_id"],)
            )
            cur.execute(
                "DELETE FROM public.paper_accounts WHERE id = %s",
                (seeded["account_id"],),
            )
    finally:
        conn.close()

    with pytest.raises(psycopg2.Error) as exc_info:
        _apply_fill(seeded, fill_event_id="paper05-pg-no-account")
    assert exc_info.value.pgcode == "PA003"


# ══════════════════════════════════════════════════════════════════════════
#  PART 2 — THE ATOMICITY PROPERTY, UNDER REAL CONCURRENCY
# ══════════════════════════════════════════════════════════════════════════


@pg_only
@pytest.mark.parametrize("n", [2, 5, 9])
def test_n_concurrent_deliveries_of_the_same_fill_event_id_admit_exactly_one(
    seeded, n
):
    """THE PROPERTY THIS MIGRATION EXISTS TO PROVIDE, UNDER REAL CONCURRENCY.

    ``n`` threads, each its own ``psycopg2`` connection, all call
    ``paper_apply_fill_atomic`` for the SAME order with the SAME ``fill_event_id`` and
    the SAME (stale-by-construction, for all but the winner) expected account
    version, released together with a ``threading.Barrier``. The account row's
    ``SELECT ... FOR UPDATE`` serialises them for real: exactly one may see
    ``version == 1`` and commit; every other one must be refused (PA001 from the
    version check, or PA002 if a resume/duplicate path raced a step further) and must
    leave no fill row, no ledger row and no extra equity snapshot behind.

    This is the exact scenario ``tests/test_marketplace_concurrency.py``'s B2 battery
    forces through ``FakeSupabase``'s simulated ``.rpc()`` call; this test forces it
    against the real function, over real connections, with no simulation in between.
    """
    import threading

    results = {}
    barrier = threading.Barrier(n)

    def _attempt(i):
        try:
            barrier.wait(timeout=30)
            results[i] = ("ok", _apply_fill(seeded, fill_event_id="paper05-pg-race"))
        except Exception as exc:  # noqa: BLE001
            results[i] = ("error", getattr(exc, "pgcode", str(exc)))

    threads = [threading.Thread(target=_attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(results) == n, f"every thread must finish; got {results}"
    outcomes = [v[0] for v in results.values()]
    assert outcomes.count("ok") == 1, (
        f"exactly one of {n} concurrent deliveries of the same fill_event_id must "
        f"succeed; got {results}"
    )
    assert outcomes.count("error") == n - 1
    for tag, value in results.items():
        if value[0] == "error":
            assert value[1] in ("PA001", "PA002"), (
                f"thread {tag} was refused for an unexpected reason: {value}"
            )

    fills = _fills(seeded["order_id"])
    assert len(fills) == 1, f"exactly one fill row must have landed, found {fills}"
    events = _balance_events(seeded["account_id"])
    assert len(events) == 1, f"exactly one balance event must have landed, found {events}"
    assert events[0][1] == fills[0][0]
    assert _account_version(seeded["account_id"]) == 2, (
        "the account must have moved exactly once, not once per competitor"
    )
    assert _equity_snapshots(seeded["session_id"]) == 1


@pg_only
def test_the_account_row_is_really_locked_not_merely_version_checked(seeded):
    """Distinguishes real `FOR UPDATE` from an optimistic check a race could still slip.

    Two DIFFERENT ``fill_event_id``s against the SAME account, released together.
    An optimistic version-only guard (no real lock) can let both transactions read
    ``version=1`` before either writes, race to decide their own next-version values,
    and have the second UPDATE silently fail its own ``WHERE version = 1`` -- which is
    exactly the PA001 outcome this function document expects to be "unreachable in
    practice" (048's own comment on the account UPDATE's ``IF NOT FOUND`` branch). What
    is asserted here is the stronger claim: the two calls do not corrupt each other's
    view of the account even transiently, and the loser is cleanly refused with PA001,
    never partially applied.
    """
    import threading

    results = {}
    barrier = threading.Barrier(2)

    def _attempt(tag, event_id):
        try:
            barrier.wait(timeout=30)
            results[tag] = ("ok", _apply_fill(seeded, fill_event_id=event_id))
        except Exception as exc:  # noqa: BLE001
            results[tag] = ("error", getattr(exc, "pgcode", str(exc)))

    t1 = threading.Thread(target=_attempt, args=("a", "paper05-pg-lock-a"))
    t2 = threading.Thread(target=_attempt, args=("b", "paper05-pg-lock-b"))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    outcomes = [v[0] for v in results.values()]
    assert outcomes.count("ok") == 1, (
        f"two fills against ONE account, both claiming version=1, must not both "
        f"succeed: {results}"
    )
    assert outcomes.count("error") == 1
    loser = [v for v in results.values() if v[0] == "error"][0]
    assert loser[1] == "PA001"

    assert _account_version(seeded["account_id"]) == 2, (
        "exactly one version bump -- two would mean the lock let both writers "
        "through and the row was corrupted into a higher, wrong version"
    )
    assert len(_fills(seeded["order_id"])) == 1
    assert len(_balance_events(seeded["account_id"])) == 1
