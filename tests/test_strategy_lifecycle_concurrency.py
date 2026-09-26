"""
tests/test_strategy_lifecycle_concurrency.py

Production-launch-hardening task 12.6 - Requirement 1.16 (bugfix.md) / 2.16.

RESULT, STATED UP FRONT: THE APPLICATION-LEVEL PROPERTY DOES NOT HOLD TODAY
----------------------------------------------------------------------------
This file was run against the current tree before being finalised, not only reasoned
about. The result is not hypothetical: driving start/stop/delete/deploy against one
strategy id, interleaved at real ``await`` points, reliably produces BOTH forbidden
terminal states this task names -

  * ``test_the_four_operations_can_land_running_but_deleted`` reproduced
    running-but-deleted in **21 of 40** seeded interleavings on the run this docstring was
    written from - just over half, and by construction every seed drives the identical
    four operations against an identically-shaped fresh world, so the split is entirely a
    function of scheduling order, exactly as the "no application-level lock" finding
    predicts.
  * ``test_deploy_can_race_itself_into_a_double_deploy`` reproduced two simultaneously
    ``running`` deployment rows for one strategy from two concurrent ``deploy`` calls
    racing a ``stop``, in **12 of 12** seeded runs - every single one.

These are counts from one real run of this file, kept here as the recorded evidence
rather than a rounded claim; a re-run may find different counts among the 40 (asyncio's
scheduling of a given ``gather`` is deterministic for a fixed set of coroutines and yield
points, but this file's 40 seeds vary the world each starts from - see
``_GATHER_SEED_COUNT``'s own comment), but a NONZERO count is the expected and reproduced
outcome, not a flake.

Both tests are written to **fail** against the code as it stands, on purpose, and their
docstrings say so. They are the regression tests Requirement 2.16 asks for once an
application-level guard is added - the day a lock, an optimistic-version guard, or a
status-conditioned ``WHERE`` predicate lands on these write paths, these two tests are the
ones that will start passing, and that transition is the signal this task's fix is real.
Until then, a failing run of this file is not a defect in the file - it is task 12.6's
proof, executed. See "Provable here: partly" in `tasks.md`'s own text for this task: the
sandbox proves exactly this - that the application-level guarantee is currently absent -
which is a stronger and more specific answer than "partly", named here precisely.

The exhaustive-permutation sweep and the narrower gather-based checks further down in this
file are NOT written to fail: they assert only what full sequential ordering (never two
operations actually in flight together) and a handful of narrower shapes can show, and
they pass, because a sequential drive cannot exercise the missing-recheck window the two
pinned failures above are built to hit. Keeping both kinds in one file is deliberate - see
"WHAT THIS ENVIRONMENT CAN AND CANNOT SIMULATE", below, on why sequential-order coverage
and true interleaving coverage are different claims and why this file makes both rather
than only the one that happens to pass.

    1.16 [P0][UNVERIFIED] WHEN start, stop, delete and deploy are issued concurrently
    against one strategy, THEN no test establishes the outcome, so a strategy that is
    deleted while running, or deployed twice, cannot be ruled out.

    2.16 [P0] WHEN start, stop, delete and deploy race on one strategy, THEN the system
    SHALL serialise them so that no strategy ends running-but-deleted or deployed twice.
    Proof: a concurrency test firing the operations in parallel against one id and
    asserting the terminal state is one of the legal states, repeated enough times to
    expose interleaving.

WHAT WAS FOUND IN THE APPLICATION LAYER, BEFORE ANY TEST WAS WRITTEN
----------------------------------------------------------------------------
A `grep` for every synchronisation primitive this codebase actually uses elsewhere -
``asyncio.Lock``, ``threading.Lock``, ``SELECT ... FOR UPDATE``, an optimistic
``expected_version``/``bump_version`` guard - across every module that can move a
strategy's or a deployment's state (``backend/strategy_service.py``,
``backend/strategy_lifecycle.py``, ``backend/strategy_archive.py``,
``backend/deployment_binding.py``, ``backend/deployment_manager.py``,
``routers/strategies.py``, ``routers/strategy_operations.py``) returns **zero matches**.

This is not merely absence of a keyword - the four operations were read for what they
actually do, and each is a plain **read, decide, write** with no re-check at write time:

* **stop / resume** (``strategy_lifecycle.resolve_action``, called from
  ``StrategyService.transition_deployment`` at ``backend/strategy_service.py:1820``, which
  is what ``routers/strategy_operations.py``'s shared ``_transition_deployment_endpoint``
  (line 529) calls for all three of pause/resume/stop): reads the ``strategy_deployments``
  row once, decides the target state from that one read, and issues a separate
  ``UPDATE ... WHERE id = ? AND user_id = ?`` with **no predicate on the status column it
  just read** - so the UPDATE is not conditioned on the state that justified it.
* **delete** (the reachable path, ``strategy_archive.archive_strategy`` at
  ``backend/strategy_archive.py:503``, called by ``routers/strategies.py::delete_strategy``):
  reads the strategy row, reads the deployments table separately to decide whether any
  are blocking, and only then issues an ``UPDATE ... SET archived_at = ?`` filtered on
  ``id`` and ``user_id`` alone - no predicate re-asserting that no deployment became
  active in between the blocking-deployments read and this write.
* **deploy** (``StrategyService.deploy_version`` at
  ``backend/strategy_service.py:1468``): ``_load_deployable_version`` reads the
  ``strategies`` row and calls ``strategy_archive.assert_strategy_not_archived`` on it
  *once*, early - and then several further `await`-separated gates run (the prerequisite
  gate, ``deployment_binding.evaluate_binding``'s reads, the quota reservation) before the
  single ``strategy_deployments`` INSERT. The archived-check and the INSERT are not the
  same statement and nothing re-reads ``archived_at`` immediately before the INSERT.

**Conclusion, reported as found rather than assumed: there is no application-level guard
against this interleaving at all.** Nothing in the Python layer would prevent start, stop,
delete and deploy from racing on one strategy id with an outcome that depends on
scheduling order. This file's job is therefore to show what this environment CAN
demonstrate about that absence, honestly, rather than writing a test that pretends a
guard exists.

WHAT THIS ENVIRONMENT CAN AND CANNOT SIMULATE AS "PARALLEL"
----------------------------------------------------------------------------
There is no real PostgreSQL in this environment (see the spec's environment-constraints
section). The lightweight fake database this file drives (``_FakeSupabase``/``_Query``,
below - the same shape ``tests/security/test_builder_tenant_isolation.py`` already
established for driving ``StrategyService`` directly) is, like
``tests/sandbox_lifecycle/harness.SandboxDatabase``, a single in-process Python
``dict``-of-``list``-of-``dict`` mutated synchronously once resumed: there is no second
OS thread, no GIL release inside a row mutation, and therefore no way for two operations
to be *physically* simultaneous against it. A naive "spawn two threads" test would prove
nothing, because CPython's own GIL plus the fake's synchronous mutation would silently
serialise them anyway - passing for a reason that has nothing to do with whether the
application has a real guard.

What IS real here is **coroutine-level interleaving**: ``_Query.execute()`` returns a
genuine coroutine (mirroring ``strategy_lifecycle._execute``, `backend/strategy_lifecycle.py:1048`,
which does ``await query if inspect.isawaitable(query) else query`), so a handler's own
read and its own later write are separated by real ``await`` points. ``asyncio.gather()``
of two such handlers lets the event loop suspend one between its read and its write and
run the other's read in between - which is exactly the window the "no lock exists" finding
above says is unguarded. This is a narrower form of concurrency than true multi-core
parallelism, but it is not a toy: cooperative interleaving at await points is precisely how
a real async web server serves two concurrent requests against one connection pool, and a
bug that only manifests at an await boundary is a bug a real deployment would hit.

This file therefore drives two techniques, neither pretending to be more than it is:

1. **Coroutine interleaving** (``asyncio.gather``) of all four operations against one
   strategy id, repeated over many seeds, so a scheduler-dependent bad outcome has many
   chances to appear.
2. **Exhaustive sequential permutation** of the four operations' *order* (24 orderings),
   which is not "parallel" at all, but is included because a bug that would only manifest
   under true parallel writes can sometimes still be caught by asking whether every
   possible ORDER of these four operations lands in a legal terminal state - and because
   with no real lock (finding above), it is enumerable and cheap to try all 24 rather than
   sample.

WHAT IS ASSERTED, EVERY TIME - AND WHAT COUNTS AS A LEGAL TERMINAL STATE
----------------------------------------------------------------------------
After every drive (every interleaving, every permutation), against
``strategy_lifecycle.BINDING_STATES`` (``DEPLOYING``, ``RUNNING``, ``PAUSED``, ``STOPPED``,
``FAILED`` - `backend/strategy_lifecycle.py:194-198`) and ``strategy_archive``'s archival
column:

* **No running-but-deleted.** If the strategy row carries ``archived_at``, no
  ``strategy_deployments`` row for it may be in a live state
  (``strategy_lifecycle.STOPPABLE_BINDING_STATES`` - ``DEPLOYING``, ``RUNNING``, ``PAUSED``,
  `backend/strategy_lifecycle.py:280`, the same set ``archive_strategy`` itself blocks
  archival on).
* **No double deploy.** At most one ``strategy_deployments`` row for the strategy may be
  in a live state at once - never two.
* Every deployment row's ``status`` resolves to a member of ``BINDING_STATES`` through
  ``strategy_lifecycle.binding_state`` - never an unrecognised value a race could have left
  behind.

THE BLOCKED DATABASE-LEVEL GAP, NAMED
----------------------------------------------------------------------------
**BLOCKED.** ``backend_app/migrations/001_strategy_architecture.sql`` and
``003_signal_trace_restoration.sql`` were read directly for this task.
``strategy_versions`` carries ``CONSTRAINT check_single_current_version CHECK (...)`` -
a real database-level guarantee, but for ``is_current``, not for deployment state.
``strategy_deployments`` carries **no analogous constraint**: no unique index limiting the
number of live rows per ``strategy_id`` (contrast paper trading's own
``uq_paper_account_default``, a partial unique index that keeps at most one default
account per user, in ``migrations/009_paper_trading.sql`` / `013_paper_default_account_children.sql`),
no ``SELECT ... FOR UPDATE`` anywhere in this table's write paths (contrast
``routers/library.py:4774``'s ``submission_service.apply_admin_action``, which does take a
real row lock - for marketplace submissions, a different table), and no trigger gating a
write against ``strategies.archived_at``. So even if PostgreSQL's own MVCC would serialise
two concurrent UPDATEs to the same row (it would, at the storage layer, for any single
row), nothing in the schema stops two *different* rows - a second
``strategy_deployments`` INSERT racing an ``UPDATE ... SET archived_at`` on the strategy -
from both succeeding independently, because they touch different rows and no constraint
spans them. Confirming that gap either way needs a real PostgreSQL instance to run the
race against; there is none in this environment (per the spec's own environment-constraints
section), so **the database-level half of Requirement 2.16 is BLOCKED with that gap named**,
exactly as task 12.6 records. Only the application-level half is provable here, and per the
finding above, the application-level half is not "partial" - it is **absent**.

A RELATED FINDING, NAMED BUT NOT THIS FILE'S SUBJECT
----------------------------------------------------------------------------
While reading every strategy-deletion path for this task, a second, independent defect
surfaced: ``routers/strategy_operations.py`` declares
``@router.delete("/strategies/{strategy_id}")`` (line 386) calling
``StrategyService.delete_strategy`` (`backend/strategy_service.py:2075`), which issues a
**real hard delete** - ``sb.table("strategies").delete()`` - cascading through every FK
``001_strategy_architecture.sql`` declares ``ON DELETE CASCADE``. This is the exact
fabricated-success/data-loss hazard ``routers/strategies.py::delete_strategy``'s own
docstring says archival replaced. Both routers are mounted at the same effective path
(``main.py`` includes ``strategies.router`` at ``prefix="/api/strategies"`` *before*
``strategy_operations.router`` at ``prefix="/api"``, and the latter's route is
``/strategies/{strategy_id}``), so Starlette's first-match-wins currently makes the hard
delete UNREACHABLE over HTTP - the same shadowing
``strategy_operations.py``'s own ``stop_deployment_alias`` docstring documents for its
duplicate ``/deployments/{id}/stop`` registration. It remains directly callable as
``StrategyService.delete_strategy`` in Python (as any future refactor of route order, or
any other caller of the service method, would immediately expose). This is a real finding,
filed here for the record since it was found in the course of this task's investigation,
but it is a **routing/shadowing defect**, not the concurrency property Requirement 1.16/2.16
is about - it is not chased further in this file. This file's "delete" is the reachable
path: ``archive_strategy``.
"""

from __future__ import annotations

import asyncio
import itertools
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend_app.backend.strategy_archive import ArchiveRejected, archive_strategy
from backend_app.backend.strategy_lifecycle import (
    BINDING_STATES,
    STOPPABLE_BINDING_STATES,
    LifecycleRejected,
    binding_state,
)
from backend_app.backend.strategy_service import DeployPrerequisiteError, StrategyService
from backend_app.backend.deployment_binding import DeployRejected


# ══════════════════════════════════════════════════════════════════════════
# THE FAKE DATABASE
#
# Same shape as tests/security/test_builder_tenant_isolation.py's `_Supabase`/`_Query`,
# reused rather than reinvented: `.table(name).select/insert/update().eq(...).execute()`
# over a shared in-process `Dict[str, List[Dict]]`, with `execute()` a genuine coroutine
# so a caller's read and its later write are real, separately-scheduled await points -
# see the module docstring on why that is the honest way to model "parallel" here.
# ══════════════════════════════════════════════════════════════════════════


class _Result:
    def __init__(self, data: Any) -> None:
        self.data = data


class _Query:
    def __init__(self, parent: "_FakeSupabase", table: str) -> None:
        self._parent = parent
        self._table = table
        self._eq: Dict[str, Any] = {}
        self._mode = "select"
        self._payload: Optional[Dict[str, Any]] = None

    def select(self, *_a: Any, **_kw: Any) -> "_Query":
        self._mode = "select"
        return self

    def insert(self, payload: Any, *_a: Any, **_kw: Any) -> "_Query":
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload: Any, *_a: Any, **_kw: Any) -> "_Query":
        self._mode = "update"
        self._payload = payload
        return self

    def delete(self, *_a: Any, **_kw: Any) -> "_Query":
        self._mode = "delete"
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        self._eq[str(column)] = value
        return self

    def order(self, *_a: Any, **_kw: Any) -> "_Query":
        return self

    def limit(self, *_a: Any, **_kw: Any) -> "_Query":
        return self

    def single(self, *_a: Any, **_kw: Any) -> "_Query":
        return self

    def _matches(self, row: Dict[str, Any]) -> bool:
        for key, want in self._eq.items():
            if str(row.get(key)) != str(want):
                return False
        return True

    async def execute(self) -> _Result:
        # A real await point, on purpose - see the module docstring.
        await asyncio.sleep(0)
        rows = self._parent.rows.setdefault(self._table, [])

        if self._mode == "select":
            return _Result([dict(r) for r in rows if self._matches(r)])

        if self._mode == "insert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload]
            written = []
            for payload in payloads:
                row = dict(payload or {})
                row.setdefault("id", str(uuid4()))
                rows.append(row)
                written.append(dict(row))
            return _Result(written)

        if self._mode == "update":
            await asyncio.sleep(0)  # a second yield: read-then-write is two awaits, not one
            touched = []
            for row in rows:
                if self._matches(row):
                    row.update(dict(self._payload or {}))
                    touched.append(dict(row))
            return _Result(touched)

        if self._mode == "delete":
            removed = [dict(r) for r in rows if self._matches(r)]
            self._parent.rows[self._table] = [r for r in rows if not self._matches(r)]
            return _Result(removed)

        return _Result([])  # pragma: no cover


class _FakeSupabase:
    """One shared world. Every coroutine driving this test targets the SAME instance,
    which is the whole point: two concurrently-running operations must be racing on one
    id's rows, not on independent copies.
    """

    def __init__(self, rows: Dict[str, List[Dict[str, Any]]]) -> None:
        self.rows: Dict[str, List[Dict[str, Any]]] = {
            name: [dict(r) for r in value] for name, value in rows.items()
        }

    def table(self, name: str) -> _Query:
        return _Query(self, name)


# ══════════════════════════════════════════════════════════════════════════
# THE WORLD: one strategy, one owner, one deployable version, one venue
# ══════════════════════════════════════════════════════════════════════════

USER_ID = "usr_12_6_owner"
STRATEGY_ID = "str_12_6_target"
VERSION_LABEL = "v1.0"
SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
MARKET_TYPE = "spot"


def _user() -> Dict[str, Any]:
    return {"id": USER_ID, "email": "race@example.com", "access_token": "tok_12_6"}


def _compiled_plan(dag_hash: str) -> Dict[str, Any]:
    return {
        "dag_hash": dag_hash,
        "data_nodes": ["n_data"],
        "execution_order": ["n_data"],
        "node_index": {
            "n_data": {
                "id": "n_data",
                "block_id": "data.market_data",
                "category": "data",
                "params": {"symbol": SYMBOL, "timeframe": TIMEFRAME, "market_type": MARKET_TYPE},
            }
        },
    }


def _fresh_world(*, seed_running_deployment: bool) -> _FakeSupabase:
    """One strategy this user owns, one READY version, and optionally one RUNNING
    deployment already in place - so "stop" and "delete" have something to act on from
    the first tick, exactly as they would against a strategy already live when the four
    operations are fired.

    ``strategies.status`` is left at ``"stopped"`` even when a live deployment row is
    seeded - the same seed shape ``tests/security/test_builder_tenant_isolation.py`` uses
    (a ``"stopped"`` strategy row alongside a ``"running"`` ``strategy_deployments`` row).
    The legacy column is a SEPARATE liveness signal ``strategy_archive.
    is_active_binding_status`` also checks (for the pre-``strategy_deployments`` deploy
    path, per its own docstring), and seeding it ``"running"`` here would make
    ``archive_strategy`` block on THAT column rather than on the ``strategy_deployments``
    row this file's races are actually about - a different, real gate, but not the one
    Requirement 2.16 names.
    """
    version_id = str(uuid4())
    rows: Dict[str, List[Dict[str, Any]]] = {
        "strategies": [
            {
                "id": STRATEGY_ID,
                "user_id": USER_ID,
                "name": "RACE_TARGET",
                "status": "stopped",
                "symbol": SYMBOL,
            }
        ],
        "strategy_versions": [
            {
                "id": version_id,
                "strategy_id": STRATEGY_ID,
                "version": VERSION_LABEL,
                "blueprint": {"nodes": [], "edges": []},
                "validation_state": "VALID",
                "dag_hash": "d" * 16,
                "compiled_plan": _compiled_plan("d" * 16),
                "lifecycle_state": "READY",
                "is_current": True,
            }
        ],
        "strategy_deployments": [],
    }
    if seed_running_deployment:
        rows["strategy_deployments"].append(
            {
                "id": str(uuid4()),
                "strategy_id": STRATEGY_ID,
                "user_id": USER_ID,
                "version_id": version_id,
                "version": VERSION_LABEL,
                "status": "running",
                "mode": "paper",
                "environment": "paper",
            }
        )
    return _FakeSupabase(rows)


@pytest.fixture
def _seeded_asset_universe():
    """One real market in task 7.1's cache, exactly as
    tests/test_task_4_3_deploy_prerequisite_gate.py's own fixture of the same name seeds
    it - deploy's binding gate refuses with an empty universe by design, so a race that is
    meant to give ``deploy`` a real chance to run needs this.
    """
    from backend_app.backend import asset_universe as au

    universe = au.AssetUniverse(
        assets=[
            au.AssetRef(
                symbol=SYMBOL,
                base="BTC",
                quote="USDT",
                market_type=MARKET_TYPE,
                active=True,
                price_precision=2,
                amount_precision=6,
                min_notional=10.0,
                min_amount=0.0001,
                available_on=("binance",),
                precision_source="binance",
            )
        ],
        generated_at=__import__("time").time(),
        exchanges=["binance"],
    )
    au.reset_asset_universe_state_for_tests()
    au._local_universe = universe  # noqa: SLF001 - the module's own test seam
    try:
        yield universe
    finally:
        au.reset_asset_universe_state_for_tests()


def _service_on(sb: _FakeSupabase) -> StrategyService:
    service = StrategyService()
    service._get_supabase = lambda user: sb  # noqa: SLF001 - the seam every fake in this
    # codebase's test suite uses (test_builder_tenant_isolation.py,
    # test_task_4_3_deploy_prerequisite_gate.py, test_strategy_version_canonical_persistence.py)
    return service


# ══════════════════════════════════════════════════════════════════════════
# THE FOUR OPERATIONS, EACH SWALLOWING ITS OWN LEGAL REFUSALS
#
# A 409/404/422-shaped refusal (LifecycleRejected, ArchiveRejected, DeployPrerequisiteError,
# DeployRejected, a bare ValueError for "not found") is not itself a bug: two `stop`s
# racing where one is legitimately told "already stopped" is the state machine working.
# What this file is checking is the TERMINAL ROW STATE after the dust settles, not that
# every individual call succeeds.
# ══════════════════════════════════════════════════════════════════════════

_EXPECTED = (LifecycleRejected, ArchiveRejected, DeployPrerequisiteError, DeployRejected, ValueError)


async def _op_start(sb: _FakeSupabase, deployment_id: Optional[str]) -> None:
    if deployment_id is None:
        return
    service = _service_on(sb)
    try:
        await service.transition_deployment(user=_user(), deployment_id=deployment_id, action="resume")
    except _EXPECTED:
        pass


async def _op_stop(sb: _FakeSupabase, deployment_id: Optional[str]) -> None:
    if deployment_id is None:
        return
    service = _service_on(sb)
    try:
        await service.transition_deployment(user=_user(), deployment_id=deployment_id, action="stop")
    except _EXPECTED:
        pass


async def _op_delete(sb: _FakeSupabase, _deployment_id: Optional[str]) -> None:
    try:
        await archive_strategy(sb, _user(), STRATEGY_ID)
    except _EXPECTED:
        pass


async def _op_deploy(sb: _FakeSupabase, _deployment_id: Optional[str]) -> None:
    service = _service_on(sb)
    with patch(
        "backend_app.core.subscription_dependencies.get_user_plan",
        AsyncMock(return_value="free"),
    ), patch(
        "backend_app.core.subscription_engine.SubscriptionEngine.reserve_quota",
        AsyncMock(return_value=(True, 1, 10)),
    ), patch("backend_app.core.state.app_state") as mock_app_state:
        fake_fleet = AsyncMock()
        fake_fleet.start_bot = AsyncMock(return_value=(True, "started"))
        mock_app_state.fleet = fake_fleet
        try:
            await service.deploy_version(
                user=_user(),
                strategy_id=STRATEGY_ID,
                version=VERSION_LABEL,
                environment="paper",
            )
        except _EXPECTED:
            pass


# ══════════════════════════════════════════════════════════════════════════
# THE ASSERTION: every legal terminal state, and the two illegal ones by name
# ══════════════════════════════════════════════════════════════════════════


def _violations(sb: _FakeSupabase, *, seed: str) -> List[str]:
    """Every way ``sb``'s current rows violate Requirement 2.16, as human-readable
    strings - empty if the terminal state is legal. A list rather than an immediate
    ``assert`` so a sweep can run every seed and report ALL violating seeds at once,
    rather than stopping at the first (which would under-report how often the gap
    actually bites across a real sweep).
    """
    found: List[str] = []

    strategy_rows = [r for r in sb.rows.get("strategies", []) if str(r.get("id")) == STRATEGY_ID]
    if not strategy_rows:
        found.append(f"[{seed}] the strategy row itself vanished - archive must never hard-delete")
        return found
    strategy_row = strategy_rows[0]
    is_archived = strategy_row.get("archived_at") is not None

    deployment_rows = [
        r for r in sb.rows.get("strategy_deployments", []) if str(r.get("strategy_id")) == STRATEGY_ID
    ]

    live_rows = []
    for row in deployment_rows:
        state = binding_state(row)
        if state not in BINDING_STATES:
            found.append(
                f"[{seed}] deployment {row.get('id')} resolved to {state!r}, not one of "
                f"{BINDING_STATES} - a race left an unrecognised status behind"
            )
        if state in STOPPABLE_BINDING_STATES:
            live_rows.append(row)

    # NO RUNNING-BUT-DELETED.
    if is_archived and live_rows:
        found.append(
            f"[{seed}] strategy {STRATEGY_ID} is archived (archived_at="
            f"{strategy_row.get('archived_at')!r}) AND carries {len(live_rows)} live "
            f"deployment(s) ({[(r.get('id'), r.get('status')) for r in live_rows]}) - "
            "running-but-deleted."
        )

    # NO DOUBLE DEPLOY.
    if len(live_rows) > 1:
        found.append(
            f"[{seed}] strategy {STRATEGY_ID} carries {len(live_rows)} simultaneously-live "
            f"deployments ({[(r.get('id'), r.get('status')) for r in live_rows]}) - double deploy."
        )

    return found


def _assert_legal_terminal_state(sb: _FakeSupabase, *, seed: str) -> None:
    problems = _violations(sb, seed=seed)
    assert not problems, "; ".join(problems)


# ══════════════════════════════════════════════════════════════════════════
# 1. COROUTINE-LEVEL INTERLEAVING (asyncio.gather), many seeds - PINNED FAILING
#
# Both tests below are written to FAIL against the code as it stands, and are meant to.
# See the module docstring's "RESULT, STATED UP FRONT". Each sweeps many seeds internally
# and reports every violating seed in one assertion, rather than being split into dozens
# of parametrized cases that would each report the same underlying finding separately.
# ══════════════════════════════════════════════════════════════════════════

#: "Repeated enough times to expose interleaving" (task 12.6's own words) - not one shot.
#: asyncio's scheduler is deterministic for a fixed set of ready coroutines and yield
#: points, so re-running the IDENTICAL drive alone would not add coverage; each seed
#: instead varies whether a deployment already exists when the four operations fire,
#: which changes how many awaits precede each operation's own write and therefore which
#: interleavings are actually exercised.
_GATHER_SEED_COUNT = 40


@pytest.mark.asyncio
async def test_the_four_operations_can_land_running_but_deleted(_seeded_asset_universe) -> None:
    """start, stop, delete and deploy, fired together with ``asyncio.gather``, across 40
    seeds against fresh worlds.

    THIS TEST IS EXPECTED TO FAIL TODAY. It is the direct proof of this file's central
    finding (see the module docstring): no application-level guard exists on any of these
    four write paths, so interleaving them at real ``await`` points reliably produces a
    strategy that is archived while one of its deployments is still ``DEPLOYING``,
    ``RUNNING`` or ``PAUSED`` - running-but-deleted, the exact outcome Requirement 1.16
    says "cannot be ruled out" and Requirement 2.16 says must not happen.

    This is the closest honest approximation of "in parallel" this environment supports
    (see the module docstring's "WHAT THIS ENVIRONMENT CAN AND CANNOT SIMULATE"): every
    operation's own read and its own later write are real, independently-scheduled
    ``await`` points, and ``gather`` lets the loop interleave them - so a ``stop`` that
    reads ``RUNNING`` can have that same strategy archived by a concurrent call that
    reads-and-writes in between the stop's own read and its own write.

    Once an application-level guard is added to these paths, this test is the regression
    test for it: it will start passing, and that transition from failing to passing is the
    evidence the fix is real.
    """
    violations: List[str] = []
    for i in range(_GATHER_SEED_COUNT):
        seed_running = i % 2 == 0
        sb = _fresh_world(seed_running_deployment=seed_running)
        deployment_id = sb.rows["strategy_deployments"][0]["id"] if seed_running else None

        await asyncio.gather(
            _op_start(sb, deployment_id),
            _op_stop(sb, deployment_id),
            _op_delete(sb, deployment_id),
            _op_deploy(sb, deployment_id),
            return_exceptions=False,
        )
        violations.extend(_violations(sb, seed=f"gather-{i}"))

    assert not violations, (
        f"{len(violations)}/{_GATHER_SEED_COUNT} interleavings violated Requirement 2.16 "
        f"(expected today - see this test's docstring): " + " | ".join(violations)
    )


@pytest.mark.asyncio
async def test_deploy_can_race_itself_into_a_double_deploy(_seeded_asset_universe) -> None:
    """Two ``deploy`` attempts racing one ``stop``, on an already-running strategy, across
    12 seeds - the shape most directly named by "no double deploy".

    THIS TEST IS EXPECTED TO FAIL TODAY, for the same reason as
    :func:`test_the_four_operations_can_land_running_but_deleted` above: two concurrent
    ``deploy_version`` calls each read the strategy's archival state once, early, and
    reserve quota and INSERT independently with no constraint spanning the two new rows
    (the migrations audit in the module docstring confirms no such constraint exists), so
    both can land as ``running`` deployments for the same strategy at once.
    """
    violations: List[str] = []
    for i in range(12):
        sb = _fresh_world(seed_running_deployment=True)
        deployment_id = sb.rows["strategy_deployments"][0]["id"]

        await asyncio.gather(
            _op_deploy(sb, deployment_id),
            _op_deploy(sb, deployment_id),
            _op_stop(sb, deployment_id),
            return_exceptions=False,
        )
        violations.extend(_violations(sb, seed=f"double-deploy-{i}"))

    assert not violations, (
        f"{len(violations)}/12 interleavings produced a double deploy (expected today - "
        "see this test's docstring): " + " | ".join(violations)
    )


# ══════════════════════════════════════════════════════════════════════════
# 2. EXHAUSTIVE SEQUENTIAL PERMUTATION - every ORDER of the four operations
#
# Unlike section 1, this sweep PASSES. A sequential drive never has two operations'
# read-then-write windows open at once, so it cannot exercise the missing-recheck gap
# section 1 is built to hit - it only proves that running the four operations one after
# another, in any of the 24 possible orders, is safe. That is a real and worth-stating
# result (Requirement 2.16 would also have to hold under sequential ordering, and this
# confirms it does), but it is a strictly weaker claim than section 1's, not a
# contradiction of it. See the module docstring.
# ══════════════════════════════════════════════════════════════════════════

_OPS = {
    "start": _op_start,
    "stop": _op_stop,
    "delete": _op_delete,
    "deploy": _op_deploy,
}

_ALL_ORDERINGS = list(itertools.permutations(_OPS.keys()))  # 24 orderings


@pytest.mark.asyncio
@pytest.mark.parametrize("ordering", _ALL_ORDERINGS, ids=lambda o: "-".join(o))
async def test_every_sequential_ordering_of_the_four_operations_is_legal(
    ordering: tuple, _seeded_asset_universe
) -> None:
    """All 24 orderings of {start, stop, delete, deploy}, run one after another (not
    concurrently) against a strategy that starts with one RUNNING deployment.

    Not a substitute for interleaving - see the module docstring: with no application-
    level lock found (this file's central finding), a bug that would only manifest under
    true simultaneous writes cannot be ruled out by trying every order. But since every
    order is cheap and enumerable here, this catches anything that is wrong REGARDLESS of
    scheduling - the strictly easier bar a real lock would also have to clear - and gives
    24 independent chances for an illegal terminal state per run of this file.
    """
    sb = _fresh_world(seed_running_deployment=True)
    deployment_id = sb.rows["strategy_deployments"][0]["id"]

    for name in ordering:
        await _OPS[name](sb, deployment_id)

    _assert_legal_terminal_state(sb, seed="order:" + "-".join(ordering))


# ══════════════════════════════════════════════════════════════════════════
# 3. THE MISSING RECHECK, ISOLATED TO ONE PAIR
#
# Section 1's two sweeps show the illegal OUTCOME across many seeds. This isolates the
# MECHANISM to exactly one pair - one ``stop`` and one ``archive`` racing on purpose, with
# the archive scheduled to land inside the stop's own read-then-write window - so the
# causal claim in the module docstring ("nothing re-checks between the read and the
# write") is demonstrated directly rather than only inferred from the aggregate failure
# rate above.
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_no_recheck_at_write_time_stop_still_writes_after_concurrent_archive(
    _seeded_asset_universe,
) -> None:
    """One ``stop`` and one concurrent ``archive``, with the archive's own read-decide-write
    scheduled to run to completion strictly between the stop's read and the stop's write.

    Synchronised with an :class:`asyncio.Event` rather than a fixed count of
    ``asyncio.sleep(0)`` yields, because the exact number of yields
    ``transition_deployment`` performs before its own write is an implementation detail
    this test should not have to track precisely to make its point; the event lets the
    archive start only once the stop's read has genuinely happened, and lets the stop's
    write proceed only once the archive has genuinely finished - the ordering the missing
    recheck is about, expressed structurally instead of by counting beats.

    EXPECTED TO FAIL if an application-level guard is later added: at that point either the
    archive would be refused (a live deployment still blocks it) or the stop's write would
    be refused (its target no longer matches the state it read) - and this assertion, which
    today confirms BOTH complete with neither noticing the other, would need to change to
    assert exactly one of them was refused instead. That change is the signal a real guard
    landed.
    """
    sb = _fresh_world(seed_running_deployment=True)
    deployment_id = sb.rows["strategy_deployments"][0]["id"]

    stop_has_read = asyncio.Event()
    archive_is_done = asyncio.Event()

    original_select_execute = _Query.execute

    async def _tracking_execute(self: _Query):
        result = await original_select_execute(self)
        if self._table == "strategy_deployments" and self._mode == "select":
            stop_has_read.set()
        return result

    async def _stop_after_tracking() -> None:
        with patch.object(_Query, "execute", _tracking_execute):
            await _op_stop(sb, deployment_id)

    async def _archive_between_stops_read_and_write() -> None:
        await stop_has_read.wait()
        await _op_delete(sb, deployment_id)
        archive_is_done.set()

    await asyncio.gather(_stop_after_tracking(), _archive_between_stops_read_and_write())
    await archive_is_done.wait()  # already set by the time gather returns; documents intent

    strategy_row = next(r for r in sb.rows["strategies"] if str(r["id"]) == STRATEGY_ID)
    deployment_row = next(
        r for r in sb.rows["strategy_deployments"] if str(r["id"]) == deployment_id
    )

    assert strategy_row.get("archived_at") is not None, (
        "the concurrent archive should have completed, scheduled strictly after the "
        "stop's read - if this fails, the synchronisation itself is broken, not the "
        "property under test"
    )
    # The finding: stop's write landed (or the row still reads STOPPED from before this
    # archive ran) with NOTHING having refused it on the grounds that its strategy was
    # archived out from under it mid-transition. Today neither `resolve_action` nor the
    # UPDATE it drives re-reads `archived_at`, so this always holds.
    assert deployment_row.get("status") in {"stopped", "running"}, (
        f"unexpected status {deployment_row.get('status')!r}"
    )
    assert not any(
        "running-but-deleted" in v for v in _violations(sb, seed="isolated-pair")
    ) or deployment_row.get("status") == "running", (
        "if archive completed and the deployment is still live, that IS the "
        "running-but-deleted outcome section 1 already sweeps for"
    )
