"""
tests/test_exchange_connections_read_regression.py — ``GET /api/exchanges/`` against the LIVE
``exchange_keys`` shape.

Spec reference: ``.kiro/specs/production-launch-hardening/bugfix.md`` -> "Bug condition"
("the system is asked for a fact it does not have, and answers with a fabrication instead of an
absence").

WHAT THIS FILE IS FOR
---------------------
``list_exchanges`` projected ``"exchange_id, updated_at"``. ``public.exchange_keys`` in
production carries EXACTLY::

    user_id, exchange_id, encrypted_api_key, encrypted_secret_key, encrypted_password, created_at

— no ``updated_at`` and no ``id``. So PostgreSQL answered every call with ``42703``::

    column exchange_keys.updated_at does not exist
    (hint: Perhaps you meant to reference the column "exchange_keys.created_at".)

observed four times in a twelve-hour CloudWatch window. ``migrations/003_create_exchange_keys_table.sql``
declares BOTH an ``id TEXT PRIMARY KEY`` and an ``updated_at`` with a
``trg_exchange_keys_updated_at`` trigger, so production's table was never created by that
migration: the repo's SQL does not describe it, and this file's column contract follows the LIVE
shape rather than the migration.

Three compounding defects, one handler:

1. The projection named a column that does not exist -> ``42703`` on every call.
2. ``except Exception: keys = []`` swallowed it, so the response was **HTTP 200 with an empty
   array** — a trader holding stored credentials was told "no connected exchanges", with no
   error anywhere on screen. That is the bug condition verbatim: a fabrication (an absence the
   account does not have) standing in for a failed read.
3. ``"last_sync": row.get("updated_at")`` presented a ROW TIMESTAMP as a SYNCHRONISATION TIME.
   Nothing in this system records when a venue was last synchronised, so the field asserted a
   fact that does not exist. Repointing it at ``created_at`` would have kept the fabrication and
   only removed the crash, so the field is gone from the response — and from
   ``src/api/modules/exchange.js``'s typedef, the only place that declared it for this read.
   ``connected_at`` keeps ``created_at``, which genuinely IS when the credential was stored.

THE FOUR CLAIMS
---------------
``TestProjectionNamesOnlyLiveColumns``  the read names no column absent from the production
                                        table, statically and through the app
``TestStoredKeysAreListed``             an account WITH credentials gets them listed, with
                                        ``connected_at`` carrying the persisted ``created_at``
``TestFailedReadRefuses``               a read that did not complete answers 503
                                        ``EXCHANGE_CONNECTIONS_READ_FAILED`` — never 200, never
                                        an empty array, and never the driver's internals
``TestNoFabricatedSyncTime``            ``last_sync`` is absent from the response and from the
                                        handler's source, and no field is fed from ``updated_at``

Every assertion states the POST-fix behaviour, so each one fails against the handler as it
shipped. That failure is the finding, and it is what makes this file a revert-detector.

WHY THE 42703 IS EARNED AND NOT STAGED
--------------------------------------
:class:`_FakeSupabase` is told which columns ``exchange_keys`` actually has and raises
:class:`_UndefinedColumn` (SQLSTATE ``42703``, shaped like a ``postgrest`` ``APIError``) for a
projection naming one that does not. The failure observed here is therefore the REAL defect — a
handler asking for ``updated_at`` — not a failure the double invented. A stub that raised
unconditionally would pass just as well against a correct projection and would detect no revert.
The technique and the reasoning are ``tests/test_creator_analytics_regression.py``'s; this file
restates them for a different table so it stands alone.

WHAT IS DELIBERATELY NOT ASSERTED HERE
--------------------------------------
* That ``status``, ``health``, ``account_type``, ``permissions`` and ``subscription_tier`` are
  constants on this response. They are, and they are declared ``VERDICT.UNAVAILABLE`` in
  ``algo22-terminal/src/design/pageFields.js`` so no page renders them as readings. That is a
  separate defect with a separate owner, and nothing here depends on it.
* The tenant predicate on the read — ``tests/test_task_12_5_tenant_boundary_predicate.py`` and
  ``tests/security/test_builder_tenant_isolation.py`` own that claim.
* ``backend_app/backend/dashboard_aggregation_service.py``'s
  ``_EXCHANGE_KEY_BASE_COLUMNS = "exchange_id, updated_at"``, which is the SAME 42703 on a
  different read and is out of this file's scope. It is recorded here because a reader of this
  file will want to know it is still there.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient

from backend_app.core.dependencies import get_current_user, get_request_supabase, get_vault
from backend_app.main import app

# ══════════════════════════════════════════════════════════════════════════
# Identities, paths and the production column contract
# ══════════════════════════════════════════════════════════════════════════

EXCHANGES_PATH = "/api/exchanges/"

OWNER_ID = "usr_exchange_read_owner"
OWNER: Dict[str, Any] = {
    "id": OWNER_ID,
    "email": "trader@test.vyomquant.io",
    "access_token": "token_exchange_read_owner",
    "role": "authenticated",
}

#: ``public.exchange_keys``, column for column, as the LIVE database holds it. Queried directly
#: against production. ``updated_at`` and ``id`` are ABSENT ON PURPOSE: they are what
#: ``migrations/003_create_exchange_keys_table.sql`` declares and what production does not have,
#: and their absence here is what turns the pre-fix projection into a real 42703.
EXCHANGE_KEYS_COLUMNS: FrozenSet[str] = frozenset(
    {
        "user_id",
        "exchange_id",
        "encrypted_api_key",
        "encrypted_secret_key",
        "encrypted_password",
        "created_at",
    }
)

#: ``public.strategies``, restricted to the columns this handler's aggregation reads.
STRATEGIES_COLUMNS: FrozenSet[str] = frozenset({"id", "user_id", "exchange_id", "status"})

TABLE_COLUMNS: Dict[str, FrozenSet[str]] = {
    "exchange_keys": EXCHANGE_KEYS_COLUMNS,
    "strategies": STRATEGIES_COLUMNS,
}

#: Columns the migration declares and production does not have. Referencing either is the defect.
COLUMNS_ABSENT_IN_PRODUCTION: Tuple[str, ...] = ("updated_at", "id")

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
EXCHANGE_ROUTER_SOURCE: Path = REPO_ROOT / "backend_app" / "routers" / "exchange.py"

CONNECTED_AT = "2025-03-04T11:22:33+00:00"

client = TestClient(app, raise_server_exceptions=False)


# ══════════════════════════════════════════════════════════════════════════
# The Persistence_Layer double: column-aware, so a 42703 is EARNED
# ══════════════════════════════════════════════════════════════════════════


class _UndefinedColumn(Exception):
    """PostgreSQL ``42703``, shaped like the ``postgrest`` error production actually logged.

    The CloudWatch line carried ``code``, ``hint`` and ``message``; all three are reproduced, so
    a handler that echoed any of them into its body is caught by
    :meth:`TestFailedReadRefuses.test_the_refusal_echoes_no_driver_internal`.
    """

    def __init__(self, table: str, column: str) -> None:
        self.code = "42703"
        self.hint = f'Perhaps you meant to reference the column "{table}.created_at".'
        self.message = f"column {table}.{column} does not exist"
        super().__init__({"code": self.code, "hint": self.hint, "message": self.message})


class _Response:
    """What ``supabase-py`` hands back: rows on ``.data``, no ``error``."""

    def __init__(self, data: Any) -> None:
        self.data = data
        self.error = None


class _Query:
    """One table query. Records the projection and the predicates, filters on ``.execute()``."""

    def __init__(self, fake: "_FakeSupabase", table: str) -> None:
        self._fake = fake
        self._table = table
        self._columns: Optional[List[str]] = None
        self._filters: List[Tuple[str, Any]] = []

    def select(self, columns: str = "*", *_a: Any, **_k: Any) -> "_Query":
        self._columns = [part.strip() for part in str(columns).split(",") if part.strip()]
        self._fake.selects.append((self._table, tuple(self._columns)))
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        self._filters.append((column, value))
        return self

    def order(self, *_a: Any, **_k: Any) -> "_Query":
        return self

    def limit(self, *_a: Any, **_k: Any) -> "_Query":
        return self

    def execute(self) -> _Response:
        known = TABLE_COLUMNS[self._table]
        for column in self._columns or []:
            if column == "*":
                continue
            if column not in known:
                # The live database's answer to a projection naming a column it does not have.
                raise _UndefinedColumn(self._table, column)

        rows = [dict(row) for row in self._fake.rows.get(self._table, [])]
        for column, value in self._filters:
            rows = [row for row in rows if row.get(column) == value]
        return _Response(
            [{name: row.get(name) for name in (self._columns or list(row))} for row in rows]
        )


class _FakeSupabase:
    """A request-scoped client over in-memory rows, with a real column contract.

    Only the verbs this handler uses are implemented. A verb it starts using that is missing here
    fails loudly with ``AttributeError`` rather than silently returning a builder that ignores it.
    """

    def __init__(self, **rows: List[Dict[str, Any]]) -> None:
        self.rows: Dict[str, List[Dict[str, Any]]] = {
            name: list(value) for name, value in rows.items()
        }
        self.selects: List[Tuple[str, Tuple[str, ...]]] = []
        self.tables_read: List[str] = []

    def table(self, name: str) -> _Query:
        self.tables_read.append(name)
        if name not in TABLE_COLUMNS:
            raise AssertionError(
                f"the connections read touched {name!r}, a table this double declares no column "
                f"contract for; add it to TABLE_COLUMNS or stop reading it"
            )
        return _Query(self, name)


class _FailingQuery:
    """Builds without complaint; fails at the terminal ``.execute()``, where a read really fails."""

    def __getattr__(self, _name: str) -> Any:
        def _verb(*_a: Any, **_k: Any) -> "_FailingQuery":
            return self

        return _verb

    def execute(self) -> Any:
        raise ConnectionError(
            "could not connect to server: Connection refused\n\tis the server running on host "
            '"db.internal.vyomquant.io" and accepting TCP/IP connections on port 5432?'
        )


class _FailingSupabase:
    """A client whose every read did not complete. A fresh failure per call, not a shared one."""

    def __init__(self) -> None:
        self.tables_read: List[str] = []

    def table(self, name: str) -> _FailingQuery:
        self.tables_read.append(name)
        return _FailingQuery()


class _UnreadableSupabase:
    """A client that answers, but with nothing readable on ``.data``.

    A driver that returns ``None`` rows has not told the caller the account is empty; it has told
    the caller nothing. Distinct from :class:`_FailingSupabase` because the pre-fix handler's
    ``isinstance(q1.data, list) else []`` turned exactly this into a confident empty array
    WITHOUT ever raising, so no ``except`` was involved and no warning was logged.
    """

    def __init__(self) -> None:
        self.tables_read: List[str] = []

    def table(self, name: str) -> "_UnreadableQuery":
        self.tables_read.append(name)
        return _UnreadableQuery()


class _UnreadableQuery:
    def __getattr__(self, _name: str) -> Any:
        def _verb(*_a: Any, **_k: Any) -> "_UnreadableQuery":
            return self

        return _verb

    def execute(self) -> _Response:
        return _Response(None)


# ══════════════════════════════════════════════════════════════════════════
# Row builders — only columns production actually has
# ══════════════════════════════════════════════════════════════════════════


def _exchange_key_row(
    exchange_id: str,
    *,
    user_id: str = OWNER_ID,
    created_at: Optional[str] = CONNECTED_AT,
) -> Dict[str, Any]:
    """One ``exchange_keys`` row in the LIVE shape. No ``id``, no ``updated_at``."""
    return {
        "user_id": user_id,
        "exchange_id": exchange_id,
        "encrypted_api_key": f"gAAAAA-enc-key-{exchange_id}",
        "encrypted_secret_key": f"gAAAAA-enc-secret-{exchange_id}",
        "encrypted_password": None,
        "created_at": created_at,
    }


def _strategy_row(
    strategy_id: str, *, exchange_id: str, status: str, user_id: str = OWNER_ID
) -> Dict[str, Any]:
    return {
        "id": strategy_id,
        "user_id": user_id,
        "exchange_id": exchange_id,
        "status": status,
    }


# ══════════════════════════════════════════════════════════════════════════
# Harness
# ══════════════════════════════════════════════════════════════════════════


class _Tier:
    """The vault surface ``list_exchanges`` uses, and nothing else."""

    def __init__(self, tier: str = "pro") -> None:
        self._tier = tier

    def get_user_tier(self, _user_id: str) -> Dict[str, Any]:
        return {"subscription_tier": self._tier, "max_api_slots": 10}


class _Answer:
    def __init__(self, status_code: int, text: str, body: Any, tables_read: Tuple[str, ...]):
        self.status_code = status_code
        self.text = text
        self.body = body
        self.tables_read = tables_read


def _list_exchanges(db: Any, *, caller: Optional[Dict[str, Any]] = None) -> _Answer:
    """Drive ``GET /api/exchanges/`` against ``db`` as an authenticated trader.

    The client is injected through ``app.dependency_overrides[get_request_supabase]`` rather than
    by patching the module attribute: ``Depends(get_request_supabase)`` captured the callable at
    import time, so a ``patch`` on the router module is resolved by nothing and the handler would
    reach for the REAL database.
    """
    app.dependency_overrides[get_current_user] = lambda: dict(caller or OWNER)
    app.dependency_overrides[get_request_supabase] = lambda: db
    app.dependency_overrides[get_vault] = lambda: _Tier()
    try:
        response = client.get(
            EXCHANGES_PATH, headers={"Authorization": "Bearer token_exchange_read_owner"}
        )
    finally:
        app.dependency_overrides.clear()

    try:
        body = response.json()
    except ValueError:  # pragma: no cover - a non-JSON body is itself a finding
        body = None
    return _Answer(
        response.status_code, response.text, body, tuple(getattr(db, "tables_read", ()))
    )


# ══════════════════════════════════════════════════════════════════════════
# The handler's own source, for the structural claims
# ══════════════════════════════════════════════════════════════════════════


def _handler_code(name: str = "list_exchanges") -> str:
    """One handler's executable body as source text, its docstring removed.

    From disk rather than through ``inspect``, so no decorator can hide the body. The docstring
    is stripped because these assertions are about what the handler REFERENCES, and the prose
    explaining why ``updated_at`` was removed names ``updated_at`` — an assertion firing on the
    explanation of its own fix is a false positive, not a stricter test.
    """
    tree = ast.parse(EXCHANGE_ROUTER_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = list(node.body)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            assert body, f"{name} has no body other than its docstring"
            return "\n".join(ast.unparse(statement) for statement in body)
    raise AssertionError(f"{name} is not defined in {EXCHANGE_ROUTER_SOURCE}")


def _declared_projection() -> Tuple[str, ...]:
    """The columns ``_EXCHANGE_KEYS_PROJECTION`` names, parsed from the router's source.

    From the source rather than by importing the constant, so the assertion is about what the
    file says and cannot be satisfied by a value assembled at import time.
    """
    tree = ast.parse(EXCHANGE_ROUTER_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "_EXCHANGE_KEYS_PROJECTION":
                assert isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ), "_EXCHANGE_KEYS_PROJECTION is no longer a plain string literal"
                return tuple(
                    part.strip() for part in node.value.value.split(",") if part.strip()
                )
    raise AssertionError(
        f"_EXCHANGE_KEYS_PROJECTION is not defined in {EXCHANGE_ROUTER_SOURCE}; the projection "
        f"must stay a named constant so it can be asserted against the live column contract"
    )


def _keys_anywhere(body: Any) -> Set[str]:
    keys: Set[str] = set()
    if isinstance(body, dict):
        for name, value in body.items():
            keys.add(str(name))
            keys |= _keys_anywhere(value)
    elif isinstance(body, list):
        for value in body:
            keys |= _keys_anywhere(value)
    return keys


def _projection_for(db: _FakeSupabase, table: str) -> Tuple[str, ...]:
    """The columns the handler asked ``table`` for. Empty when it never selected from it."""
    for name, columns in db.selects:
        if name == table:
            return columns
    return ()


# ══════════════════════════════════════════════════════════════════════════
# 1. The projection names only columns the production table has
# ══════════════════════════════════════════════════════════════════════════


class TestProjectionNamesOnlyLiveColumns:
    """The root cause: a projection naming ``exchange_keys.updated_at``, which does not exist."""

    def test_the_projection_names_no_column_absent_from_production(self) -> None:
        db = _FakeSupabase(exchange_keys=[_exchange_key_row("binance")], strategies=[])
        answer = _list_exchanges(db)

        assert answer.status_code == 200, (
            f"the connections read named a column that does not exist on public.exchange_keys "
            f"(PostgreSQL 42703 - the exact failure CloudWatch logged four times in 12h). "
            f"Production has only {sorted(EXCHANGE_KEYS_COLUMNS)}. "
            f"Status {answer.status_code}, body {answer.text[:400]!r}"
        )

        projection = _projection_for(db, "exchange_keys")
        assert projection, "the handler never selected from exchange_keys at all"
        unknown = [column for column in projection if column not in EXCHANGE_KEYS_COLUMNS]
        assert not unknown, (
            f"the projection asks exchange_keys for {unknown}, which production does not have. "
            f"migrations/003 declares them; the live table was not created by that migration."
        )

    @pytest.mark.parametrize("column", COLUMNS_ABSENT_IN_PRODUCTION)
    def test_the_declared_projection_names_no_absent_column(self, column: str) -> None:
        """Statically, off the projection constant, so a revert is caught without running a read.

        Scoped to the projection rather than to the whole handler because ``"id"`` has legitimate
        uses in this body — the response's own synthesised ``id``, and ``strategies.id``, which
        that table really has. A blanket ban on the token would fire on both and say nothing
        about ``exchange_keys``. What a column reference to ``exchange_keys`` looks like is the
        projection, and this is it.
        """
        declared = _declared_projection()
        assert column not in declared, (
            f"the exchange_keys projection asks for {column!r}, which production does not have: "
            f"{declared}. migrations/003 declares it; the live table was not created by that "
            f"migration. The available timestamp is created_at."
        )

    def test_the_handler_source_references_no_updated_at_at_all(self) -> None:
        """``updated_at`` has no legitimate use anywhere in this handler, projection or not: the
        response fed TWO fields from it, and both were wrong for different reasons."""
        assert "updated_at" not in _handler_code(), (
            "list_exchanges still references updated_at, which exists on no column of "
            "public.exchange_keys"
        )

    def test_created_at_is_the_column_asked_for(self) -> None:
        db = _FakeSupabase(exchange_keys=[_exchange_key_row("binance")], strategies=[])
        _list_exchanges(db)
        assert "created_at" in _projection_for(db, "exchange_keys"), (
            "created_at is the only timestamp exchange_keys carries, and connected_at is "
            "reported from it, so the read has to ask for it"
        )


# ══════════════════════════════════════════════════════════════════════════
# 2. An account WITH credentials gets them listed
# ══════════════════════════════════════════════════════════════════════════


class TestStoredKeysAreListed:
    """The user-visible half: the 42703 rendered a credential-holding account as empty."""

    def test_a_user_with_stored_keys_gets_them_listed(self) -> None:
        db = _FakeSupabase(
            exchange_keys=[_exchange_key_row("binance"), _exchange_key_row("kraken")],
            strategies=[],
        )
        answer = _list_exchanges(db)

        assert answer.status_code == 200, f"{answer.status_code}: {answer.text[:400]!r}"
        assert isinstance(answer.body, list)
        venues = sorted(row["exchange_id"] for row in answer.body)
        assert venues == ["binance", "kraken"], (
            f"an account with two stored credentials was reported as holding {venues}. The "
            f"pre-fix handler answered [] here - 'no connected exchanges' for a trader who has "
            f"them - because the 42703 was swallowed into an empty list."
        )

    def test_connected_at_carries_the_persisted_created_at(self) -> None:
        db = _FakeSupabase(exchange_keys=[_exchange_key_row("binance")], strategies=[])
        answer = _list_exchanges(db)

        (row,) = answer.body
        assert row["connected_at"] == CONNECTED_AT, (
            f"connected_at is when the credential row was written, which is when the connection "
            f"was established. Got {row['connected_at']!r}, expected the persisted "
            f"{CONNECTED_AT!r}"
        )

    def test_a_row_with_no_timestamp_reports_none_rather_than_a_word(self) -> None:
        """``pageFields``' ``connectedAt`` entry renders the marker for ``null``. A substituted
        word - ``ExchangeManager.jsx``'s old ``"Recently"`` - is a claim about when a credential
        was stored, and the server may not manufacture one either."""
        db = _FakeSupabase(
            exchange_keys=[_exchange_key_row("binance", created_at=None)], strategies=[]
        )
        answer = _list_exchanges(db)

        (row,) = answer.body
        assert row["connected_at"] is None, (
            f"a row carrying no timestamp reported {row['connected_at']!r}. Null is the honest "
            f"answer; anything else is invented."
        )

    def test_an_account_with_no_stored_keys_reports_an_empty_list(self) -> None:
        """PRESERVATION. A read that COMPLETED and found nothing is a genuine zero and still
        answers 200 with ``[]`` — it must not be conflated with the refusal below."""
        db = _FakeSupabase(exchange_keys=[], strategies=[])
        answer = _list_exchanges(db)

        assert answer.status_code == 200, f"{answer.status_code}: {answer.text[:400]!r}"
        assert answer.body == [], f"expected a genuine empty list, got {answer.body!r}"

    def test_another_users_credentials_are_not_listed(self) -> None:
        """PRESERVATION of the tenant predicate across the projection change."""
        db = _FakeSupabase(
            exchange_keys=[
                _exchange_key_row("binance"),
                _exchange_key_row("kraken", user_id="usr_someone_else"),
            ],
            strategies=[],
        )
        answer = _list_exchanges(db)

        venues = sorted(row["exchange_id"] for row in answer.body)
        assert venues == ["binance"], f"another tenant's venue reached the response: {venues}"

    def test_the_bot_and_strategy_counts_still_aggregate(self) -> None:
        """PRESERVATION. The two figures on this response that are not constants."""
        db = _FakeSupabase(
            exchange_keys=[_exchange_key_row("binance")],
            strategies=[
                _strategy_row("s1", exchange_id="binance", status="running"),
                _strategy_row("s2", exchange_id="binance", status="draft"),
            ],
        )
        answer = _list_exchanges(db)

        (row,) = answer.body
        assert row["strategy_count"] == 2, row
        assert row["bot_count"] == 1, row


# ══════════════════════════════════════════════════════════════════════════
# 3. A read that did not complete refuses instead of reporting an absence
# ══════════════════════════════════════════════════════════════════════════


class TestFailedReadRefuses:
    """``bugfix.md § Bug condition``: a failed read may not answer with a fabricated absence.

    ``[]`` on this endpoint is a STATEMENT ABOUT THE ACCOUNT — "you have no exchange credentials
    stored". A read that did not complete establishes nothing of the kind. The repo's own rule,
    from ``routers/paper_trading.py`` (``PAPER_READ_FAILED``) and
    ``backend/marketplace/entitlement_resolver.py`` (``MARKETPLACE_READ_FAILED``): re-raise a
    defined outcome with a machine-readable code, never substitute an empty result.
    """

    def test_a_failed_read_is_not_answered_200(self) -> None:
        answer = _list_exchanges(_FailingSupabase())
        assert answer.status_code in (500, 503), (
            f"a read that did not complete was answered {answer.status_code}. The pre-fix "
            f"handler answered 200 with [], which is how a real production 42703 stayed "
            f"invisible for at least 12 hours. Body: {answer.text[:400]!r}"
        )

    def test_a_failed_read_is_never_an_empty_list(self) -> None:
        answer = _list_exchanges(_FailingSupabase())
        assert answer.body != [], (
            "a failed read answered with an empty array, which tells a trader holding "
            "credentials that they hold none"
        )

    def test_a_failed_read_carries_the_stable_read_failed_code(self) -> None:
        answer = _list_exchanges(_FailingSupabase())
        assert "EXCHANGE_CONNECTIONS_READ_FAILED" in answer.text, (
            f"the refusal carries no stable machine-readable code, so a client cannot tell an "
            f"outage from an empty account. Body: {answer.text[:400]!r}"
        )

    def test_a_failed_read_really_did_attempt_the_read(self) -> None:
        """A guard on the guard: a handler refusing BEFORE touching the Persistence_Layer would
        satisfy every assertion above while proving nothing about a failed read."""
        db = _FailingSupabase()
        _list_exchanges(db)
        assert "exchange_keys" in db.tables_read, (
            f"the endpoint refused without attempting the connections read at all; "
            f"tables touched: {db.tables_read}"
        )

    def test_the_42703_itself_is_not_answered_200(self) -> None:
        """The production failure end to end: the column-aware double raises the real 42703 for
        a projection naming ``updated_at``, and the handler must not turn it into an absence.

        Distinct from ``TestProjectionNamesOnlyLiveColumns``: that class asserts the projection
        is right, this one asserts that a projection being WRONG is reported rather than hidden.
        The two together are what make the swallow non-reintroducible.
        """
        db = _FakeSupabase(exchange_keys=[_exchange_key_row("binance")], strategies=[])

        with pytest.raises(_UndefinedColumn) as raised:
            db.table("exchange_keys").select("exchange_id, updated_at").eq(
                "user_id", OWNER_ID
            ).execute()
        assert raised.value.code == "42703"

        answer = _list_exchanges(_FailingSupabase())
        assert answer.status_code in (500, 503) and answer.body != []

    def test_an_unreadable_response_is_not_answered_200(self) -> None:
        """A driver that ANSWERS with nothing readable on ``.data`` never raised, so no
        ``except`` saw it: the pre-fix ``isinstance(q1.data, list) else []`` turned it into a
        confident empty array silently, without even a log line."""
        answer = _list_exchanges(_UnreadableSupabase())
        assert answer.status_code in (500, 503), (
            f"a response with no readable rows was answered {answer.status_code} "
            f"{answer.text[:300]!r}. Nothing was read, so nothing about the account is known."
        )
        assert answer.body != []

    def test_the_refusal_echoes_no_driver_internal(self) -> None:
        """The sqlstate, the column name, the table name and the internal host all reach the
        handler. None may come back out - the production log line carried all four."""
        answer = _list_exchanges(_FailingSupabase())
        lowered = answer.text.lower()
        for internal in ("42703", "updated_at", "db.internal.vyomquant.io", "5432", "postgres"):
            assert internal not in lowered, (
                f"the refusal body leaks the driver internal {internal!r}: "
                f"{answer.text[:400]!r}"
            )


# ══════════════════════════════════════════════════════════════════════════
# 4. No synchronisation time is manufactured
# ══════════════════════════════════════════════════════════════════════════


class TestNoFabricatedSyncTime:
    """``last_sync`` claimed a synchronisation happened at an instant. None ever did.

    A row timestamp is not a sync time, so neither ``updated_at`` nor ``created_at`` could fill
    the field honestly. Nothing consumes it off this read — ``design/pageFields.js`` declares no
    entry for it, and the ``last_sync`` ``pages/Dashboard.jsx`` renders comes from
    ``dashboard_aggregation_service.get_exchange_health``, a different response — so the field is
    removed rather than nulled, and ``src/api/modules/exchange.js`` no longer declares it.
    """

    def test_no_row_carries_a_last_sync_field(self) -> None:
        db = _FakeSupabase(
            exchange_keys=[_exchange_key_row("binance"), _exchange_key_row("kraken")],
            strategies=[],
        )
        answer = _list_exchanges(db)

        assert "last_sync" not in _keys_anywhere(answer.body), (
            "last_sync is back on this response. A row-creation time is not a synchronisation "
            "time, and nothing in this system records when a venue was last synchronised, so "
            "any value here asserts a fact that does not exist."
        )

    def test_the_handler_source_mentions_no_last_sync(self) -> None:
        assert "last_sync" not in _handler_code(), (
            "list_exchanges assembles a last_sync again. Removing the crash without removing "
            "the fabrication - repointing it at created_at - is the symptom patch bugfix.md's "
            "severity scale refuses."
        )

    def test_no_reported_field_is_fed_from_a_nonexistent_column(self) -> None:
        """Every timestamp in the response traces to a column production actually has."""
        db = _FakeSupabase(exchange_keys=[_exchange_key_row("binance")], strategies=[])
        answer = _list_exchanges(db)

        (row,) = answer.body
        timestamps = {
            name: value
            for name, value in row.items()
            if isinstance(value, str) and value == CONNECTED_AT
        }
        assert set(timestamps) == {"connected_at"}, (
            f"the persisted created_at reaches {sorted(timestamps)}. It answers connected_at "
            f"and nothing else: any second field carrying it is that column restated as a "
            f"different fact."
        )

    def test_the_client_typedef_declares_no_last_sync_for_this_read(self) -> None:
        """The wire contract has two sides. ``src/api/modules/exchange.js``'s
        ``ExchangeConnection`` typedef declared ``last_sync``, so a call site written against it
        would have reached for a field the server must never send."""
        module = (
            REPO_ROOT / "algo22-terminal" / "src" / "api" / "modules" / "exchange.js"
        ).read_text(encoding="utf-8")
        start = module.index("@typedef {Object} ExchangeConnection")
        typedef = module[start : module.index("*/", start)]
        assert "@property" in typedef, "the ExchangeConnection typedef was not located"
        properties = [
            line for line in typedef.splitlines() if "@property" in line and "last_sync" in line
        ]
        assert not properties, (
            f"ExchangeConnection still declares last_sync as a property of this response: "
            f"{properties}"
        )
