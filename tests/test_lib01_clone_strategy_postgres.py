"""LIB-01: ``POST /api/library/{id}/clone`` against real PostgreSQL.

THE DEFECT
==========
``clone_strategy`` answered **500 "Failed to look up library entry."** whenever the
service-role client was unavailable. The chain:

* ``_get_service_client()`` returns ``None`` when the credentials are absent and the process
  is permitted local fallbacks;
* ``_build_service_client()`` - whose docstring said *"raises 500 if env is not
  configured"* - caught only ``RuntimeError``, so the ``None`` passed through untouched;
* the handler then called ``svc.table("library_strategies")`` on ``None``, raising
  ``AttributeError``, which its broad ``except Exception`` relabelled as a 500 naming the
  row lookup.

So a **configuration** failure presented as a statement about the caller's data, and the
real cause (``'NoneType' object has no attribute 'table'``) appeared only in a log line.
``tests/test_validation_sweep.py::test_a_route_with_no_schema_to_violate_still_never_answers_500[library.clone_strategy]``
was the red test.

THE FIX, AND WHY IT ANSWERS 503
-------------------------------
``_require_service_client()`` raises ``MarketplaceError(MARKETPLACE_READ_FAILED)`` - **503**
- which is not a new code: ``get_categories`` in the same module already answers exactly
that for exactly this condition. The catalogue has no "server not configured" code, and
503 is right on its merits: the condition is an unreachable dependency, it is retryable,
and it says nothing false about the caller's data. The four broad ``except Exception``
handlers in the clone path were also narrowed to the concrete PostgREST error, so a genuine
read failure is answered as a read failure instead of being relabelled.

WHY THESE TESTS USE REAL POSTGRESQL, AND THE ONE THING THEY INJECT
------------------------------------------------------------------
Every assertion about what the clone **persists** runs against the live database through
``tests/paper_pg_client.PostgresSupabase``, so ``library_ratings_user_library_idx`` is a
real unique index, the real column defaults apply, and the real types reject a bad value.

**One input is injected: the entitlement verdict.** ``_entitlement_resolver.resolve`` reads
the caller's Subscription as a PostgREST *embedded resource*
(``library_subscriptions!library_subscriptions_library_id_fkey(...)`` off
``library_strategies``). The psycopg2 shim issues plain ``SELECT *`` and cannot express an
embed, and ``marketplace_subscriptions`` is not provisioned in this database at all
(measured: ``staging/probe_lib01_schema.py``). Rather than hand-roll an embed parser - a
second place for the projection to be wrong - the verdict is supplied directly and
everything downstream of it is real.

That injection weakens nothing these tests claim: every assertion below is about rows in
PostgreSQL *after* the verdict, and
:func:`test_5_an_unentitled_caller_writes_nothing` injects a NON-entitling verdict as the
negative control, so the suite can tell the two apart. The entitlement decision itself has
its own thorough coverage in ``tests/test_entitlement_matrix.py``.

WHAT IS DELIBERATELY NOT CLAIMED
--------------------------------
"Rollback" is not asserted, because there is no transaction to roll back: the handler
issues independent PostgREST statements. What is asserted is that a refusal **issues no
statement at all** against ``strategies``, ``library_ratings`` or the listing's
``clone_count`` - a different and more precise fact.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

psycopg2 = pytest.importorskip("psycopg2")
import psycopg2.extras  # noqa: E402

DSN = (
    os.environ.get("AERORA_TEST_DATABASE_URL")
    or "postgresql://postgres:@127.0.0.1:62674/postgres"
)
if not DSN.startswith("postgres"):  # pragma: no cover - selection guard
    pytest.skip("LIB-01 requires PostgreSQL", allow_module_level=True)

from tests.paper_pg_client import PostgresSupabase  # noqa: E402

from backend_app.backend.marketplace.entitlement_resolver import (  # noqa: E402
    Entitlement)
from backend_app.backend.marketplace.errors import (  # noqa: E402
    MARKETPLACE_CLONING_DISABLED, MARKETPLACE_NOT_SUBSCRIBED,
    MARKETPLACE_READ_FAILED, MarketplaceError)
from backend_app.routers import library as lib  # noqa: E402

#: Columns the clone path never copies onto the clone. ``strategies`` carries no moderation
#: column at all - those live on ``library_strategies`` - so this list is what a future edit
#: would have to ADD before it could leak one, which is why it is asserted by name rather
#: than relying on the table shape staying as it is.
FORBIDDEN_CLONE_COLUMNS = (
    "author_id",
    "moderation_status",
    "moderation_notes",
    "moderated_by",
    "moderated_at",
    "is_featured",
    "clone_count",
    "avg_rating",
    "rating_count",
    "verification_status",
    "evaluation_score",
    "is_verified_clone",
    "ml_model_path",
)


# ══════════════════════════════════════════════════════════════════════════
#  HARNESS
# ══════════════════════════════════════════════════════════════════════════


def _tables_present(dsn: str) -> bool:
    conn = psycopg2.connect(dsn)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                " WHERE table_schema = 'public' "
                "   AND table_name IN ('library_strategies', 'strategies', "
                "                      'library_ratings')"
            )
            return int(cur.fetchone()[0]) == 3
    finally:
        conn.close()


@pytest.fixture(scope="module", autouse=True)
def _require_schema():
    try:
        if not _tables_present(DSN):
            pytest.skip("library_strategies / strategies / library_ratings not provisioned")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable: {exc}")


class Oracle:
    """Reads and seeds over its OWN psycopg2 connection, never through the shim."""

    def __init__(self, dsn: str) -> None:
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True
        self.listing_ids: List[str] = []
        self.strategy_ids: List[str] = []
        self.user_ids: List[str] = []

    # ── teardown ──────────────────────────────────────────────────────────
    def close(self) -> None:
        """Delete in FK order: children, then listings, then the users they reference.

        ``strategies.user_id`` and ``profiles.id`` both reference ``auth.users(id)``, so the
        user rows go last. Measured FK set: ``staging/probe_auth_users_shape.py``.
        """
        with self._conn.cursor() as cur:
            for uid in self.user_ids:
                cur.execute("DELETE FROM library_ratings WHERE user_id = %s", (uid,))
                cur.execute("DELETE FROM strategies WHERE user_id = %s", (uid,))
            for lid in self.listing_ids:
                cur.execute("DELETE FROM library_ratings WHERE library_id = %s", (lid,))
                cur.execute("DELETE FROM strategies WHERE source_library_id = %s", (lid,))
                cur.execute("DELETE FROM library_strategies WHERE id = %s", (lid,))
            for sid in self.strategy_ids:
                cur.execute("DELETE FROM strategies WHERE id = %s", (sid,))
            for uid in self.user_ids:
                cur.execute("DELETE FROM profiles WHERE id = %s", (uid,))
                cur.execute("DELETE FROM auth.users WHERE id = %s", (uid,))
        self._conn.close()

    def _all(self, sql: str, params: tuple) -> List[Dict[str, Any]]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    # ── seeding ───────────────────────────────────────────────────────────
    def new_user(self) -> str:
        """A synthetic identity that really exists, because the FKs require it.

        ``strategies.user_id`` references ``auth.users(id)``, so a bare UUID is refused with
        ``strategies_user_id_fkey``. ``auth.users`` in this provisioned schema is a
        three-column stub whose only NOT NULL column with no default is ``id``, so the
        insert is the primary key and nothing else - measured in
        ``staging/probe_auth_users_shape.py`` rather than guessed at.
        """
        uid = str(uuid.uuid4())
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                (uid,),
            )
        self.user_ids.append(uid)
        return uid

    def seed_source_strategy(self, owner: str, **overrides: Any) -> str:
        sid = str(uuid.uuid4())
        self.strategy_ids.append(sid)
        row = {
            "id": sid,
            "user_id": owner,
            "name": "Source Strategy",
            "symbol": "BTCUSDT",
            "timeframe": "5m",
            "exchange_id": "binance",
            "buy_logic": psycopg2.extras.Json({"rules": ["rsi<30"]}),
            "sell_logic": psycopg2.extras.Json({"rules": ["rsi>70"]}),
            "risk": psycopg2.extras.Json({"stop_loss": 2}),
            "indicators": psycopg2.extras.Json([{"name": "rsi"}]),
            "ml_model_path": "models/owner/secret.pkl",
            "status": "stopped",
        }
        row.update(overrides)
        cols = ", ".join(f'"{k}"' for k in row)
        marks = ", ".join(["%s"] * len(row))
        with self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO strategies ({cols}) VALUES ({marks})",
                list(row.values()),
            )
        return sid

    def seed_listing(
        self,
        author: str,
        source_strategy_id: str,
        *,
        is_active: bool = True,
        moderation_status: str = "approved",
        source_cloning_enabled: bool = True,
        clone_count: int = 7,
        name: str = "Published Strategy",
        is_featured: bool = False,
    ) -> str:
        """A listing row that satisfies the live CHECK constraints.

        ``is_featured`` defaults to FALSE and is a parameter, because
        ``chk_ls_featured_requires_published`` is
        ``is_featured = false OR moderation_status = 'approved'`` - measured in
        ``staging/probe_lib01_constraints.py``. The first version of this helper set it TRUE
        unconditionally and the database refused every non-approved seed, which is the
        constraint doing its job.

        ``moderation_notes`` is seeded with text so :func:`test_12` is asserting against a
        listing that really carries a moderator-only field, rather than against a NULL.
        """
        lid = str(uuid.uuid4())
        self.listing_ids.append(lid)
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO library_strategies "
                " (id, author_id, source_strategy_id, name, category, difficulty, "
                "  symbol, timeframe, exchange_id, is_active, moderation_status, "
                "  clone_count, source_cloning_enabled, is_featured, moderation_notes) "
                " VALUES (%s,%s,%s,%s,'momentum','beginner','BTCUSDT','5m','binance',"
                "         %s,%s,%s,%s,%s,'moderator only')",
                (
                    lid, author, source_strategy_id, name,
                    is_active, moderation_status, clone_count, source_cloning_enabled,
                    is_featured,
                ),
            )
        return lid

    # ── reads ─────────────────────────────────────────────────────────────
    def listing(self, lid: str) -> Optional[Dict[str, Any]]:
        rows = self._all("SELECT * FROM library_strategies WHERE id = %s", (lid,))
        return rows[0] if rows else None

    def strategy(self, sid: str) -> Optional[Dict[str, Any]]:
        rows = self._all("SELECT * FROM strategies WHERE id = %s", (sid,))
        return rows[0] if rows else None

    def clones_of(self, lid: str) -> List[Dict[str, Any]]:
        return self._all(
            "SELECT * FROM strategies WHERE source_library_id = %s ORDER BY created_at",
            (lid,),
        )

    def ratings_for(self, lid: str) -> List[Dict[str, Any]]:
        return self._all("SELECT * FROM library_ratings WHERE library_id = %s", (lid,))


@pytest.fixture
def oracle():
    ora = Oracle(DSN)
    yield ora
    ora.close()


@pytest.fixture
def pg():
    client = PostgresSupabase(DSN)
    yield client
    client.conn.close()


@pytest.fixture
def entitled(monkeypatch):
    """Inject an ENTITLING verdict. The one thing this schema cannot supply - see module docs."""

    async def _resolve(caller, listing_id, supabase, now):
        return Entitlement(entitling=True, reason="SUBSCRIBED")

    monkeypatch.setattr(lib._entitlement_resolver, "resolve", _resolve)


#: The client the handler should see, PER THREAD.
#:
#: WHY A THREAD-LOCAL AND NOT A SAVE/RESTORE (found by the PostgreSQL lane of the Phase 7
#: full regression). ``run_clone`` used to do this::
#:
#:     original = module._require_service_client
#:     module._require_service_client = lambda: pg
#:     try: ...
#:     finally: module._require_service_client = original
#:
#: which is a read-modify-write on a MODULE GLOBAL, and
#: ``test_14_two_concurrent_clones_by_one_caller_are_bounded_and_owned`` calls it from two
#: threads at once. The interleaving that actually happened:
#:
#:     thread A  saves original = <the real function>, installs <lambda: clientA>
#:     thread B  saves original = <lambda: clientA>,   installs <lambda: clientB>
#:     thread A  restores <the real function>
#:     thread B  restores <lambda: clientA>            <-- the leak
#:
#: so the module was left permanently bound to a lambda returning clientA, whose connection
#: the worker then closed in its own ``finally``. Every later request to ``clone_strategy``
#: in that interpreter got a dead client, and the full run recorded
#: ``psycopg2.InterfaceError: connection already closed`` behind
#: ``500 INTERNAL_SERVER_ERROR`` on eleven tests in four other files - including the two
#: ``test_validation_sweep`` cells this phase had just turned green.
#:
#: It passed in isolation and in every targeted grouping, because the leak only matters to
#: a test that runs AFTERWARDS in the same process. That is the same class of defect as the
#: orphaned ``slowapi`` ``Limiter`` instances root-caused in Phase 6, and it is why
#: ``staging/REGRESSION_ORDER_ISOLATION.md`` treats "it passed in isolation" as not good
#: enough.
#:
#: A thread-local removes the race rather than guarding it: the global is bound ONCE, by the
#: module-scoped fixture below, to a function that reads this - so there is no
#: read-modify-write to interleave, and nothing to restore wrongly.
_handler_client = threading.local()


@pytest.fixture(autouse=True, scope="module")
def _bind_service_client_once():
    """Bind ``library._require_service_client`` to the thread-local, once, and restore it.

    Module-scoped and autouse so the binding exists for every test here and is reversed
    exactly once at the end, by a single thread. ``raising=False`` is not used: if the
    attribute is missing, that is a product change this file must notice.
    """
    import backend_app.routers.library as module

    original = module._require_service_client

    def _from_thread_local():
        """The thread's bound client, or the REAL function when none is bound.

        Falling through rather than raising is deliberate:
        ``test_8_an_unavailable_service_client_is_503_not_500`` exists to exercise the real
        ``_require_service_client`` with ``_build_service_client`` answering ``None``, and
        every other call in this file goes through ``run_clone``, which binds one.
        """
        client = getattr(_handler_client, "client", None)
        if client is None:
            return original()
        return client

    module._require_service_client = _from_thread_local
    try:
        yield
    finally:
        module._require_service_client = original


def run_clone(pg, *, listing_id: str, caller_id: str) -> Any:
    """Call the real handler coroutine with the real service client bound to ``pg``.

    Thread-safe: the client is published on a thread-local that the already-installed
    ``_require_service_client`` reads. See ``_handler_client`` for what the previous
    save/restore version cost.
    """
    import backend_app.routers.library as module

    previous = getattr(_handler_client, "client", None)
    _handler_client.client = pg
    try:
        return asyncio.run(
            module.clone_strategy.__wrapped__.__wrapped__(
                request=None,
                library_id=listing_id,
                user={"id": caller_id},
                _strategy_quota=None,
            )
        )
    finally:
        # Per-thread, so this cannot be clobbered by another thread's value.
        _handler_client.client = previous


# ══════════════════════════════════════════════════════════════════════════
#  1. THE VALID CLONE
# ══════════════════════════════════════════════════════════════════════════


def test_1_a_valid_clone_is_persisted(pg, oracle, entitled):
    author, caller = oracle.new_user(), oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(author, source)

    result = run_clone(pg, listing_id=listing, caller_id=caller)

    clones = oracle.clones_of(listing)
    assert len(clones) == 1, "exactly one clone row"
    clone = clones[0]
    assert str(clone["id"]) == str(result["new_strategy_id"])
    assert clone["name"] == "[Clone] Published Strategy"
    assert clone["status"] == "stopped"
    assert clone["buy_logic"] == {"rules": ["rsi<30"]}
    assert clone["sell_logic"] == {"rules": ["rsi>70"]}
    assert clone["risk"] == {"stop_loss": 2}
    assert clone["indicators"] == [{"name": "rsi"}]
    # clone_count moved 7 -> 8, and the verified-clone marker exists.
    assert oracle.listing(listing)["clone_count"] == 8
    markers = oracle.ratings_for(listing)
    assert len(markers) == 1 and markers[0]["is_verified_clone"] is True


# ══════════════════════════════════════════════════════════════════════════
#  2-6. THE REFUSALS
# ══════════════════════════════════════════════════════════════════════════


def test_2_a_source_strategy_that_no_longer_exists_is_404_and_writes_nothing(
    pg, oracle, entitled
):
    """The listing survives its source being deleted; the clone must not invent one."""
    author, caller = oracle.new_user(), oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(author, source)
    with oracle._conn.cursor() as cur:
        cur.execute("DELETE FROM strategies WHERE id = %s", (source,))

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        run_clone(pg, listing_id=listing, caller_id=caller)
    assert caught.value.status_code == 404

    assert oracle.clones_of(listing) == []
    assert oracle.listing(listing)["clone_count"] == 7, "clone_count untouched"
    assert oracle.ratings_for(listing) == []


def test_3_the_author_cloning_their_own_listing_is_409_and_writes_nothing(
    pg, oracle, entitled
):
    """"source belongs to another user" from the other side: the OWNER is refused 409.

    Deliberately ahead of the consent gate in the handler, so the owner's answer does not
    depend on their own ``source_cloning_enabled`` setting.
    """
    author = oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(author, source, source_cloning_enabled=False)

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        run_clone(pg, listing_id=listing, caller_id=author)
    assert caught.value.status_code == 409

    assert oracle.clones_of(listing) == []
    assert oracle.listing(listing)["clone_count"] == 7


@pytest.mark.parametrize(
    "is_active, moderation_status",
    [
        (False, "approved"),
        (True, "pending"),
        (True, "rejected"),
        (False, "pending"),
    ],
)
def test_4_an_unpublished_or_unapproved_source_is_404_and_writes_nothing(
    pg, oracle, entitled, is_active, moderation_status
):
    """Only ``is_active`` AND ``approved``/``featured`` may be cloned."""
    author, caller = oracle.new_user(), oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(
        author, source, is_active=is_active, moderation_status=moderation_status
    )

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        run_clone(pg, listing_id=listing, caller_id=caller)
    assert caught.value.status_code == 404

    assert oracle.clones_of(listing) == []
    assert oracle.listing(listing)["clone_count"] == 7


def test_5_an_unentitled_caller_writes_nothing(pg, oracle, monkeypatch):
    """THE NEGATIVE CONTROL for the injected verdict.

    Without this, every "a clone row exists" assertion above could be passing on a harness
    that is simply unable to refuse, and every "nothing was written" assertion could be
    passing on one unable to write.
    """

    async def _refuse(caller, listing_id, supabase, now):
        return Entitlement(entitling=False, reason="NOT_SUBSCRIBED")

    monkeypatch.setattr(lib._entitlement_resolver, "resolve", _refuse)

    author, caller = oracle.new_user(), oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(author, source)

    with pytest.raises(MarketplaceError) as caught:
        run_clone(pg, listing_id=listing, caller_id=caller)
    assert caught.value.code == MARKETPLACE_NOT_SUBSCRIBED
    assert caught.value.http_status == 403

    assert oracle.clones_of(listing) == []
    assert oracle.listing(listing)["clone_count"] == 7
    assert oracle.ratings_for(listing) == []


def test_6_a_malformed_listing_identifier_never_reaches_the_database(pg, oracle, entitled):
    """``_safe_uuid`` refuses before the first statement, so a bad id is a 4xx not a 500."""
    from fastapi import HTTPException

    before = len(pg.statements)
    with pytest.raises(HTTPException) as caught:
        run_clone(pg, listing_id="not-a-uuid", caller_id=oracle.new_user())
    assert 400 <= caught.value.status_code < 500
    assert len(pg.statements) == before, "a malformed id issued a statement"


# ══════════════════════════════════════════════════════════════════════════
#  7-9. IDEMPOTENCY, THE UNAVAILABLE CLIENT, AND WHAT A REFUSAL LEAVES
# ══════════════════════════════════════════════════════════════════════════


def test_7_cloning_twice_returns_the_first_clone_and_creates_no_second_row(
    pg, oracle, entitled
):
    """Step 5's idempotency, and the "duplicate destination" case.

    The second call must not create a second ``[Clone] ...`` row for the same caller and
    listing, and must not bump ``clone_count`` again.
    """
    author, caller = oracle.new_user(), oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(author, source)

    first = run_clone(pg, listing_id=listing, caller_id=caller)
    assert oracle.listing(listing)["clone_count"] == 8

    second = run_clone(pg, listing_id=listing, caller_id=caller)

    assert str(second["new_strategy_id"]) == str(first["new_strategy_id"])
    assert len(oracle.clones_of(listing)) == 1, "no second clone row"
    assert oracle.listing(listing)["clone_count"] == 8, "clone_count bumped twice"


def test_8_an_unavailable_service_client_is_503_not_500(oracle):
    """THE LIB-01 ASSERTION. The 500 that ``test_validation_sweep`` caught is now a 503.

    ``_build_service_client`` is made to answer ``None`` - exactly what it does when the
    credentials are absent and the process permits local fallbacks - and the handler is run
    for real. Before the fix this produced
    ``500 {"error":"INTERNAL_SERVER_ERROR","message":"Failed to look up library entry."}``
    from an ``AttributeError`` on ``None.table``.
    """
    import backend_app.routers.library as module

    original = module._build_service_client
    module._build_service_client = lambda: None
    try:
        with pytest.raises(MarketplaceError) as caught:
            asyncio.run(
                module.clone_strategy.__wrapped__.__wrapped__(
                    request=None,
                    library_id=str(uuid.uuid4()),
                    user={"id": oracle.new_user()},
                    _strategy_quota=None,
                )
            )
    finally:
        module._build_service_client = original

    assert caught.value.code == MARKETPLACE_READ_FAILED
    assert caught.value.http_status == 503, (
        "a missing service-role client must be a retryable dependency failure, not a 500 "
        "that blames the caller's data"
    )


def test_9_a_refusal_leaves_no_partial_write_behind(pg, oracle, entitled):
    """"Rollback", stated as what is actually true on this transport.

    There is no transaction here - the handler issues independent PostgREST statements - so
    what is asserted is that the consent refusal issues NO statement against ``strategies``,
    ``library_ratings`` or the listing, rather than issuing some and undoing them.
    """
    author, caller = oracle.new_user(), oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(author, source, source_cloning_enabled=False)

    pg.statements.clear()
    with pytest.raises(MarketplaceError) as caught:
        run_clone(pg, listing_id=listing, caller_id=caller)
    assert caught.value.code == MARKETPLACE_CLONING_DISABLED

    written = {table for table, op, _ in pg.statements if op != "select"}
    assert written == set(), f"the refused clone wrote to {sorted(written)}"
    assert oracle.clones_of(listing) == []
    assert oracle.listing(listing)["clone_count"] == 7
    assert oracle.ratings_for(listing) == []
    # And the owner's logic was never even fetched, so a refusal does not pull it in-process.
    assert "strategies" not in {table for table, _, _ in pg.statements}


# ══════════════════════════════════════════════════════════════════════════
#  10-13. OWNERSHIP, IMMUTABILITY AND PRIVILEGE
# ══════════════════════════════════════════════════════════════════════════


def test_10_the_clone_is_owned_by_the_caller(pg, oracle, entitled):
    author, caller = oracle.new_user(), oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(author, source)

    run_clone(pg, listing_id=listing, caller_id=caller)

    clone = oracle.clones_of(listing)[0]
    assert str(clone["user_id"]) == caller
    assert str(clone["user_id"]) != author
    assert str(clone["source_library_id"]) == listing


def test_11_the_source_strategy_and_its_author_are_unchanged(pg, oracle, entitled):
    """A clone is a copy. Nothing about the original may move."""
    author, caller = oracle.new_user(), oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(author, source)
    before = oracle.strategy(source)

    run_clone(pg, listing_id=listing, caller_id=caller)

    after = oracle.strategy(source)
    # `updated_at` is the only column a trigger could legitimately move; it is compared too,
    # because nothing should have UPDATEd this row at all.
    assert after == before, "the source strategy row changed during a clone"
    assert str(after["user_id"]) == author, "the source's ownership moved"


def test_12_the_clone_inherits_no_moderation_or_admin_field(pg, oracle, entitled):
    """"cannot inherit moderator/admin fields", asserted by name.

    ``strategies`` carries no moderation column today, so this currently holds by the shape
    of the table. It is asserted anyway: the list below is exactly what a future edit would
    have to add to the clone payload before a listing's moderation state could ride along,
    and this test is what would catch that.
    """
    author, caller = oracle.new_user(), oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(author, source, moderation_status="featured")

    run_clone(pg, listing_id=listing, caller_id=caller)

    clone = oracle.clones_of(listing)[0]
    leaked = [column for column in FORBIDDEN_CLONE_COLUMNS if clone.get(column)]
    assert leaked == [], (
        f"the clone carries {leaked}, which belongs to the listing or its author. A clone "
        "is a copy of the STRATEGY, not of its marketplace standing."
    )
    # The listing keeps its own moderation state; the clone did not move it.
    assert oracle.listing(listing)["moderation_status"] == "featured"


def test_13_the_authors_model_artifact_path_is_not_copied(pg, oracle, entitled):
    """``ml_model_path`` is a filesystem path that reaches ``joblib.load`` in the cloner's
    bot process - the process holding the cloner's decrypted exchange keys - and the AUTHOR
    controls the artifact's bytes. The handler excludes it deliberately; asserted here
    against the persisted row rather than against the payload dict.
    """
    author, caller = oracle.new_user(), oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(author, source)

    assert oracle.strategy(source)["ml_model_path"] == "models/owner/secret.pkl"

    run_clone(pg, listing_id=listing, caller_id=caller)

    clone = oracle.clones_of(listing)[0]
    assert not clone["ml_model_path"], (
        "the clone carries the author's model artifact path"
    )


# ══════════════════════════════════════════════════════════════════════════
#  14. CONCURRENCY
# ══════════════════════════════════════════════════════════════════════════


def test_14_two_concurrent_clones_by_one_caller_are_bounded_and_owned(oracle, monkeypatch):
    """Two simultaneous clone requests, over two independent PostgreSQL connections.

    WHAT IS GUARANTEED, AND WHAT IS NOT. Step 5's idempotency is a read-then-write and
    there is **no unique constraint on ``strategies (user_id, source_library_id)``** -
    measured: the only unique index on ``strategies`` is its primary key
    (``staging/probe_lib01_schema.py``). So two concurrent callers can both read "no
    existing clone" and both insert, and this test does not assert that they cannot. That
    is reported as **LIB-02**, and the honest fix is a migration adding the constraint.

    What this test does assert is the bound: **at most one clone per request**, every clone
    row is owned by the CALLER, none carries the author's artifact path, the listing's
    moderation state is untouched, and ``clone_count`` never exceeds the number of clones
    actually created. A duplicate is two rows the caller owns - not a cross-tenant write and
    not a corrupted listing.
    """

    async def _resolve(caller, listing_id, supabase, now):
        return Entitlement(entitling=True, reason="SUBSCRIBED")

    monkeypatch.setattr(lib._entitlement_resolver, "resolve", _resolve)

    author, caller = oracle.new_user(), oracle.new_user()
    source = oracle.seed_source_strategy(author)
    listing = oracle.seed_listing(author, source, clone_count=0)

    results: Dict[str, Any] = {}
    barrier = threading.Barrier(2)

    def worker(tag: str) -> None:
        client = PostgresSupabase(DSN)
        try:
            barrier.wait(timeout=30)
            try:
                results[tag] = ("ok", run_clone(client, listing_id=listing, caller_id=caller))
            except Exception as exc:  # noqa: BLE001
                results[tag] = ("error", type(exc).__name__, getattr(exc, "code", None))
        finally:
            client.conn.close()

    threads = [threading.Thread(target=worker, args=(tag,)) for tag in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert len(results) == 2, f"both workers must finish; got {results}"

    clones = oracle.clones_of(listing)
    assert 1 <= len(clones) <= 2, (
        f"two requests produced {len(clones)} clone rows; at most one per request"
    )
    for clone in clones:
        assert str(clone["user_id"]) == caller, "a clone was written for the wrong owner"
        assert not clone["ml_model_path"]
        assert str(clone["source_library_id"]) == listing

    listing_row = oracle.listing(listing)
    assert listing_row["clone_count"] <= len(clones), (
        "clone_count claims more clones than exist - the read-modify-write lost an update "
        "in the WRONG direction (over-counting), which no retry can correct"
    )
    assert listing_row["moderation_status"] == "approved", "the listing's state moved"
    assert str(listing_row["author_id"]) == author, "the listing's ownership moved"
