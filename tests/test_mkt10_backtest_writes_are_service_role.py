"""tests/test_mkt10_backtest_writes_are_service_role.py — MKT-10

``strategy_backtests`` is the five-backtest half of the Marketplace Eligibility_Gate's
evidence. Until this change its only writer was the CALLER'S OWN request-scoped client,
so ``authenticated`` had to keep INSERT/UPDATE/DELETE on the table — and a privilege the
application needs is also one a logged-in seller can use directly against PostgREST,
with no application code in the path. The gate was counting evidence its own subject
could author. Measured in production on 2026-11-18: ``authenticated`` held
DELETE/INSERT/SELECT/UPDATE here, while the three execution ledgers had already been
narrowed to SELECT.

The fix moves the three writes onto the service-role client so the privilege can be
revoked. That trade is only sound if two things hold, and this file is what holds them:

1. **The writes really do run under the service-role client** — otherwise the revoke
   stops every backtest from persisting, which is exactly why this was deferred rather
   than done when the ledgers were narrowed.
2. **Each write still carries its own ownership predicate** — the service-role client is
   not subject to ``backtests_owner_access``, so ``.eq("user_id", …)`` is no longer belt
   and braces over RLS. It IS the check. Removing one would turn a scoped UPDATE into a
   cross-tenant one.

Reads are deliberately left on the request-scoped client and are asserted to stay there:
a read has no reason to give up RLS as a second layer, and ``authenticated`` keeps
SELECT.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from backend_app.backend import backtest_service as bts
from backend_app.backend.backtest_service import (
    BacktestService,
    reset_executed_bar_count_support,
)

TABLE = "strategy_backtests"

OWNER = {"id": "11111111-1111-4111-8111-111111111111", "access_token": "owner-jwt"}
INTRUDER = {"id": "22222222-2222-4222-8222-222222222222", "access_token": "intruder-jwt"}
STRATEGY_ID = "33333333-3333-4333-8333-333333333333"
VERSION_ID = "44444444-4444-4444-8444-444444444444"
BACKTEST_ID = "55555555-5555-4555-8555-555555555555"

#: The two methods whose statements have no WHERE clause of their own, and the one that
#: does. Named here so the reason each is in this file is visible.
_SCOPED_WRITE_METHODS = ("update_backtest_results", "delete_backtest")


# ───────────────────────── the recording double ─────────────────────────


class _Result:
    def __init__(self, data: List[Dict]) -> None:
        self.data = data
        self.error = None


class _Query:
    """One PostgREST statement, remembering its verb, filters and payload."""

    def __init__(self, client: "_Client", table: str) -> None:
        self._c = client
        self._table = table
        self._verb: Optional[str] = None
        self._payload: Optional[Dict] = None
        self._filters: List[Tuple[str, Any]] = []
        self._columns: Optional[str] = None

    # verbs
    def insert(self, payload, *a, **kw):
        self._verb, self._payload = "insert", dict(payload)
        return self

    def update(self, payload, *a, **kw):
        self._verb, self._payload = "update", dict(payload)
        return self

    def delete(self, *a, **kw):
        self._verb = "delete"
        return self

    def select(self, columns="*", *a, **kw):
        self._verb, self._columns = "select", columns
        return self

    # modifiers
    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def _matches(self, row: Dict) -> bool:
        return all(str(row.get(c)) == str(v) for c, v in self._filters)

    def execute(self):
        rows = self._c.rows.setdefault(self._table, [])
        record = {
            "client": self._c.label,
            "table": self._table,
            "verb": self._verb,
            "filters": dict(self._filters),
            "payload": self._payload,
            "columns": self._columns,
        }
        self._c.ops.append(record)

        if self._verb == "insert":
            rows.append(dict(self._payload or {}))
            return _Result([dict(self._payload or {})])

        if self._verb == "update":
            hit = [r for r in rows if self._matches(r)]
            for r in hit:
                r.update(self._payload or {})
            return _Result([dict(r) for r in hit])

        if self._verb == "delete":
            hit = [r for r in rows if self._matches(r)]
            for r in hit:
                rows.remove(r)
            return _Result([dict(r) for r in hit])

        return _Result([dict(r) for r in rows if self._matches(r)])


class _Client:
    """A labelled PostgREST client, so a test can say WHICH one got the statement."""

    def __init__(self, label: str, rows: Optional[Dict[str, List[Dict]]] = None) -> None:
        self.label = label
        self.rows: Dict[str, List[Dict]] = rows if rows is not None else {}
        self.ops: List[Dict] = []

    def table(self, name):
        return _Query(self, name)

    def verbs(self, table: str = TABLE) -> List[str]:
        return [o["verb"] for o in self.ops if o["table"] == table]

    def mutations(self, table: str = TABLE) -> List[Dict]:
        return [
            o
            for o in self.ops
            if o["table"] == table and o["verb"] in {"insert", "update", "delete"}
        ]


def _wire(rows: Optional[Dict[str, List[Dict]]] = None):
    """The real service with its two client seams pointed at two DISTINCT doubles.

    Both share one ``rows`` store, so they are two views of one database — which is what
    production has. Keeping the client objects distinct is the whole point: it is how a
    statement's ``client`` label can prove which authority issued it.
    """
    shared: Dict[str, List[Dict]] = rows if rows is not None else {}
    read = _Client("request-scoped", shared)
    write = _Client("service-role", shared)
    service = BacktestService()
    service._get_supabase = lambda user: read  # noqa: SLF001
    service._get_write_supabase = lambda: write  # noqa: SLF001
    return service, read, write


@pytest.fixture(autouse=True)
def _forget_006_probe():
    """The 006 availability verdict is a module global; no test may inherit another's."""
    reset_executed_bar_count_support()
    yield
    reset_executed_bar_count_support()


def _existing_row(user_id: str = OWNER["id"], **overrides) -> Dict:
    row = {
        "id": BACKTEST_ID,
        "user_id": user_id,
        "strategy_id": STRATEGY_ID,
        "status": "running",
        "executed_bar_count": None,
    }
    row.update(overrides)
    return row


async def _create(service, user=OWNER, **overrides):
    kwargs = dict(
        user=user,
        strategy_id=STRATEGY_ID,
        version="v1.0",
        blueprint={"schema_version": "2.0", "dag_hash": "hash-1"},
        dataset="BTC/USDT",
        start_date="2025-01-01",
        end_date="2025-04-01",
        initial_capital=10_000.0,
        commission=0.001,
        slippage=0.0005,
        version_id=VERSION_ID,
    )
    kwargs.update(overrides)
    return await service.create_backtest(**kwargs)


# ───────── 1. each write runs under the service-role client ─────────


def test_the_insert_runs_under_the_service_role_client() -> None:
    """``create_backtest``'s INSERT is what the revoke would otherwise have broken."""
    service, read, write = _wire()
    asyncio.run(_create(service))

    assert write.verbs() == ["insert"], (
        "the INSERT did not reach the service-role client, so revoking authenticated's "
        "INSERT on strategy_backtests would stop every backtest from being recorded"
    )
    assert read.mutations() == [], (
        "the request-scoped client issued a mutation; authenticated's DML privilege "
        "cannot be revoked while that is true"
    )


def test_the_results_update_runs_under_the_service_role_client() -> None:
    service, read, write = _wire({TABLE: [_existing_row()]})
    asyncio.run(
        service.update_backtest_results(
            user=OWNER,
            backtest_id=BACKTEST_ID,
            results={"total_return": 1.5, "sharpe_ratio": 0.9},
        )
    )

    assert "update" in write.verbs()
    assert read.mutations() == []


def test_the_delete_runs_under_the_service_role_client() -> None:
    service, read, write = _wire({TABLE: [_existing_row()]})
    deleted = asyncio.run(service.delete_backtest(user=OWNER, backtest_id=BACKTEST_ID))

    assert deleted is True
    assert "delete" in write.verbs()
    assert read.mutations() == []


def test_no_mutation_anywhere_reaches_the_request_scoped_client() -> None:
    """All three writes in one process, then one assertion over the whole recording."""
    service, read, write = _wire({TABLE: [_existing_row()]})
    asyncio.run(_create(service))
    asyncio.run(
        service.update_backtest_results(
            user=OWNER, backtest_id=BACKTEST_ID, results={"total_return": 1.0}
        )
    )
    asyncio.run(service.delete_backtest(user=OWNER, backtest_id=BACKTEST_ID))

    assert {o["verb"] for o in write.mutations()} == {"insert", "update", "delete"}
    assert read.mutations() == [], (
        f"these mutations still used the caller's own client: {read.mutations()}"
    )


# ───────── 2. the ownership predicate is now the whole check ─────────


@pytest.mark.parametrize("column", ["id", "user_id"])
def test_the_update_still_filters_on_both_columns(column: str) -> None:
    """Without RLS behind it, dropping either predicate is a cross-tenant write."""
    service, _read, write = _wire({TABLE: [_existing_row()]})
    asyncio.run(
        service.update_backtest_results(
            user=OWNER, backtest_id=BACKTEST_ID, results={"total_return": 1.0}
        )
    )

    (update,) = [o for o in write.mutations() if o["verb"] == "update"]
    assert column in update["filters"], (
        f"the UPDATE lost its {column!r} predicate. Under the service-role client "
        "backtests_owner_access does not apply, so this predicate is the only thing "
        "scoping the statement to its owner"
    )


@pytest.mark.parametrize("column", ["id", "user_id"])
def test_the_delete_still_filters_on_both_columns(column: str) -> None:
    service, _read, write = _wire({TABLE: [_existing_row()]})
    asyncio.run(service.delete_backtest(user=OWNER, backtest_id=BACKTEST_ID))

    (delete,) = [o for o in write.mutations() if o["verb"] == "delete"]
    assert column in delete["filters"], (
        f"the DELETE lost its {column!r} predicate, which is now the only ownership scope"
    )


def test_the_update_predicate_carries_the_callers_own_id() -> None:
    """A predicate naming the wrong id would scope the statement to the wrong tenant."""
    service, _read, write = _wire({TABLE: [_existing_row()]})
    asyncio.run(
        service.update_backtest_results(
            user=OWNER, backtest_id=BACKTEST_ID, results={"total_return": 1.0}
        )
    )

    (update,) = [o for o in write.mutations() if o["verb"] == "update"]
    assert update["filters"]["user_id"] == OWNER["id"]
    assert update["filters"]["id"] == BACKTEST_ID


def test_the_insert_writes_the_verified_identity_not_a_supplied_one() -> None:
    """The INSERT has no WHERE clause; its ``user_id`` is the whole ownership claim.

    RLS checked ``WITH CHECK (user_id = auth.uid())``. The replacement is that the value
    comes from the ``user`` dict — which ``get_current_user`` builds from the verified
    JWT — and not from anything the request body can set.
    """
    service, _read, write = _wire()
    asyncio.run(_create(service))

    (insert,) = [o for o in write.mutations() if o["verb"] == "insert"]
    assert insert["payload"]["user_id"] == OWNER["id"]


# ───────── 3. cross-tenant attempts still match nothing ─────────


def test_a_non_owner_update_matches_nothing_and_leaves_the_row_alone() -> None:
    rows = {TABLE: [_existing_row(user_id=OWNER["id"], status="running")]}
    service, _read, _write = _wire(rows)

    out = asyncio.run(
        service.update_backtest_results(
            user=INTRUDER, backtest_id=BACKTEST_ID, results={"total_return": 99.0}
        )
    )

    assert out == {}, "a non-owner's results write must match no row"
    assert rows[TABLE][0]["status"] == "running", (
        "the owner's row was modified by a caller who does not own it"
    )
    assert "total_return_pct" not in rows[TABLE][0]


def test_a_non_owner_delete_matches_nothing_and_leaves_the_row_alone() -> None:
    rows = {TABLE: [_existing_row(user_id=OWNER["id"])]}
    service, _read, _write = _wire(rows)

    deleted = asyncio.run(
        service.delete_backtest(user=INTRUDER, backtest_id=BACKTEST_ID)
    )

    assert deleted is False
    assert len(rows[TABLE]) == 1, "a non-owner deleted the owner's backtest"


# ───────── 4. reads stay on the request-scoped client ─────────


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda s: s.get_backtest(user=OWNER, backtest_id=BACKTEST_ID),
            id="get_backtest",
        ),
        pytest.param(lambda s: s.list_backtests(user=OWNER), id="list_backtests"),
        pytest.param(
            lambda s: s.get_backtest_history(user=OWNER, strategy_id=STRATEGY_ID),
            id="get_backtest_history",
        ),
        pytest.param(
            lambda s: s.compare_backtests(user=OWNER, backtest_ids=[BACKTEST_ID]),
            id="compare_backtests",
        ),
    ],
)
def test_reads_keep_rls_as_a_second_layer(call) -> None:
    """Reads were not moved, and must not drift onto the service-role client.

    ``authenticated`` keeps SELECT, so these still pass through
    ``backtests_owner_access`` as well as their own ``user_id`` predicate.
    """
    service, read, write = _wire({TABLE: [_existing_row()]})
    asyncio.run(call(service))

    assert read.ops, "the read did not use the caller's own RLS-scoped client"
    assert write.ops == [], (
        "a read moved onto the service-role client, giving up RLS for no reason"
    )


# ───────── 5. the 006 probe asks the client that will write ─────────


def test_the_executed_bar_count_probe_uses_the_writing_client() -> None:
    """The probe's question is "will the UPDATE find this column?".

    Asking it through a client with different authority asks a different question, so
    the probe has to travel with the write. It previously ran on the caller's client
    because the write did too.
    """
    service, read, write = _wire({TABLE: [_existing_row()]})
    asyncio.run(
        service.update_backtest_results(
            user=OWNER,
            backtest_id=BACKTEST_ID,
            results={"total_return": 1.0},
            executed_bar_count=500,
        )
    )

    probes = [
        o
        for o in write.ops
        if o["verb"] == "select" and o["columns"] == bts.EXECUTED_BAR_COUNT_COLUMN
    ]
    assert probes, (
        "the 006 availability probe did not run through the service-role client, so it "
        "no longer sees what the write sees"
    )
    assert not [o for o in read.ops if o["columns"] == bts.EXECUTED_BAR_COUNT_COLUMN]


def test_the_bar_count_still_lands_on_the_row() -> None:
    """The probe move must not cost the column it exists to protect."""
    rows = {TABLE: [_existing_row()]}
    service, _read, _write = _wire(rows)
    asyncio.run(
        service.update_backtest_results(
            user=OWNER,
            backtest_id=BACKTEST_ID,
            results={"total_return": 1.0},
            executed_bar_count=500,
        )
    )

    assert rows[TABLE][0][bts.EXECUTED_BAR_COUNT_COLUMN] == 500


# ───────── 6. source-level guards against a silent revert ─────────


def _method_source(name: str) -> str:
    return textwrap.dedent(inspect.getsource(getattr(BacktestService, name)))


@pytest.mark.parametrize(
    "method", ["create_backtest", "update_backtest_results", "delete_backtest"]
)
def test_a_write_method_never_reaches_for_the_request_scoped_client(method: str) -> None:
    """The regression this file exists to prevent, caught at the source.

    A behavioural test can be satisfied by a stub; this cannot. If someone restores
    ``self._get_supabase(user)`` in a write path, ``authenticated``'s revoked privilege
    makes it fail in production and nowhere else.
    """
    src = _method_source(method)
    assert "_get_write_supabase" in src, (
        f"{method} no longer acquires the service-role client"
    )
    assert "_get_supabase(" not in src, (
        f"{method} acquires the caller's own client again. authenticated holds no DML on "
        f"{TABLE} in production, so this write would fail there and pass here"
    )


def test_the_write_seam_resolves_to_the_service_role_factory() -> None:
    """``_get_write_supabase`` must call the service-role factory, not be re-pointed."""
    src = _method_source("_get_write_supabase")
    assert "get_service_supabase_async" in src
    calls = [
        n.func.id
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    ]
    assert "get_service_supabase_async" in calls, (
        "the write seam no longer calls get_service_supabase_async"
    )
    assert "create_request_supabase_async" not in calls


def test_the_three_mutations_are_the_only_ones_in_the_module() -> None:
    """A fourth write would need its own ownership predicate and its own review.

    Pinned because the revoke is justified by an exhaustive reading of this module. A
    new ``.insert``/``.update``/``.delete`` added without moving it to the service-role
    client would fail in production; added WITH the service-role client but without a
    ``user_id`` predicate, it would be a cross-tenant write that no RLS policy catches.
    """
    tree = ast.parse(
        Path(bts.__file__).read_text(encoding="utf-8"), filename=bts.__file__
    )
    mutations = [
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"insert", "update", "delete", "upsert"}
    ]
    assert sorted(mutations) == ["delete", "insert", "update"], (
        f"the module's mutation inventory changed: {sorted(mutations)}. Every write here "
        "runs without RLS, so a new one needs an explicit user_id predicate and a line "
        "in this test"
    )
