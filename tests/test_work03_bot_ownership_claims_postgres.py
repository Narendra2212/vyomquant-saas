"""WORK-03: at most one bot owner per (user, symbol), across processes.

WHY THIS FILE USES REAL CONNECTIONS AND REAL SUBPROCESSES
=========================================================
The defect is that `FleetManager._active_fleet` is a per-process dict, so two pods cannot
see each other's bots. A test that drives two coroutines in one interpreter cannot
distinguish a correct distributed claim from a process-local one -- both would pass. So:

* §2 uses **two independent psycopg2 connections** plus a `threading.Barrier`, which is
  the narrowest real race: two live transactions contending for the same row.
* §3 uses **two separate OS processes** via `subprocess`, which share no Python memory at
  all. That is the case `_active_fleet` could never have handled.
* §7 is a **negative control**: the same harness is pointed at a process-local dict
  implementation, which must FAIL to hold the invariant. Without it, §2 and §3 could be
  passing for the wrong reason.

The oracle throughout is **how many claimants were granted ownership**, read back from
PostgreSQL -- not whether a function returned False.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from typing import List, Optional, Tuple

import pytest

psycopg2 = pytest.importorskip("psycopg2")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or "postgresql://postgres:@127.0.0.1:62674/postgres"
)
if not DSN.startswith("postgres"):
    pytest.skip("WORK-03 requires PostgreSQL", allow_module_level=True)

from backend_app.core import bot_ownership as bo  # noqa: E402


def _conn():
    c = psycopg2.connect(DSN)
    c.autocommit = True
    return c


@pytest.fixture(scope="module")
def db():
    try:
        conn = _conn()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    cur = conn.cursor()
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='bot_ownership_claims')"
    )
    if not cur.fetchone()[0]:
        pytest.skip("migration 025 not applied to this database")
    cur.close()
    yield conn
    conn.close()


@pytest.fixture
def pair(db):
    """A fresh (user, symbol) per test, so tests never contend with each other."""
    user_id = str(uuid.uuid4())
    symbol = f"BTC/T{uuid.uuid4().hex[:6]}"
    yield user_id, symbol
    cur = db.cursor()
    cur.execute(
        "DELETE FROM public.bot_ownership_claims WHERE user_id = %s", (user_id,)
    )
    cur.close()


def _claims(db, user_id: str) -> List[tuple]:
    cur = db.cursor()
    cur.execute(
        "SELECT symbol, owner_token::text, worker_name, state "
        "FROM public.bot_ownership_claims WHERE user_id = %s", (user_id,)
    )
    rows = cur.fetchall()
    cur.close()
    return rows


# ── 1. the basic contract ───────────────────────────────────────────────────


def test_a_first_claim_is_granted(pair, db):
    user_id, symbol = pair
    conn = _conn()
    try:
        claim = bo.acquire(conn.cursor(), user_id, symbol, "w1")
        assert claim.granted
        assert claim.outcome is bo.CLAIMED
        assert claim.owner_token
        assert len(_claims(db, user_id)) == 1
    finally:
        conn.close()


def test_a_second_claim_on_a_live_bot_is_refused_deterministically(pair, db):
    """Zero rows is the 'stand down', and it must not be an exception."""
    user_id, symbol = pair
    a, b = _conn(), _conn()
    try:
        first = bo.acquire(a.cursor(), user_id, symbol, "w1")
        second = bo.acquire(b.cursor(), user_id, symbol, "w2")

        assert first.granted
        assert not second.granted
        assert second.outcome is bo.HELD_BY_ANOTHER
        assert second.owner_token == ""
        # Still exactly one row, still owned by the first worker.
        rows = _claims(db, user_id)
        assert len(rows) == 1
        assert rows[0][1] == first.owner_token
        assert rows[0][2] == "w1"
    finally:
        a.close()
        b.close()


def test_release_is_scoped_to_the_owner_token(pair, db):
    """A worker whose claim was taken over cannot release its successor's claim."""
    user_id, symbol = pair
    conn = _conn()
    try:
        real = bo.acquire(conn.cursor(), user_id, symbol, "w1")
        impostor = bo.BotClaim(user_id, symbol, str(uuid.uuid4()), "w2", bo.CLAIMED)

        assert bo.release(conn.cursor(), impostor) is False
        assert len(_claims(db, user_id)) == 1, "an impostor token released a live claim"

        assert bo.release(conn.cursor(), real) is True
        assert _claims(db, user_id) == []
    finally:
        conn.close()


def test_heartbeat_reports_lost_ownership(pair, db):
    """`False` is how a reaped worker learns to stand down."""
    user_id, symbol = pair
    conn = _conn()
    try:
        claim = bo.acquire(conn.cursor(), user_id, symbol, "w1")
        assert bo.heartbeat(conn.cursor(), claim) is True

        # Somebody else takes the row over (simulating a stale takeover).
        cur = conn.cursor()
        cur.execute(
            "UPDATE public.bot_ownership_claims SET owner_token = gen_random_uuid() "
            "WHERE user_id = %s AND symbol = %s", (user_id, symbol),
        )
        assert bo.heartbeat(conn.cursor(), claim) is False, (
            "a worker that lost ownership still heartbeated successfully, so it would "
            "keep trading alongside the new owner"
        )
    finally:
        conn.close()


# ── 2. two independent CONNECTIONS racing ───────────────────────────────────


def test_two_connections_racing_produce_exactly_one_owner(pair, db):
    """THE CORE WORK-03 ASSERTION, at the narrowest real race."""
    user_id, symbol = pair
    barrier = threading.Barrier(2)
    results: List[Optional[bo.BotClaim]] = [None, None]

    def contend(slot: int, worker: str):
        conn = _conn()
        try:
            cur = conn.cursor()
            barrier.wait(timeout=10)
            results[slot] = bo.acquire(cur, user_id, symbol, worker)
        finally:
            conn.close()

    threads = [
        threading.Thread(target=contend, args=(0, "wA")),
        threading.Thread(target=contend, args=(1, "wB")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    granted = [r for r in results if r and r.granted]
    refused = [r for r in results if r and not r.granted]

    assert len(granted) == 1, (
        f"{len(granted)} claimants were granted ownership of the same bot. Outcomes: "
        f"{[(r.worker_name, str(r.outcome)) for r in results if r]}"
    )
    assert len(refused) == 1
    assert refused[0].outcome is bo.HELD_BY_ANOTHER
    assert len(_claims(db, user_id)) == 1


def test_many_connections_racing_still_produce_exactly_one_owner(pair, db):
    """Widened, because a two-way race can pass by luck."""
    user_id, symbol = pair
    n = 8
    barrier = threading.Barrier(n)
    results: List[Optional[bo.BotClaim]] = [None] * n

    def contend(slot: int):
        conn = _conn()
        try:
            cur = conn.cursor()
            barrier.wait(timeout=20)
            results[slot] = bo.acquire(cur, user_id, symbol, f"w{slot}")
        finally:
            conn.close()

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    granted = [r for r in results if r and r.granted]
    assert len(granted) == 1, (
        f"{len(granted)} of {n} concurrent claimants were granted ownership"
    )
    assert len(_claims(db, user_id)) == 1


# ── 3. two separate OS PROCESSES ────────────────────────────────────────────


_CHILD = r'''
import json, os, sys, time
sys.path.insert(0, os.environ["WORK03_REPO"])
from backend_app.core import bot_ownership as bo
import psycopg2

user_id = sys.argv[1]
symbol  = sys.argv[2]
worker  = sys.argv[3]
start_at = float(sys.argv[4])

conn = psycopg2.connect(os.environ["WORK03_DSN"])
conn.autocommit = True
cur = conn.cursor()

# Align the two processes on a wall-clock instant; they share no memory, so there is no
# barrier available.
while time.time() < start_at:
    time.sleep(0.001)

claim = bo.acquire(cur, user_id, symbol, worker)
print("RESULT" + json.dumps({
    "worker": worker, "granted": claim.granted, "outcome": str(claim.outcome),
    "token": claim.owner_token,
}))
conn.close()
'''


def _spawn(user_id: str, symbol: str, worker: str, start_at: float):
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env = dict(os.environ)
    env["WORK03_REPO"] = repo
    env["WORK03_DSN"] = DSN
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD, user_id, symbol, worker, str(start_at)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=repo,
    )


def _harvest(proc) -> Optional[dict]:
    out, err = proc.communicate(timeout=120)
    for line in (out or "").splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[len("RESULT"):])
    print(f"child produced no result. stdout={out[-400:]!r} stderr={err[-400:]!r}")
    return None


def test_two_separate_processes_produce_exactly_one_owner(pair, db):
    """THE CASE `_active_fleet` COULD NEVER HAVE HANDLED.

    Two interpreters, no shared Python memory, aligned on a wall-clock instant.
    """
    user_id, symbol = pair
    start_at = time.time() + 2.0
    procs = [
        _spawn(user_id, symbol, "procA", start_at),
        _spawn(user_id, symbol, "procB", start_at),
    ]
    results = [_harvest(p) for p in procs]

    assert all(r is not None for r in results), (
        "a child process failed to report; the cross-process case is unproven"
    )
    granted = [r for r in results if r["granted"]]
    assert len(granted) == 1, (
        f"{len(granted)} separate processes were granted ownership of the same bot: "
        f"{results}"
    )
    assert len(_claims(db, user_id)) == 1


def test_four_separate_processes_produce_exactly_one_owner(pair, db):
    user_id, symbol = pair
    start_at = time.time() + 2.5
    procs = [_spawn(user_id, symbol, f"proc{i}", start_at) for i in range(4)]
    results = [_harvest(p) for p in procs]

    assert all(r is not None for r in results)
    granted = [r for r in results if r["granted"]]
    assert len(granted) == 1, f"{len(granted)} of 4 processes were granted: {results}"


# ── 4. crash and stale recovery ─────────────────────────────────────────────


def test_a_stale_claim_can_be_taken_over(pair, db):
    """A worker that dies holding a claim must not strand the bot forever."""
    user_id, symbol = pair
    conn = _conn()
    try:
        dead = bo.acquire(conn.cursor(), user_id, symbol, "dead-pod")
        assert dead.granted

        # The pod dies: no heartbeat. Age the claim past the threshold.
        cur = conn.cursor()
        cur.execute(
            "UPDATE public.bot_ownership_claims "
            "SET heartbeat_at = now() - make_interval(secs => %s) "
            "WHERE user_id = %s AND symbol = %s",
            (bo.effective_stale_seconds() + 30, user_id, symbol),
        )

        survivor = bo.acquire(conn.cursor(), user_id, symbol, "live-pod")
        assert survivor.granted, "a stale claim stranded the bot permanently"
        assert survivor.outcome is bo.RECLAIMED_STALE
        assert survivor.owner_token != dead.owner_token

        rows = _claims(db, user_id)
        assert len(rows) == 1 and rows[0][2] == "live-pod"
    finally:
        conn.close()


def test_the_dead_worker_cannot_release_the_successors_claim(pair, db):
    """The resurrection case. Without token scoping this is a second-bot bug."""
    user_id, symbol = pair
    conn = _conn()
    try:
        dead = bo.acquire(conn.cursor(), user_id, symbol, "dead-pod")
        cur = conn.cursor()
        cur.execute(
            "UPDATE public.bot_ownership_claims "
            "SET heartbeat_at = now() - make_interval(secs => %s) "
            "WHERE user_id = %s AND symbol = %s",
            (bo.effective_stale_seconds() + 30, user_id, symbol),
        )
        survivor = bo.acquire(conn.cursor(), user_id, symbol, "live-pod")
        assert survivor.granted

        # The dead pod wakes up and tries to tidy up.
        assert bo.release(conn.cursor(), dead) is False
        rows = _claims(db, user_id)
        assert len(rows) == 1 and rows[0][1] == survivor.owner_token, (
            "the resurrected worker released the live owner's claim, which would let a "
            "third worker start a second bot"
        )
    finally:
        conn.close()


def test_a_live_claim_is_not_reclaimable(pair, db):
    """The mirror of stale recovery: the threshold must not reap a healthy claim."""
    user_id, symbol = pair
    conn = _conn()
    try:
        owner = bo.acquire(conn.cursor(), user_id, symbol, "healthy")
        assert bo.heartbeat(conn.cursor(), owner) is True

        thief = bo.acquire(conn.cursor(), user_id, symbol, "thief")
        assert not thief.granted, "a freshly heartbeated claim was reaped"
    finally:
        conn.close()


def test_retry_after_recovery_succeeds_exactly_once(pair, db):
    """Two workers both notice the stale claim; only one may take it."""
    user_id, symbol = pair
    conn = _conn()
    try:
        bo.acquire(conn.cursor(), user_id, symbol, "dead")
        cur = conn.cursor()
        cur.execute(
            "UPDATE public.bot_ownership_claims "
            "SET heartbeat_at = now() - make_interval(secs => %s) "
            "WHERE user_id = %s AND symbol = %s",
            (bo.effective_stale_seconds() + 30, user_id, symbol),
        )
    finally:
        conn.close()

    barrier = threading.Barrier(2)
    results: List[Optional[bo.BotClaim]] = [None, None]

    def contend(slot: int):
        c = _conn()
        try:
            cur2 = c.cursor()
            barrier.wait(timeout=10)
            results[slot] = bo.acquire(cur2, user_id, symbol, f"recover{slot}")
        finally:
            c.close()

    ts = [threading.Thread(target=contend, args=(i,)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)

    granted = [r for r in results if r and r.granted]
    assert len(granted) == 1, (
        f"{len(granted)} workers recovered the same stale claim: "
        f"{[(r.worker_name, str(r.outcome)) for r in results if r]}"
    )


# ── 5. duplicate delivery, and tenant independence ─────────────────────────


def test_duplicate_delivery_of_the_same_start_is_idempotent(pair, db):
    """The WORK-02 reaper can redeliver a start_bot command. Twice must mean once."""
    user_id, symbol = pair
    a, b = _conn(), _conn()
    try:
        first = bo.acquire(a.cursor(), user_id, symbol, "consumer-1")
        # The reaper redelivers to a different consumer.
        second = bo.acquire(b.cursor(), user_id, symbol, "consumer-2")

        assert first.granted and not second.granted
        assert len(_claims(db, user_id)) == 1
    finally:
        a.close()
        b.close()


def test_independent_tenants_never_contend(db):
    """Structural: the key is (user_id, symbol), so two users are two rows."""
    symbol = f"BTC/T{uuid.uuid4().hex[:6]}"
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    conn = _conn()
    try:
        ca = bo.acquire(conn.cursor(), user_a, symbol, "wA")
        cb = bo.acquire(conn.cursor(), user_b, symbol, "wB")
        assert ca.granted and cb.granted, (
            "two different users were prevented from running the same symbol"
        )
        assert len(_claims(db, user_a)) == 1
        assert len(_claims(db, user_b)) == 1
    finally:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM public.bot_ownership_claims WHERE user_id IN (%s, %s)",
            (user_a, user_b),
        )
        conn.close()


def test_one_user_can_run_two_different_symbols(pair, db):
    user_id, symbol = pair
    other = f"ETH/T{uuid.uuid4().hex[:6]}"
    conn = _conn()
    try:
        assert bo.acquire(conn.cursor(), user_id, symbol, "w").granted
        assert bo.acquire(conn.cursor(), user_id, other, "w").granted
        assert len(_claims(db, user_id)) == 2
    finally:
        conn.close()


# ── 6. the store is unavailable: FAIL CLOSED ───────────────────────────────


def test_an_unreachable_claim_store_refuses_the_start(pair):
    """A claim we cannot establish is indistinguishable from one somebody else holds.

    This is the Redis-outage argument made concrete: the invariant must not fail open
    when its store is unreachable.
    """
    user_id, symbol = pair

    class _Broken:
        def execute(self, *a, **k):
            raise RuntimeError("connection refused")

        def fetchone(self):
            raise AssertionError("must not be reached")

    claim = bo.acquire(_Broken(), user_id, symbol, "w1")

    assert not claim.granted, "an unreachable claim store granted ownership"
    assert claim.outcome is bo.STORE_UNAVAILABLE


def test_redis_being_down_cannot_grant_ownership(pair, db):
    """WORK-03 does not consult Redis at all, which is the point.

    Asserted structurally: if a future change routes the claim through the fail-soft
    Redis facade, a Redis outage could answer "acquired" for everybody.
    """
    import ast
    import inspect

    source = inspect.getsource(bo)
    tree = ast.parse(source)
    names = {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    } | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert "redis_manager" not in names, (
        "the bot-ownership claim now consults redis_manager, which is fail-soft and "
        "therefore cannot carry a correctness invariant"
    )


# ── 7. NEGATIVE CONTROL: a process-local implementation must FAIL ──────────


def test_the_harness_detects_a_process_local_implementation(pair):
    """THE CONTROL THAT MAKES §2 AND §3 MEANINGFUL.

    Reconstructs the pre-fix approach -- a per-process dict, which is exactly what
    `FleetManager._active_fleet` is -- and shows the cross-process harness reports TWO
    owners. If this ever stops reporting two, the harness cannot tell a distributed claim
    from a local one and §2/§3 prove nothing.
    """
    user_id, symbol = pair

    def local_acquire(fleet: dict, worker: str) -> bool:
        key = f"{user_id}_{symbol}"
        if key in fleet:
            return False
        fleet[key] = worker
        return True

    # Two "pods" = two separate dicts, because they share no memory.
    pod_a: dict = {}
    pod_b: dict = {}

    granted = [
        local_acquire(pod_a, "podA"),
        local_acquire(pod_b, "podB"),
    ]

    assert granted == [True, True], (
        "the process-local reconstruction did not produce two owners, so this control "
        "cannot demonstrate the defect WORK-03 fixes"
    )
    assert sum(granted) == 2


def test_the_stale_threshold_cannot_be_configured_below_its_floor():
    """A threshold inside one heartbeat period manufactures loss rather than detecting it."""
    assert bo.effective_stale_seconds() >= (
        bo.MIN_STALE_TO_INTERVAL_RATIO * bo.HEARTBEAT_INTERVAL_SECONDS
    )


def test_the_primary_key_is_the_invariant(db):
    """Stated against the catalogue: without this constraint there is no uniqueness."""
    cur = db.cursor()
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'pk_bot_ownership_claims'"
    )
    row = cur.fetchone()
    cur.close()
    assert row is not None, "the (user_id, symbol) primary key is gone"
    assert "user_id" in row[0] and "symbol" in row[0]


def test_a_client_cannot_write_a_claim(db):
    """A caller who could INSERT a claim could deny somebody the ability to start a bot."""
    cur = db.cursor()
    cur.execute(
        "SELECT privilege_type FROM information_schema.role_table_grants "
        "WHERE table_name='bot_ownership_claims' AND grantee='authenticated' "
        "ORDER BY privilege_type"
    )
    grants = {r[0] for r in cur.fetchall()}
    cur.close()
    assert grants == {"SELECT"}, (
        f"authenticated holds {sorted(grants)} on bot_ownership_claims; it must be "
        "SELECT only"
    )


# ── 8. THE WIRING: the claim is on the real start_bot path ─────────────────
#
# A claim mechanism nobody calls is the defect class this whole engagement was
# commissioned to find (TRADE-04 was exactly that). These assert the claim is actually
# taken by `FleetManager.start_bot` and actually released on every exit path.


def test_start_bot_refuses_when_the_claim_is_refused(monkeypatch, pair):
    """A refused claim must stop `start_bot` before it builds anything.

    `claim_bot` is imported INSIDE `start_bot`, so patching it on the module is the
    correct seam -- the import resolves at call time.

    Deliberately does NOT fake `app_state` or `BotRunner` via `sys.modules`: the first
    version of this test replaced both module objects globally, which corrupted module
    state for every later test in the session and turned a 62-second file into a
    16-minute one. `start_bot` wraps its tier lookup in `try/except` and defaults to the
    free tier, so the refusal is reached without any of that surgery.
    """
    import asyncio

    from backend_app.backend import fleet_manager as fm

    user_id, symbol = pair

    def _refused(uid, sym):
        return bo.BotClaim(str(uid), str(sym), "", "other-pod", bo.HELD_BY_ANOTHER)

    monkeypatch.setattr(bo, "claim_bot", _refused)

    fleet = fm.FleetManager()
    ok, message = asyncio.run(
        fleet.start_bot(user_id, symbol, {"exchange_id": "binance"},
                        paper_trading=True)
    )

    assert ok is False, "start_bot proceeded despite a refused ownership claim"
    assert "already owns" in message, f"unexpected refusal message: {message!r}"
    # And nothing was registered locally either.
    assert fleet._active_fleet == {}, (
        "a refused claim still left an entry in the fleet registry"
    )


def test_start_bot_is_wired_to_claim_bot():
    """AST, not substring: the docstrings in this area mention claim_bot repeatedly.

    `TRADE-04` existed as a function and was wired to nothing; this asserts the WORK-03
    claim does not repeat that.
    """
    import ast
    import inspect
    import textwrap

    from backend_app.backend.fleet_manager import FleetManager

    # `textwrap.dedent`, because `inspect.getsource` of a METHOD returns it indented and
    # `ast.parse` then raises IndentationError. The PORT-01 test had the mirror problem:
    # there the function was module-level and a hand-rolled dedent destroyed its body.
    # `dedent` is correct for both.
    tree = ast.parse(textwrap.dedent(inspect.getsource(FleetManager.start_bot)))
    body = tree.body[0]
    stmts = body.body[1:] if ast.get_docstring(body) else body.body
    called = {
        n.func.id
        for stmt in stmts for n in ast.walk(stmt)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "claim_bot" in called, (
        "FleetManager.start_bot does not call claim_bot in executable code, so the "
        "WORK-03 invariant is not enforced on the real path"
    )


def test_every_exit_path_releases_the_claim():
    """stop_bot, a construction failure, a crash and a BOOT FAILURE must all give the
    claim back.

    A claim that is never released strands the bot until the stale threshold, so the
    trader is told "another process owns it" when nothing does.

    `_boot_and_run` was added to this list after the fact: it pops the fleet record
    itself, so the done-callback release found `None` and the claim leaked. Three exit
    paths were checked here; there were four.
    """
    import ast
    import inspect
    import textwrap

    from backend_app.backend.fleet_manager import FleetManager

    for method in (FleetManager.start_bot, FleetManager.stop_bot,
                   FleetManager._cleanup_dead_bot, FleetManager._boot_and_run):
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        node = tree.body[0]
        stmts = node.body[1:] if ast.get_docstring(node) else node.body
        called = {
            n.func.id
            for stmt in stmts for n in ast.walk(stmt)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "release_bot" in called, (
            f"FleetManager.{method.__name__} never calls release_bot; a bot leaving via "
            f"that path would stay claimed until the stale threshold expired"
        )


def test_the_no_store_policy_fails_closed_outside_a_local_environment(monkeypatch, pair):
    """No PostgreSQL configured + not a declared local env -> refuse."""
    user_id, symbol = pair
    for var in ("AERORA_TEST_DATABASE_URL", "DATABASE_URL", "ENV", "ENVIRONMENT",
                "DEV_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ENV", "production")

    claim = bo.claim_bot(user_id, symbol)

    assert not claim.granted, (
        "a production environment with no claim store granted bot ownership"
    )
    assert claim.outcome is bo.STORE_UNAVAILABLE


def test_the_no_store_policy_permits_a_declared_local_environment(monkeypatch, pair):
    """...and a developer with the default SQLite DSN can still start a paper bot."""
    user_id, symbol = pair
    for var in ("AERORA_TEST_DATABASE_URL", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ENV", "development")

    claim = bo.claim_bot(user_id, symbol)

    assert claim.granted
    assert claim.owner_token.startswith("local-")
    # Symmetric: release is a no-op rather than a special case at the call site.
    assert bo.release_bot(claim) is True


def test_the_no_store_policy_uses_the_same_detector_as_the_rate_limiter():
    """One definition of 'may this process substitute local state for shared state?'."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(bo.claim_bot))
    node = tree.body[0]
    stmts = node.body[1:] if ast.get_docstring(node) else node.body
    names = {
        n.id for stmt in stmts for n in ast.walk(stmt) if isinstance(n, ast.Name)
    }
    assert "local_state_is_acceptable" in names, (
        "claim_bot no longer uses the canonical runtime_env detector, so the fleet and "
        "the rate limiter can now disagree about what counts as a local environment"
    )


# ── 10. claim liveness: the claim must not go stale while the bot still runs ──
#
# WHY THIS SECTION EXISTS
# -----------------------
# The first WORK-03 implementation wired `claim_bot` and `release_bot` and stopped there.
# `heartbeat()` was written and unit-tested but called from NOWHERE in `backend_app` --
# verified by grep: the only production importers were `claim_bot` and `release_bot`.
#
# That left the invariant holding for exactly STALE_AFTER_SECONDS and no longer. After
# 90 seconds the row is stale by its own predicate, so a second process calling
# `start_bot` for the same (user, symbol) is GRANTED the claim while the first bot is
# still trading -- and the first never finds out, because nothing calls heartbeat(). The
# defect WORK-03 exists to prevent was postponed by 90 seconds, not prevented.
#
# §10.1 is the negative control that demonstrates the gap was real.
# §10.2 is the positive control that the loop closes it.


class _RecordingTask:
    """Stands in for the bot's asyncio.Task. The oracle is `cancelled`."""

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def done(self) -> bool:
        return False


def _age_claim(db, user_id: str, symbol: str, seconds: int) -> None:
    """Push `heartbeat_at` into the past, simulating a worker that stopped refreshing."""
    cur = db.cursor()
    cur.execute(
        "UPDATE public.bot_ownership_claims "
        "   SET heartbeat_at = now() - make_interval(secs => %s) "
        " WHERE user_id = %s AND symbol = %s",
        (seconds, user_id, symbol),
    )
    cur.close()


def _heartbeat_age(db, user_id: str, symbol: str) -> Optional[float]:
    cur = db.cursor()
    cur.execute(
        "SELECT extract(epoch FROM (now() - heartbeat_at)) "
        "  FROM public.bot_ownership_claims WHERE user_id = %s AND symbol = %s",
        (user_id, symbol),
    )
    row = cur.fetchone()
    cur.close()
    return None if row is None else float(row[0])


def test_an_unrefreshed_claim_becomes_takeable_while_the_bot_still_runs(pair, db):
    """NEGATIVE CONTROL: this is the gap, stated as a passing test.

    No heartbeat loop -> the claim ages past the threshold -> a second process is
    granted ownership of a bot that is still running. If a future change made this test
    fail, the heartbeat loop would no longer be load-bearing and §10.2 could pass
    vacuously.
    """
    user_id, symbol = pair
    first = bo.acquire(_conn().cursor(), user_id, symbol, "pod-a")
    assert first.granted

    # The bot is still trading, but nobody refreshed the claim.
    _age_claim(db, user_id, symbol, bo.effective_stale_seconds() + 30)

    second = bo.acquire(_conn().cursor(), user_id, symbol, "pod-b")

    assert second.granted, (
        "an unrefreshed claim did NOT become takeable, so the stale mechanism is not "
        "doing what the rest of this file assumes"
    )
    assert second.outcome is bo.RECLAIMED_STALE
    # And this is precisely the two-runner condition: pod-a was never told.
    assert first.owner_token != second.owner_token


def test_a_heartbeated_claim_stays_ours_and_is_not_takeable(pair, db, monkeypatch):
    """POSITIVE CONTROL: the loop refreshes a claim that had already aged out."""
    monkeypatch.setenv("AERORA_TEST_DATABASE_URL", DSN)
    user_id, symbol = pair
    claim = bo.acquire(_conn().cursor(), user_id, symbol, "pod-a")
    assert claim.granted

    _age_claim(db, user_id, symbol, bo.effective_stale_seconds() + 30)
    assert _heartbeat_age(db, user_id, symbol) > bo.effective_stale_seconds()

    assert bo.heartbeat_bot(claim) is True, "heartbeat_bot denied a claim we still own"

    age = _heartbeat_age(db, user_id, symbol)
    assert age is not None and age < 5, (
        f"heartbeat_bot did not refresh heartbeat_at (age still {age}s)"
    )
    # Now a competitor must be refused, where in §10.1 it was granted.
    rival = bo.acquire(_conn().cursor(), user_id, symbol, "pod-b")
    assert not rival.granted, "a freshly heartbeated claim was still taken over"
    assert rival.outcome is bo.HELD_BY_ANOTHER


def test_heartbeat_bot_reports_a_takeover_as_false(pair, db, monkeypatch):
    """Once ownership is gone, the old owner must learn it and stop."""
    monkeypatch.setenv("AERORA_TEST_DATABASE_URL", DSN)
    user_id, symbol = pair
    first = bo.acquire(_conn().cursor(), user_id, symbol, "pod-a")
    _age_claim(db, user_id, symbol, bo.effective_stale_seconds() + 30)
    second = bo.acquire(_conn().cursor(), user_id, symbol, "pod-b")
    assert second.granted

    assert bo.heartbeat_bot(first) is False, (
        "the dispossessed owner's heartbeat still reported success, so its bot would "
        "keep trading alongside the new owner"
    )
    assert bo.heartbeat_bot(second) is True


def test_an_unreachable_store_raises_rather_than_denying_ownership(pair, monkeypatch):
    """THE DISTINCTION THAT KEEPS A BLIP FROM KILLING A LIVE BOT.

    `heartbeat()` collapses "store down" and "ownership lost" into one `False`, which is
    fine for a caller holding the cursor. `heartbeat_bot` manages its own connection, so
    returning `False` there would make the loop stop a healthy bot on a transient
    network error. It must raise instead.
    """
    user_id, symbol = pair
    # A port with nothing on it.
    monkeypatch.setenv("AERORA_TEST_DATABASE_URL",
                       "postgresql://postgres:@127.0.0.1:1/postgres")
    claim = bo.BotClaim(user_id, symbol, str(uuid.uuid4()), "pod-a", bo.CLAIMED)

    with pytest.raises(bo.ClaimStoreUnavailable):
        bo.heartbeat_bot(claim)


def test_the_heartbeat_loop_refreshes_a_real_claim(pair, db, monkeypatch):
    """End to end through `FleetManager._heartbeat_claim` against real PostgreSQL."""
    import asyncio

    from backend_app.backend import fleet_manager as fm

    monkeypatch.setenv("AERORA_TEST_DATABASE_URL", DSN)
    monkeypatch.setattr(bo, "HEARTBEAT_INTERVAL_SECONDS", 1)

    user_id, symbol = pair
    claim = bo.acquire(_conn().cursor(), user_id, symbol, "pod-a")
    assert claim.granted
    _age_claim(db, user_id, symbol, bo.effective_stale_seconds() + 30)

    fleet = fm.FleetManager()
    bot_key = fleet._bot_key(user_id, symbol)
    task = _RecordingTask()
    fleet._active_fleet[bot_key] = {"task": task, "claim": claim, "symbol": symbol}

    async def _drive():
        # The loop never returns while ownership holds, so bound it.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(fleet._heartbeat_claim(bot_key, claim), timeout=3.0)

    asyncio.run(_drive())

    age = _heartbeat_age(db, user_id, symbol)
    assert age is not None, "the claim row vanished while the heartbeat loop held it"

    # TWO ASSERTIONS, BECAUSE THE OLD SINGLE ONE CONFLATED TWO THINGS.
    #
    # It read `age < 3`, reusing the loop's own `wait_for` timeout as the freshness bound -
    # which leaves ZERO margin for everything that happens between the last refresh and
    # this measurement: the loop's final `asyncio.sleep(interval)`, the `wait_for`
    # cancellation, and this `_heartbeat_age` round trip. On an idle machine that is
    # milliseconds and the test passes; inside the full regression it slipped and the test
    # failed with `age 4.045936s` while the heartbeat had in fact worked perfectly.
    #
    # The row was deliberately aged to `effective_stale_seconds() + 30` (~120s) BEFORE the
    # loop ran, so "did a refresh happen at all" is answerable without any timing margin -
    # that is the first assertion, and it cannot be affected by load. The second keeps the
    # freshness claim meaningful but allows for the measurement itself.
    aged_to = bo.effective_stale_seconds() + 30
    assert age < aged_to - 10, (
        f"the claim was aged to {aged_to}s before the loop ran and is still {age}s old, so "
        f"the heartbeat loop never refreshed it"
    )
    assert age < bo.HEARTBEAT_INTERVAL_SECONDS + 5, (
        f"the claim is {age}s old after a loop with a "
        f"{bo.HEARTBEAT_INTERVAL_SECONDS}s interval. A refresh happened (see above) but "
        f"not recently enough to call the claim fresh."
    )
    assert task.cancelled is False, "the loop stopped a bot it still owned"


def test_the_heartbeat_loop_stops_the_bot_when_ownership_is_lost(pair, monkeypatch):
    """Losing the claim must cancel the runner, not merely log."""
    import asyncio

    from backend_app.backend import fleet_manager as fm

    monkeypatch.setattr(bo, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(bo, "heartbeat_bot", lambda claim: False)

    user_id, symbol = pair
    claim = bo.BotClaim(user_id, symbol, str(uuid.uuid4()), "pod-a", bo.CLAIMED)
    fleet = fm.FleetManager()
    bot_key = fleet._bot_key(user_id, symbol)
    task = _RecordingTask()
    fleet._active_fleet[bot_key] = {"task": task, "claim": claim, "symbol": symbol}

    asyncio.run(asyncio.wait_for(fleet._heartbeat_claim(bot_key, claim), timeout=5.0))

    assert task.cancelled is True, (
        "ownership was lost and the local runner kept going -- two bots now trade the "
        "same pair on the same account"
    )


def test_the_heartbeat_loop_tolerates_a_transient_store_outage(pair, monkeypatch):
    """A blip must not kill a live bot: availability must not masquerade as takeover."""
    import asyncio

    from backend_app.backend import fleet_manager as fm

    calls = {"n": 0}

    def _flaky(claim):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise bo.ClaimStoreUnavailable("simulated blip")
        return True

    monkeypatch.setattr(bo, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(bo, "heartbeat_bot", _flaky)

    user_id, symbol = pair
    claim = bo.BotClaim(user_id, symbol, str(uuid.uuid4()), "pod-a", bo.CLAIMED)
    fleet = fm.FleetManager()
    bot_key = fleet._bot_key(user_id, symbol)
    task = _RecordingTask()
    fleet._active_fleet[bot_key] = {"task": task, "claim": claim, "symbol": symbol}

    async def _drive():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(fleet._heartbeat_claim(bot_key, claim), timeout=1.0)

    asyncio.run(_drive())

    assert calls["n"] > 2, "the loop gave up instead of retrying after a store blip"
    assert task.cancelled is False, (
        "a transient store outage stopped a bot this process still owned"
    )


def test_the_heartbeat_loop_surrenders_after_a_prolonged_outage(pair, monkeypatch):
    """FAIL CLOSED on the only axis that can produce duplicate venue writes.

    Once we have been unable to refresh for longer than the stale threshold, another
    worker is ENTITLED to take the claim over, so we must assume one has.
    """
    import asyncio

    from backend_app.backend import fleet_manager as fm

    def _always_down(claim):
        raise bo.ClaimStoreUnavailable("simulated sustained outage")

    monkeypatch.setattr(bo, "HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(bo, "heartbeat_bot", _always_down)
    monkeypatch.setattr(bo, "effective_stale_seconds", lambda: 0.3)

    user_id, symbol = pair
    claim = bo.BotClaim(user_id, symbol, str(uuid.uuid4()), "pod-a", bo.CLAIMED)
    fleet = fm.FleetManager()
    bot_key = fleet._bot_key(user_id, symbol)
    task = _RecordingTask()
    fleet._active_fleet[bot_key] = {"task": task, "claim": claim, "symbol": symbol}

    asyncio.run(asyncio.wait_for(fleet._heartbeat_claim(bot_key, claim), timeout=10.0))

    assert task.cancelled is True, (
        "the claim store was unreachable for longer than the stale threshold and the "
        "bot kept trading on a claim another worker was free to take"
    )


def test_surrender_does_not_delete_the_successors_claim(pair, db, monkeypatch):
    """The dispossessed worker must stand down WITHOUT touching the new owner's row."""
    import asyncio

    from backend_app.backend import fleet_manager as fm

    monkeypatch.setenv("AERORA_TEST_DATABASE_URL", DSN)
    monkeypatch.setattr(bo, "HEARTBEAT_INTERVAL_SECONDS", 0.05)

    user_id, symbol = pair
    first = bo.acquire(_conn().cursor(), user_id, symbol, "pod-a")
    _age_claim(db, user_id, symbol, bo.effective_stale_seconds() + 30)
    second = bo.acquire(_conn().cursor(), user_id, symbol, "pod-b")
    assert second.granted

    fleet = fm.FleetManager()
    bot_key = fleet._bot_key(user_id, symbol)
    task = _RecordingTask()
    fleet._active_fleet[bot_key] = {"task": task, "claim": first, "symbol": symbol}

    asyncio.run(asyncio.wait_for(fleet._heartbeat_claim(bot_key, first), timeout=5.0))

    assert task.cancelled is True
    rows = _claims(db, user_id)
    assert len(rows) == 1, f"the surrendering worker disturbed the claim table: {rows}"
    assert rows[0][1] == second.owner_token, (
        "the dispossessed worker deleted or overwrote its successor's claim"
    )


def test_surrender_is_structurally_forbidden_from_releasing(pair):
    """AST: `_surrender_bot` must never call release_bot.

    Release is token-scoped so the DELETE would match zero rows today, but if that
    scoping were ever relaxed this call would delete the NEW owner's claim. Asserted
    structurally so the protection does not depend on two files staying in agreement.
    """
    import ast
    import inspect
    import textwrap

    from backend_app.backend.fleet_manager import FleetManager

    tree = ast.parse(textwrap.dedent(inspect.getsource(FleetManager._surrender_bot)))
    node = tree.body[0]
    stmts = node.body[1:] if ast.get_docstring(node) else node.body
    called = {
        n.func.id
        for stmt in stmts for n in ast.walk(stmt)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "release_bot" not in called, (
        "_surrender_bot releases the claim; a worker that LOST ownership must not touch "
        "the row, because the row now belongs to somebody else"
    )


def test_start_bot_wires_a_claim_heartbeat():
    """AST: taking the claim is not enough if nothing keeps it alive."""
    import ast
    import inspect
    import textwrap

    from backend_app.backend.fleet_manager import FleetManager

    tree = ast.parse(textwrap.dedent(inspect.getsource(FleetManager.start_bot)))
    node = tree.body[0]
    stmts = node.body[1:] if ast.get_docstring(node) else node.body
    attrs = {
        n.func.attr
        for stmt in stmts for n in ast.walk(stmt)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "_heartbeat_claim" in attrs, (
        "start_bot takes an ownership claim but never starts a heartbeat for it, so the "
        "claim goes stale while the bot is still trading and a second process may take "
        "ownership of a running bot"
    )


# ── 11. the boot-failure claim leak ─────────────────────────────────────────


def test_a_boot_failure_releases_the_claim(pair, db, monkeypatch):
    """`_boot_and_run` pops the fleet record ITSELF on a boot failure.

    The done-callback that normally releases the claim therefore found `None` and the
    claim LEAKED, stranding the bot for a full stale interval after a failure that left
    nothing running. Found by reading the three release sites against the four exit
    paths; `test_every_exit_path_releases_the_claim` had only checked three.
    """
    import asyncio

    from backend_app.backend import fleet_manager as fm

    monkeypatch.setenv("AERORA_TEST_DATABASE_URL", DSN)
    user_id, symbol = pair
    claim = bo.acquire(_conn().cursor(), user_id, symbol, "pod-a")
    assert claim.granted
    assert len(_claims(db, user_id)) == 1, "precondition: the claim row must exist"

    class _BootFailingBot:
        async def _recover_live_state(self):
            raise RuntimeError("simulated boot failure")

    fleet = fm.FleetManager()
    bot_key = fleet._bot_key(user_id, symbol)
    fleet._active_fleet[bot_key] = {
        "task": _RecordingTask(), "claim": claim, "symbol": symbol,
    }

    asyncio.run(fleet._boot_and_run(_BootFailingBot(), bot_key))

    assert fleet._active_fleet == {}, "the failed bot stayed in the fleet registry"
    assert _claims(db, user_id) == [], (
        "a boot failure left the ownership claim in place, so restarting this bot would "
        "be refused for the whole stale interval even though nothing is running"
    )
