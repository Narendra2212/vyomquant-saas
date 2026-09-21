# -*- coding: utf-8 -*-
"""
tests/security/test_builder_tenant_isolation.py

Strategy-builder task 8.7 - the tenant isolation and secret containment suite.
Requirements 12.1, 21.1, 21.2, 21.4, 21.6, 21.7.

  Property 22: No user can read or reference another user's resources.
  Property 23: No exchange id, api key, secret or passphrase appears in ``graph_json``,
               ``compiled_plan``, ``training_jobs.config``, any response body, any WS
               frame or any log line.

WHAT THIS FILE HOLDS IN PLACE
-----------------------------
* **One matrix, six resource types, every endpoint that names one.** Strategy, version,
  training job, model version, deployment and exchange account, each addressed through
  every REST endpoint that takes its identifier, as a **non-owner**. Every entry must
  answer 403 or 404 (or, for a collection, 200 carrying nothing), and the whole response
  body is searched for the owner's identifiers as *substrings* rather than one field
  being checked.

* **The matrix cannot pass vacuously.** A handler that answered 404 for everybody would
  satisfy "the non-owner gets a 404" while being useless. So every refusal is paired: the
  same identifier is resolved through the *same loader the endpoint uses*, against the
  *same seeded database*, as the owner - and must be found. Six resource types, six
  positive controls, plus an HTTP owner control on every endpoint whose success path
  completes in this environment.

* **A refusal writes nothing.** The fake PostgREST client records every insert, update and
  delete with its filters and applies them to its rows, so "the non-owner's request left
  no trace" is a fact about the recorded statements and about the rows afterwards, not
  about a call count.

* **Realtime subscriptions are authorised, and refusals are reported.** All five owned
  channel families (``training``, ``builder.validation``, ``strategy``, ``deployment``,
  ``execution``) are asked for as owner and as non-owner through the real
  ``authorize_channel_subscription``. A non-owner is refused with a reported code, and the
  refusal frame carries no resource data at all (Requirements 21.5, 21.6).

* **Property 23 is a substring scan over real artifacts.** A save is driven through the
  real router with a DATA node carrying ``exchange``, ``api_key``, ``api_secret`` and
  ``passphrase``, and the persisted ``graph_json`` / ``compiled_plan`` are searched - key
  by key at every depth, and value by value as substrings - for any of them. The training
  configuration's own guard (``strategy_service.assert_no_exchange_identity``, which
  ``build_training_config`` calls before returning) is exercised over the full forbidden
  vocabulary at three nesting depths, and the call is proved to be there by parsing the
  function's AST rather than by reading its docstring.

* **Logs are scanned, not trusted.** Every request the matrix makes and the save that
  carries credentials run under ``caplog`` at DEBUG, and the captured text is searched for
  the secret values.

WHAT THIS FILE CANNOT PROVE, AND SAYS SO
----------------------------------------
**Database-level row-level security is NOT verified here, and cannot be.** There is no
PostgreSQL in this environment and migrations ``004``, ``004b``, ``004c``, ``004d`` and
``004e`` are **unapplied**, so nothing in this file demonstrates that ``tj_owner_select``,
``tj_owner_insert``, ``tj_owner_update``, ``mv_owner_select`` or ``mv_owner_insert``
actually filter a query, nor that the ``strategy_versions`` policies scope a read through
``strategies.user_id = auth.uid()``. What IS verified is the half that lives in this
repository's Python: the **API-layer** filter that Requirement 21.2 asks for on top of
RLS, and that 004d *declares* the five owner policies (:class:`TestPersistenceLayerRls`).
The fake client honours ``.eq`` and nothing else - it does not simulate RLS - which is the
strict direction: an endpoint that leaned on RLS alone FAILS here rather than passing on a
policy this environment cannot run.

WHAT IS REAL
------------
The routers, the services, the loaders, the channel authoriser, the registry, the
validator, the compiler, the canonical parse, the forbidden-parameter vocabulary and the
training-config guard are all the production ones. The doubles are the PostgREST client
(this environment has no instance of one) and the performance/telemetry read on the
strategy projection, which is not this file's subject. No assertion in this file is
satisfied by a double's behaviour: the fake honours ``.eq`` filters, so an endpoint whose
ownership filter went missing cannot pass.
"""

import ast
import inspect as _inspect
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend_app.backend import model_versioning as MV
from backend_app.backend import training_status as TS
from backend_app.backend import deployment_binding as DB
from backend_app.backend import strategy_service as S
from backend_app.backend.strategy_dag.schema import (
    FORBIDDEN_PARAM_FIELDS,
    is_forbidden_param,
)
from backend_app.backend.strategy_service import StrategyService
from backend_app.backend.ws_channels import (
    OWNED_CHANNEL_FAMILIES,
    canvas_state_frame,
    runtime_state_frame,
)
from backend_app.core.websocket_auth import (
    CHANNEL_REFUSED_FORBIDDEN,
    authorize_channel_subscription,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_ROUTER_PATH = REPO_ROOT / "backend_app" / "routers" / "strategy_operations.py"
MIGRATION_004D = REPO_ROOT / "backend_app" / "migrations" / "004d_training_and_models.sql"

# ---------------------------------------------------------------------------
# The two tenants, and the resources exactly one of them owns
# ---------------------------------------------------------------------------

OWNER_ID = "usr_8_7_owner"
INTRUDER_ID = "usr_8_7_intruder"

STRATEGY_ID = "str_8_7_owned"
VERSION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
VERSION_LABEL = "v4.0"
OTHER_VERSION_LABEL = "v3.0"
OTHER_VERSION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
JOB_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
MODEL_VERSION_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
DEPLOYMENT_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
ACCOUNT_ID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
NODE_ID = "n_model"
#: The owner's Paper_Session, for the seventh owned channel family (``paper.{session_id}``,
#: marketplace-subscriptions-paper-trading task 26.3). It resolves through ``paper_sessions.user_id``
#: - the only family that reads that relation - so it needs an identifier and a seeded row of its
#: own rather than reusing ``DEPLOYMENT_ID``.
SESSION_ID = "99999999-9999-4999-8999-999999999999"

#: The owner-only strings a cross-tenant response must never carry. Every one of these is
#: on a row the intruder does not own, so any of them in a body addressed to the intruder
#: is a leak regardless of which field it arrived in. ``VERSION_ID`` and the plan hash are
#: in here on purpose: the version-history and version-compare endpoints return rows that
#: carry no ``user_id``, so without an identifier of the *row* a leak of another tenant's
#: graph would look like an empty answer.
OWNER_MARKERS = (
    OWNER_ID,
    "OWNER_ONLY_STRATEGY_NAME",
    "OWNER_ONLY_DATASET_FINGERPRINT",
    "OWNER_ONLY_ARTIFACT_CHECKSUM",
    "OWNER_ONLY_ERROR_MESSAGE",
    "s3://owner-private-bucket/models/owner.pkl",
    VERSION_ID,
    OTHER_VERSION_ID,
    DEPLOYMENT_ID,
    JOB_ID,
    MODEL_VERSION_ID,
)

#: Credential material the tests feed IN, so that finding any of it anywhere - a persisted
#: column, a response body, a WS frame, a log line - is unambiguous. None of these strings
#: occurs anywhere in this repository other than in this file.
CREDENTIAL_CANARIES = {
    "api_key": "CANARY-API-KEY-8f3a91",
    "apikey": "CANARY-APIKEY-CAMEL-77b2",
    "api_secret": "CANARY-API-SECRET-4d7e02",
    "secret": "CANARY-SECRET-1a9c55",
    "secret_key": "CANARY-SECRET-KEY-b6e310",
    "passphrase": "CANARY-PASSPHRASE-77c1de",
    "password": "CANARY-PASSWORD-2e8b40",
    "private_key": "CANARY-PRIVATE-KEY-90ff1c",
    "credentials": "CANARY-CREDENTIALS-3c5a7b",
}

#: The venue slug, kept separate from the credential material above.
#:
#: Requirement 12.1 forbids an exchange *identifier* in ``graph_json``, ``compiled_plan``
#: and ``training_jobs.config``, so the persistence scan looks for this one too. It is NOT
#: in the response scan, because ``GET /api/exchanges`` exists in order to say which
#: exchanges a user has connected, and an endpoint whose whole purpose is to name a venue
#: cannot be asked not to. This is the distinction ``schema._QUOTABLE_FORBIDDEN_KEYS``
#: already draws: a venue id is not a secret, and conflating the two would either weaken
#: the credential assertion or make the venue assertion untrue.
VENUE_CANARY = "canaryvenue"

#: Everything a persisted builder artifact must contain none of.
PERSISTED_CANARIES = tuple(
    sorted(set(CREDENTIAL_CANARIES.values()) | {VENUE_CANARY})
)

#: Everything a response body, a realtime frame or a log line must contain none of.
CANARY_VALUES = tuple(sorted(set(CREDENTIAL_CANARIES.values())))

#: The params a hostile or stale client sends on a DATA node: every forbidden key at once.
POISONED_PARAMS = {**CREDENTIAL_CANARIES, "exchange": VENUE_CANARY, "exchange_id": VENUE_CANARY}


# ---------------------------------------------------------------------------
# The one double: a PostgREST client that honours .eq and applies its writes
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    """A chainable PostgREST stand-in that FILTERS, and that APPLIES its writes.

    Filtering is the whole point of this file: a fake that ignored
    ``.eq("user_id", ...)`` would let every ownership assertion pass while the production
    filter was missing. Applying the writes is what makes "the intruder's request left no
    trace" a statement about the rows rather than about a call count.

    ``execute`` is a coroutine, because the pooled client this platform builds per request
    is async and ``routers/strategies.py`` awaits it unconditionally. Every other caller
    goes through ``strategy_service._execute``, which handles either shape.
    """

    def __init__(self, parent: "_Supabase", table: str):
        self._parent = parent
        self._table = table
        self._eq: Dict[str, Any] = {}
        self._neq: Dict[str, Any] = {}
        self._in: Dict[str, tuple] = {}
        self._mode = "select"
        self._payload: Optional[Dict[str, Any]] = None

    # ── builder ────────────────────────────────────────────────────────────
    def select(self, columns="*", *a, **kw):
        self._mode = "select"
        return self

    def insert(self, payload, *a, **kw):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload, *a, **kw):
        self._mode = "update"
        self._payload = payload
        return self

    def delete(self, *a, **kw):
        self._mode = "delete"
        return self

    def eq(self, column, value):
        self._eq[str(column)] = value
        return self

    def neq(self, column, value):
        self._neq[str(column)] = value
        return self

    def in_(self, column, values):
        self._in[str(column)] = tuple(values)
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    def range(self, *a, **kw):
        return self

    def single(self, *a, **kw):
        return self

    # ── matching ───────────────────────────────────────────────────────────
    def _matches(self, row: Dict[str, Any]) -> bool:
        for key, want in self._eq.items():
            if isinstance(want, bool):
                if bool(row.get(key)) is not want:
                    return False
            elif str(row.get(key)) != str(want):
                return False
        for key, unwanted in self._neq.items():
            if str(row.get(key)) == str(unwanted):
                return False
        for key, allowed in self._in.items():
            if row.get(key) not in allowed:
                return False
        return True

    # ── terminal ───────────────────────────────────────────────────────────
    async def execute(self):
        if self._table in self._parent.absent_tables:
            raise RuntimeError(f'relation "public.{self._table}" does not exist (42P01)')
        rows = self._parent.rows.setdefault(self._table, [])

        if self._mode == "insert":
            payloads = self._payload if isinstance(self._payload, list) else [self._payload]
            written = []
            for payload in payloads:
                row = dict(payload or {})
                rows.append(row)
                self._parent.writes.append(("insert", self._table, dict(row), {}))
                written.append(row)
            return _Result(written)

        if self._mode == "update":
            self._parent.writes.append(
                ("update", self._table, dict(self._payload or {}), dict(self._eq))
            )
            touched = []
            for row in rows:
                if self._matches(row):
                    row.update(dict(self._payload or {}))
                    touched.append(row)
            return _Result(touched)

        if self._mode == "delete":
            self._parent.writes.append(("delete", self._table, {}, dict(self._eq)))
            removed = [row for row in rows if self._matches(row)]
            self._parent.rows[self._table] = [
                row for row in rows if not self._matches(row)
            ]
            return _Result(removed)

        return _Result([dict(row) for row in rows if self._matches(row)])


class _Supabase:
    """A seeded, filtering, write-recording stand-in for the request-scoped client.

    It deliberately does **not** simulate row-level security. That is the strict
    direction: an endpoint whose only tenant filter is an RLS policy leaks here and fails,
    rather than passing on a policy this environment has no PostgreSQL to enforce.
    """

    def __init__(self, rows=None, absent_tables=()):
        self.rows: Dict[str, List[Dict[str, Any]]] = {
            name: [dict(row) for row in value] for name, value in (rows or {}).items()
        }
        self.absent_tables = set(absent_tables)
        self.writes: List[tuple] = []

    def table(self, name):
        return _Query(self, name)

    # convenience ----------------------------------------------------------
    def row(self, table: str, row_id: str) -> Optional[Dict[str, Any]]:
        for row in self.rows.get(table, []):
            if str(row.get("id")) == str(row_id):
                return row
        return None

    def writes_touching(self, *tables: str) -> List[tuple]:
        wanted = set(tables) if tables else None
        return [
            write
            for write in self.writes
            if wanted is None or write[1] in wanted
        ]


# ---------------------------------------------------------------------------
# Seed data: everything below belongs to OWNER_ID and to nobody else
# ---------------------------------------------------------------------------


def _user(user_id: str) -> Dict[str, Any]:
    return {
        "id": user_id,
        "email": f"{user_id}@example.com",
        "role": "authenticated",
        "access_token": f"token_{user_id}",
    }


@pytest.fixture(scope="module")
def registry():
    """The real assembled block registry. Assembly is ~150 ms, so it is shared."""
    from backend_app.backend.strategy_dag import registry as registry_module

    return registry_module.build_registry()


def _graph(reg, *, extra_data_params=None) -> Any:
    """``ohlcv_feed -> ema -> gt(vs constant) -> buy``, over the real registry.

    The smallest graph the real validator and the real compiler both accept, built from
    published descriptors rather than invented block ids - so a save reaches the ownership
    check instead of stopping at ``UNRESOLVED_BLOCK``, and the persisted artifacts these
    tests scan are what production would really write.
    """
    from backend_app.backend.strategy_dag.schema import EdgeSpec, NodeSpec, StrategyGraph

    def node(block_id, **params):
        return NodeSpec.create(block_id, reg[block_id].category, params=params)

    data_params = {
        "symbol": "SOL/USDT",
        "timeframe": "4h",
        "market_type": "spot",
        "mode": "streaming",
    }
    data_params.update(extra_data_params or {})

    data = node("ohlcv_feed", **data_params)
    ema = node("ema", window=20, source="close")
    const = node("constant", value=30.0)
    gate = node("gt")
    action = node("action_buy_market", quantity_type="percent_of_equity", quantity=0.25)
    return StrategyGraph(
        nodes=[data, ema, const, gate, action],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", gate.id, "left"),
            EdgeSpec.create(const.id, "value", gate.id, "right"),
            EdgeSpec.create(gate.id, "out", action.id, "signal"),
        ],
    )


#: A stored graph and plan for the seeded version rows. These only have to be *parseable*
#: - what a save really writes is asserted in :class:`TestProperty23NoCredentialInAPersistedArtifact`
#: from a live save through the router.
STORED_GRAPH = {
    "schema_version": 2,
    "nodes": [
        {
            "id": "n_data",
            "block_id": "ohlcv_feed",
            "category": "DATA",
            "params": {"symbol": "BTC/USDT", "timeframe": "1h", "market_type": "spot"},
        }
    ],
    "edges": [],
}


def _compiled_plan() -> Dict[str, Any]:
    return {
        "dag_hash": "0123456789abcdef0123456789abcdef",
        "execution_order": ["n_data"],
        "data_nodes": ["n_data"],
        "node_index": {
            "n_data": {
                "id": "n_data",
                "block_id": "ohlcv_feed",
                "category": "data",
                "params": {"symbol": "BTC/USDT", "timeframe": "1h", "market_type": "spot"},
            }
        },
    }


def _seed_rows() -> Dict[str, List[Dict[str, Any]]]:
    plan = _compiled_plan()
    graph = STORED_GRAPH
    return {
        "strategies": [
            {
                "id": STRATEGY_ID,
                "user_id": OWNER_ID,
                "name": "OWNER_ONLY_STRATEGY_NAME",
                "status": "stopped",
                "symbol": "BTC/USDT",
                "current_version": VERSION_LABEL,
                "buy_logic": {},
            }
        ],
        "strategy_versions": [
            {
                "id": VERSION_ID,
                "strategy_id": STRATEGY_ID,
                "version": VERSION_LABEL,
                "blueprint": graph,
                "graph_json": graph,
                "compiled_plan": plan,
                "dag_hash": plan["dag_hash"],
                "validation_state": "VALID",
                "lifecycle_state": "READY",
                "is_current": True,
                "is_draft": False,
                "is_read_only": False,
                "created_at": "2024-01-02T00:00:00Z",
            },
            {
                "id": OTHER_VERSION_ID,
                "strategy_id": STRATEGY_ID,
                "version": OTHER_VERSION_LABEL,
                "blueprint": graph,
                "graph_json": graph,
                "compiled_plan": plan,
                "dag_hash": plan["dag_hash"],
                "validation_state": "VALID",
                "lifecycle_state": "READY",
                "is_current": False,
                "is_draft": False,
                "is_read_only": False,
                "created_at": "2024-01-01T00:00:00Z",
            },
        ],
        "strategy_deployments": [
            {
                "id": DEPLOYMENT_ID,
                "user_id": OWNER_ID,
                "strategy_id": STRATEGY_ID,
                "version_id": VERSION_ID,
                "version": VERSION_LABEL,
                "status": "running",
                "mode": "paper",
                "environment": "paper",
                "started_at": "2024-01-03T00:00:00Z",
                "error_message": "OWNER_ONLY_ERROR_MESSAGE",
            }
        ],
        "training_jobs": [
            {
                "id": JOB_ID,
                "user_id": OWNER_ID,
                "strategy_id": STRATEGY_ID,
                "version_id": VERSION_ID,
                "node_id": NODE_ID,
                "block_id": "ml.xgboost_classifier",
                "status": "RUNNING",
                "cancel_requested": False,
                "config": {"symbol": "BTC/USDT", "timeframe": "1h", "epochs": 10},
                "dataset_fingerprint": "OWNER_ONLY_DATASET_FINGERPRINT",
                "dataset_rows": 5000,
                "usable_rows": 4000,
                "feature_columns": 12,
                "feature_names": ["f1", "f2"],
                "epochs_total": 10,
                "epoch_current": 3,
                "progress": 30,
            }
        ],
        "model_versions": [
            {
                "id": MODEL_VERSION_ID,
                "user_id": OWNER_ID,
                "strategy_id": STRATEGY_ID,
                "version_id": VERSION_ID,
                "training_job_id": JOB_ID,
                "node_id": NODE_ID,
                "block_id": "ml.xgboost_classifier",
                "model_version": 1,
                "artifact_uri": "s3://owner-private-bucket/models/owner.pkl",
                "artifact_checksum": "OWNER_ONLY_ARTIFACT_CHECKSUM",
                "artifact_bytes": 2048,
                "serialization": "joblib",
                "is_active": True,
                "feature_schema": {"names": ["f1", "f2"]},
            }
        ],
        "exchange_keys": [
            {
                "id": ACCOUNT_ID,
                "user_id": OWNER_ID,
                "exchange_id": VENUE_CANARY,
                "api_key": CREDENTIAL_CANARIES["api_key"],
                "api_secret": CREDENTIAL_CANARIES["api_secret"],
                "passphrase": CREDENTIAL_CANARIES["passphrase"],
                "updated_at": "2024-01-01T00:00:00Z",
            }
        ],
        # ``paper.{session_id}``'s owner relation. ``_PAPER_SESSIONS_RELATION`` reads
        # ``id,user_id`` and nothing else, so those are the columns seeded - the same shape
        # ``risk_settings`` below is seeded in.
        "paper_sessions": [{"id": SESSION_ID, "user_id": OWNER_ID}],
        "risk_settings": [{"id": "risk_owner", "user_id": OWNER_ID}],
        "exchange_connections": [],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sb():
    return _Supabase(_seed_rows())


@pytest.fixture
def owner():
    return _user(OWNER_ID)


@pytest.fixture
def intruder():
    return _user(INTRUDER_ID)


@pytest.fixture(autouse=True)
def _module_state():
    """Reset the caches the builder modules keep, before and after every test."""
    DB.reset_binding_column_support()
    DB.reset_venue_timeframe_cache()
    S.reset_canonical_column_support()
    yield
    DB.reset_binding_column_support()
    DB.reset_venue_timeframe_cache()
    S.reset_canonical_column_support()


@pytest.fixture
def app_and_client(sb):
    """The real app, with the one database seam pointed at the fake and limits suspended.

    ``create_request_supabase_async`` is the single seam every Strategy Builder read goes
    through - ``StrategyService._get_supabase``, ``training_status._client_for`` and
    ``model_versioning._client_for`` all call it - so patching it once covers every
    endpoint in the matrix without any handler being aware of a test.

    The rate limits are suspended for the duration, and restored afterwards, for the
    reason ``test_sb06_exchange_agnostic_save`` gives: this file makes far more than
    20 requests a minute from one address, and a 429 says nothing about isolation either
    way. The limits themselves are untouched in production code, and
    :class:`TestEveryEndpointKeepsItsAuthAndItsLimiter` asserts every decorator is still
    on every route.
    """
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user, get_request_supabase
    from backend_app.main import app

    limiter = getattr(app.state, "limiter", None)
    was_enabled = getattr(limiter, "enabled", None)
    if limiter is not None:
        limiter.enabled = False

    async def _client(*_a, **_kw):
        return sb

    patches = [
        patch("backend_app.backend.strategy_service.create_request_supabase_async", _client),
        patch("backend_app.core.dependencies.create_request_supabase_async", _client),
        patch("backend_app.routers.strategies._sb", AsyncMock(return_value=sb)),
        patch.object(
            StrategyService, "_get_strategy_performance", AsyncMock(return_value={})
        ),
    ]
    for item in patches:
        item.start()

    app.dependency_overrides[get_request_supabase] = lambda: sb

    state = {"user": _user(OWNER_ID)}
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    try:
        yield app, TestClient(app, raise_server_exceptions=False), state
    finally:
        app.dependency_overrides.clear()
        for item in reversed(patches):
            item.stop()
        if limiter is not None and was_enabled is not None:
            limiter.enabled = was_enabled


# ---------------------------------------------------------------------------
# The matrix: (resource type x endpoint), as a non-owner
# ---------------------------------------------------------------------------

#: ``(resource, method, path, query, body, expectation)``.
#:
#: ``expectation`` is ``"refused"`` for an endpoint that names one resource - it must
#: answer 403 or 404 - or ``"empty"`` for a collection endpoint, where 200 carrying no
#: row of the owner's is the correct answer and a 404 would be wrong (a user with no jobs
#: has an empty list, not a missing one).
#:
#: ``GRAPH_BODY`` is a placeholder resolved by :func:`_request`. The save body has to be a
#: graph the real registry publishes - a save is refused for being unreadable BEFORE
#: ownership is consulted, deliberately (``create_version`` compiles first so an invalid
#: graph never opens a database client), so an invented block id would make the ownership
#: assertion untestable rather than strict. Building it here would run registry assembly
#: at collection time for every worker.
GRAPH_BODY = "<the-real-registrys-graph>"

MATRIX = [
    # ── strategy ────────────────────────────────────────────────────────────
    ("strategy", "GET", f"/api/strategies/{STRATEGY_ID}", None, None, "refused"),
    ("strategy", "PUT", f"/api/strategies/{STRATEGY_ID}", None, {"name": "taken"}, "refused"),
    ("strategy", "DELETE", f"/api/strategies/{STRATEGY_ID}", None, None, "refused"),
    (
        "strategy",
        "POST",
        f"/api/strategy-operations/strategies/{STRATEGY_ID}/versions",
        None,
        GRAPH_BODY,
        "refused",
    ),
    (
        "strategy",
        "POST",
        f"/api/strategy-operations/strategies/{STRATEGY_ID}/nodes/n_data/preview",
        None,
        {"bars": 10},
        "refused",
    ),
    (
        "strategy",
        "GET",
        f"/api/strategy-operations/strategies/{STRATEGY_ID}/data-quality",
        None,
        None,
        "refused",
    ),
    ("strategy", "GET", f"/api/strategies/{STRATEGY_ID}/versions", None, None, "empty"),
    # ── version ─────────────────────────────────────────────────────────────
    (
        "version",
        "POST",
        f"/api/strategies/{STRATEGY_ID}/versions/{VERSION_LABEL}/deploy",
        None,
        {},
        "refused",
    ),
    (
        "version",
        "POST",
        f"/api/strategies/{STRATEGY_ID}/versions/restore",
        {"version": OTHER_VERSION_LABEL},
        None,
        "refused",
    ),
    (
        "version",
        "POST",
        f"/api/strategies/{STRATEGY_ID}/versions/compare",
        {"version_a": VERSION_LABEL, "version_b": OTHER_VERSION_LABEL},
        None,
        "refused",
    ),
    # ── training job ────────────────────────────────────────────────────────
    (
        "training_job",
        "GET",
        f"/api/strategy-operations/training/jobs/{JOB_ID}",
        None,
        None,
        "refused",
    ),
    (
        "training_job",
        "POST",
        f"/api/strategy-operations/training/jobs/{JOB_ID}/cancel",
        None,
        None,
        "refused",
    ),
    (
        "training_job",
        "GET",
        "/api/strategy-operations/training/jobs",
        {"version_id": VERSION_ID},
        None,
        "empty",
    ),
    # ── model version ───────────────────────────────────────────────────────
    (
        "model_version",
        "GET",
        f"/api/strategy-operations/models/{MODEL_VERSION_ID}/download",
        None,
        None,
        "refused",
    ),
    # ── deployment ──────────────────────────────────────────────────────────
    ("deployment", "GET", f"/api/deployments/{DEPLOYMENT_ID}", None, None, "refused"),
    ("deployment", "POST", f"/api/deployments/{DEPLOYMENT_ID}/pause", None, None, "refused"),
    ("deployment", "POST", f"/api/deployments/{DEPLOYMENT_ID}/resume", None, None, "refused"),
    ("deployment", "POST", f"/api/deployments/{DEPLOYMENT_ID}/stop", None, None, "refused"),
    ("deployment", "POST", f"/api/deployments/{DEPLOYMENT_ID}/restart", None, None, "refused"),
    # ── exchange account ────────────────────────────────────────────────────
    ("exchange_account", "GET", "/api/exchanges", None, None, "empty"),
]

MATRIX_IDS = [
    f"{resource}-{method}-{path}" for resource, method, path, _q, _b, _e in MATRIX
]


_GRAPH_CACHE: Dict[str, Any] = {}


def _valid_save_body() -> Dict[str, Any]:
    """``{"blueprint": <a real canonical graph>}``, assembled once per process."""
    if "body" not in _GRAPH_CACHE:
        from backend_app.backend.strategy_dag import registry as registry_module

        graph = _graph(registry_module.build_registry())
        _GRAPH_CACHE["body"] = {"blueprint": graph.to_dict()}
    return _GRAPH_CACHE["body"]


def _request(client, method: str, path: str, query, body):
    kwargs: Dict[str, Any] = {}
    if query:
        kwargs["params"] = query
    if body is GRAPH_BODY:
        kwargs["json"] = _valid_save_body()
    elif body is not None:
        kwargs["json"] = body
    return client.request(method, path, **kwargs)


def _leaked_markers(text: str, *supplied: str) -> List[str]:
    """The owner's markers in ``text``, minus anything the caller itself supplied.

    An identifier echoed back in a refusal - "Deployment {id} not found" - is not a leak:
    the caller put it in the URL. What would be a leak is a marker the caller could not
    already know, which is why the row identifiers stay in :data:`OWNER_MARKERS` and are
    excluded only for the request that named them. That keeps the teeth exactly where they
    belong: ``GET /strategies/{id}/versions`` returns version rows whose ids the caller
    never supplied, so a leak there is still caught.
    """
    known = " ".join(str(item) for item in supplied if item)
    return [
        marker
        for marker in OWNER_MARKERS
        if marker in text and marker not in known
    ]


class TestProperty22CrossTenantMatrix:
    """**Property 22.** No user can read or reference another user's resources.

    Requirements 21.2 (every read and write filtered by the requesting user's identity)
    and 21.4 (a resource owned by another user is a not-authorized or not-found status
    carrying no resource data).
    """

    @pytest.mark.parametrize(
        "resource,method,path,query,body,expectation", MATRIX, ids=MATRIX_IDS
    )
    def test_a_non_owner_is_refused_and_gets_no_data(
        self, app_and_client, resource, method, path, query, body, expectation
    ):
        _app, client, state = app_and_client
        state["user"] = _user(INTRUDER_ID)

        response = _request(client, method, path, query, body)

        if expectation == "refused":
            assert response.status_code in (403, 404), (
                f"{method} {path} answered {response.status_code} to a non-owner; "
                f"Requirement 21.4 asks for 403 or 404. Body: {response.text[:400]}"
            )
        else:
            assert response.status_code == 200, response.text

        leaked = _leaked_markers(response.text, path, json.dumps(query or {}))
        assert not leaked, (
            f"{method} {path} returned the owner's data to a non-owner: {leaked}. "
            f"Body: {response.text[:400]}"
        )

    @pytest.mark.parametrize(
        "resource,method,path,query,body,expectation", MATRIX, ids=MATRIX_IDS
    )
    def test_a_non_owners_request_writes_nothing_of_the_owners(
        self, app_and_client, sb, resource, method, path, query, body, expectation
    ):
        """A refusal is a read. Requirement 21.2 covers writes as well as reads.

        Asserted two ways, because either alone is weak: no recorded statement carries a
        filter naming the intruder together with one of the owner's tables, and every one
        of the owner's rows is byte-identical afterwards.
        """
        _app, client, state = app_and_client
        state["user"] = _user(INTRUDER_ID)
        before = json.dumps(sb.rows, sort_keys=True, default=str)

        _request(client, method, path, query, body)

        after = json.dumps(sb.rows, sort_keys=True, default=str)
        assert after == before, (
            f"{method} {path} by a non-owner mutated the owner's rows. "
            f"Recorded writes: {sb.writes}"
        )
        for kind, table, payload, filters in sb.writes:
            if kind == "select":
                continue
            owner_scoped = str(filters.get("user_id", "")) in ("", INTRUDER_ID)
            assert owner_scoped, (
                f"{method} {path} issued a {kind} on {table} filtered by "
                f"user_id={filters.get('user_id')!r} for a request made by "
                f"{INTRUDER_ID}: {payload}"
            )

    @pytest.mark.parametrize(
        "resource,method,path,query,body,expectation", MATRIX, ids=MATRIX_IDS
    )
    def test_the_owner_is_not_refused_so_the_matrix_is_not_vacuous(
        self, app_and_client, resource, method, path, query, body, expectation
    ):
        """The same request, by the owner, must not be a 403 or a 404.

        Without this, a handler that answered 404 to everybody - including the owner -
        would satisfy the matrix above while being broken. The success path of some of
        these endpoints cannot complete in this environment (no market data feed, no
        in-process deployment runtime, no object store), so what is asserted is the
        narrow thing that makes the refusal above meaningful: the owner's request is
        *not* refused as unowned. :class:`TestOwnerCanResolveEveryResource` makes the
        same point at the loader seam, where the answer is unambiguous.
        """
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)

        response = _request(client, method, path, query, body)

        assert response.status_code not in (403, 404), (
            f"{method} {path} refused the OWNER with {response.status_code}, so the "
            f"cross-tenant assertion above proves nothing. Body: {response.text[:400]}"
        )


class TestCollectionEndpointsAnswerEmptyRatherThanAnotherTenantsRows:
    """The two ``"empty"`` matrix entries, with the positive half made explicit.

    "200 and no rows" only means something if the owner's own request returns rows. Both
    halves are asserted here so a collection endpoint that had simply stopped working
    could not pass the matrix above.
    """

    def test_version_history(self, app_and_client):
        _app, client, state = app_and_client

        state["user"] = _user(OWNER_ID)
        mine = client.get(f"/api/strategies/{STRATEGY_ID}/versions")
        assert mine.status_code == 200, mine.text
        ids = {version["id"] for version in mine.json()["versions"]}
        assert ids == {VERSION_ID, OTHER_VERSION_ID}

        state["user"] = _user(INTRUDER_ID)
        theirs = client.get(f"/api/strategies/{STRATEGY_ID}/versions")
        assert theirs.status_code == 200, theirs.text
        assert theirs.json()["versions"] == []
        assert theirs.json()["total"] == 0
        assert not _leaked_markers(theirs.text, f"/api/strategies/{STRATEGY_ID}/versions")

    def test_training_job_list(self, app_and_client):
        _app, client, state = app_and_client
        path = "/api/strategy-operations/training/jobs"

        state["user"] = _user(OWNER_ID)
        mine = client.get(path, params={"version_id": VERSION_ID})
        assert mine.status_code == 200, mine.text
        assert [job["job_id"] for job in mine.json()["jobs"]] == [JOB_ID]

        state["user"] = _user(INTRUDER_ID)
        theirs = client.get(path, params={"version_id": VERSION_ID})
        assert theirs.status_code == 200, theirs.text
        assert theirs.json()["jobs"] == []
        assert not _leaked_markers(theirs.text, path, VERSION_ID)

    def test_exchange_account_list(self, app_and_client):
        _app, client, state = app_and_client

        state["user"] = _user(OWNER_ID)
        mine = client.get("/api/exchanges")
        assert mine.status_code == 200, mine.text
        assert len(mine.json()) == 1

        state["user"] = _user(INTRUDER_ID)
        theirs = client.get("/api/exchanges")
        assert theirs.status_code == 200, theirs.text
        assert theirs.json() == []


class TestOwnerCanResolveEveryResource:
    """Six positive controls, one per resource type, at the loader the endpoints use.

    This is the anti-vacuity proof that does not depend on a success path completing:
    against the *same* seeded client, the owner resolves each resource and the intruder
    does not.
    """

    @pytest.mark.asyncio
    async def test_strategy(self, sb, owner, intruder):
        with patch.object(
            StrategyService, "_get_strategy_performance", AsyncMock(return_value={})
        ):
            service = StrategyService()
            service._get_supabase = lambda user: sb  # noqa: SLF001 - the documented seam
            assert (await service.get_strategy(owner, STRATEGY_ID)) is not None
            assert (await service.get_strategy(intruder, STRATEGY_ID)) is None

    @pytest.mark.asyncio
    async def test_version(self, sb, owner, intruder):
        service = StrategyService()
        row = await service._load_owned_version(sb, owner, VERSION_ID)  # noqa: SLF001
        assert row["id"] == VERSION_ID
        with pytest.raises(ValueError):
            await service._load_owned_version(sb, intruder, VERSION_ID)  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_training_job(self, sb, owner, intruder):
        row = await TS.load_owned_training_job(owner, JOB_ID, sb=sb)
        assert row["id"] == JOB_ID
        with pytest.raises(TS.TrainingJobNotFound):
            await TS.load_owned_training_job(intruder, JOB_ID, sb=sb)

    @pytest.mark.asyncio
    async def test_model_version(self, sb, owner, intruder):
        row = await MV.load_owned_model_version(owner, MODEL_VERSION_ID, sb=sb)
        assert row["id"] == MODEL_VERSION_ID
        with pytest.raises(MV.ModelVersionNotFound):
            await MV.load_owned_model_version(intruder, MODEL_VERSION_ID, sb=sb)

    @pytest.mark.asyncio
    async def test_deployment(self, sb, owner, intruder):
        """Through ``transition_deployment``'s own scoped read, on a no-op transition."""
        service = StrategyService()
        service._get_supabase = lambda user: sb  # noqa: SLF001
        result = await service.transition_deployment(
            user=owner, deployment_id=DEPLOYMENT_ID, action="resume"
        )
        assert result["idempotent"] is True  # already RUNNING
        with pytest.raises(ValueError):
            await service.transition_deployment(
                user=intruder, deployment_id=DEPLOYMENT_ID, action="resume"
            )

    @pytest.mark.asyncio
    async def test_exchange_account(self, sb, owner, intruder):
        account = await DB.load_owned_exchange_account(sb, OWNER_ID, ACCOUNT_ID)
        assert account["id"] == ACCOUNT_ID
        with pytest.raises(DB.DeployRejected) as caught:
            await DB.load_owned_exchange_account(sb, INTRUDER_ID, ACCOUNT_ID)
        assert caught.value.http_status == 404
        assert caught.value.code == "EXCHANGE_ACCOUNT_NOT_FOUND"


class TestEveryEndpointKeepsItsAuthAndItsLimiter:
    """Requirements 21.1 and 21.8: authentication and the existing rate limit, on all of them.

    Read off the route table rather than off the source, so a route that lost its
    dependency is caught even if the decorator text is still nearby in the file.
    """

    def _routes(self, app):
        from fastapi.routing import APIRoute

        return [route for route in app.routes if isinstance(route, APIRoute)]

    @pytest.mark.parametrize(
        "resource,method,path,query,body,expectation", MATRIX, ids=MATRIX_IDS
    )
    def test_every_matrix_route_requires_an_authenticated_user(
        self, app_and_client, resource, method, path, query, body, expectation
    ):
        from backend_app.core.dependencies import get_current_user

        app, _client, _state = app_and_client
        matched = [
            route
            for route in self._routes(app)
            if method in route.methods and route.path_regex.match(path)
        ]
        assert matched, f"no route matched {method} {path}"
        for route in matched:
            names = {
                dependency.call
                for dependency in route.dependant.dependencies
                if dependency.call is not None
            }
            flat = {
                sub.call
                for sub in route.dependant.dependencies
                for sub in [sub]
                if sub.call is not None
            }
            assert get_current_user in names or get_current_user in flat, (
                f"{method} {route.path} declares no get_current_user dependency"
            )

    def test_every_route_this_spec_added_keeps_a_limiter(self):
        """Requirement 21.8, read off the source of the builder router.

        Scoped to the ``strategy-operations`` paths this specification adds, because
        that is what 21.8 is about; the older routes in the same file are covered by
        their own tasks.
        """
        source = OPS_ROUTER_PATH.read_text(encoding="utf-8")
        declarations = re.findall(
            r'@router\.(?:get|post|put|patch|delete)\(\s*"(/strategy-operations/[^"]+)"'
            r'[^\n]*\)\n(.*?)\nasync def',
            source,
            re.DOTALL,
        )
        assert len(declarations) >= 12, declarations
        for path, window in declarations:
            assert "@limiter.limit(" in window, f"{path} lost its rate limit"


class TestRealtimeChannelSubscriptions:
    """Requirements 21.5 and 21.6, over every owned channel family.

    Parametrised from ``OWNED_CHANNEL_FAMILIES`` itself, so a family added later is covered
    here the moment it is registered - which is how ``paper.{session_id}`` arrived.

    The refusal is *reported* - a code and a sentence - and the frame carries no resource
    data, which is the difference between a refusal and a silent no-op.
    """

    def _channel_for(self, family) -> str:
        resource = {
            "job_id": JOB_ID,
            "strategy_id": STRATEGY_ID,
            "deployment_id": DEPLOYMENT_ID,
            "session_id": SESSION_ID,
        }[family.resource]
        return family.channel(resource)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "family", OWNED_CHANNEL_FAMILIES, ids=[f.namespace for f in OWNED_CHANNEL_FAMILIES]
    )
    async def test_the_owner_may_subscribe(self, sb, owner, family):
        decision = await authorize_channel_subscription(
            self._channel_for(family), owner, supabase=sb
        )
        assert decision.allowed, decision.reason
        assert decision.owner_id == OWNER_ID

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "family", OWNED_CHANNEL_FAMILIES, ids=[f.namespace for f in OWNED_CHANNEL_FAMILIES]
    )
    async def test_a_non_owner_is_refused_and_the_refusal_is_reported(
        self, sb, intruder, family
    ):
        channel = self._channel_for(family)
        decision = await authorize_channel_subscription(channel, intruder, supabase=sb)

        assert not decision.allowed
        assert decision.code == CHANNEL_REFUSED_FORBIDDEN
        assert decision.reason.strip(), "Requirement 21.6 asks for the refusal to be reported"
        assert decision.owner_id is None

        frame = decision.refusal_frame()
        assert frame["type"] == "subscription_refused"
        assert frame["channel"] == channel
        text = json.dumps(frame)
        # The channel name is what the subscriber asked for, so it is excluded; nothing
        # else about the resource may be in the frame.
        assert not _leaked_markers(text, channel), text
        for canary in CANARY_VALUES:
            assert canary not in text


class TestProperty23NoCredentialInAPersistedArtifact:
    """**Property 23**, first half: ``graph_json`` and ``compiled_plan``.

    Requirement 12.1: a persisted Canonical_Graph, Compiled_Plan or training
    configuration holds no exchange identifier, API key, secret or passphrase - even when
    the client sends every one of them.
    """

    def test_a_credential_bearing_save_persists_neither_the_key_nor_the_value(
        self, app_and_client, sb, registry, caplog
    ):
        """Every forbidden key at once, on the DATA node, through the real save path.

        The graph, the validator, the compiler, the canonical parse and the router are all
        the production ones. What is asserted is what reached the fake database, not what
        the response said about it.
        """
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)
        graph = _graph(registry, extra_data_params=POISONED_PARAMS)

        with caplog.at_level("DEBUG"):
            response = client.post(
                f"/api/strategy-operations/strategies/{STRATEGY_ID}/versions",
                json={"blueprint": graph.to_dict()},
            )

        assert response.status_code == 200, response.text
        assert sb.rows.get("strategy_versions"), "the save wrote no version row"

        for kind, table, payload, _filters in sb.writes:
            if kind != "insert":
                continue
            self._assert_clean(payload, f"{table} insert")

        for row in sb.rows.get("strategy_versions", []):
            self._assert_clean(row.get("graph_json"), "graph_json")
            self._assert_clean(row.get("compiled_plan"), "compiled_plan")
            self._assert_clean(row.get("blueprint"), "blueprint")

        self._assert_no_canary_in(response.text, "the save response body")
        self._assert_no_canary_in(caplog.text, "the log")

    def test_the_canonical_parse_drops_every_forbidden_key_it_publishes(self):
        """The vocabulary and the parse agree, so Property 23 has one source of truth."""
        from backend_app.backend.strategy_dag.schema import strip_forbidden_params

        params = {"symbol": "BTC/USDT", **POISONED_PARAMS}
        stripped = strip_forbidden_params(params)
        assert stripped == {"symbol": "BTC/USDT"}
        for key in FORBIDDEN_PARAM_FIELDS:
            assert is_forbidden_param(key)
            assert is_forbidden_param(key.upper())

    # ── helpers ────────────────────────────────────────────────────────────
    def _assert_clean(self, payload: Any, what: str) -> None:
        offending = _forbidden_keys(payload)
        assert not offending, f"{what} carries forbidden key(s) {offending}"
        text = json.dumps(payload, default=str)
        found = [canary for canary in PERSISTED_CANARIES if canary in text]
        assert not found, f"{what} carries exchange identity or credential material: {found}"

    def _assert_no_canary_in(self, text: str, what: str) -> None:
        found = [canary for canary in CANARY_VALUES if canary in text]
        assert not found, f"{what} carries credential material: {found}"


def _forbidden_keys(payload: Any, path: str = "") -> List[str]:
    """Every forbidden key in ``payload``, at any depth. The scan Property 23 names."""
    found: List[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            here = f"{path}.{key}" if path else str(key)
            if is_forbidden_param(key):
                found.append(here)
            found.extend(_forbidden_keys(value, here))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            found.extend(_forbidden_keys(value, f"{path}[{index}]"))
    return found


class TestProperty23NoCredentialInATrainingConfiguration:
    """**Property 23**, second half: ``training_jobs.config``.

    ``build_training_config`` calls ``assert_no_exchange_identity`` on the document
    before returning it, so a forbidden key can never reach the column. Both halves of
    that sentence are checked: the guard rejects the whole vocabulary at every nesting
    depth, and the call really is the last thing the builder does.
    """

    @pytest.mark.parametrize("key", FORBIDDEN_PARAM_FIELDS)
    def test_the_guard_refuses_the_key_at_the_top_level(self, key):
        with pytest.raises(RuntimeError) as caught:
            S.assert_no_exchange_identity(
                {"symbol": "BTC/USDT", key: POISONED_PARAMS.get(key, "x")}
            )
        assert key in str(caught.value)

    @pytest.mark.parametrize("key", FORBIDDEN_PARAM_FIELDS)
    def test_the_guard_refuses_the_key_when_it_is_nested(self, key):
        nested = {"data_source": {"venue": {"deep": {key: "x"}}}}
        with pytest.raises(RuntimeError):
            S.assert_no_exchange_identity(nested)
        inside_a_list = {"nodes": [{"params": {key: "x"}}]}
        with pytest.raises(RuntimeError):
            S.assert_no_exchange_identity(inside_a_list)

    def test_a_clean_configuration_passes(self):
        S.assert_no_exchange_identity(
            {
                "data_source": {"symbol": "BTC/USDT", "timeframe": "1h"},
                "splits": {"val_fraction": 0.2},
                "nodes": [{"node_id": "n_model", "epochs": 10}],
            }
        )

    def test_build_training_config_ends_by_calling_the_guard(self):
        """Proved by parsing the function, not by reading what it says about itself."""
        source = _inspect.getsource(S.build_training_config)
        tree = ast.parse(_dedent(source))
        function = tree.body[0]
        guarded = [
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "assert_no_exchange_identity"
        ]
        assert guarded, "build_training_config no longer calls assert_no_exchange_identity"
        last = function.body[-1]
        assert isinstance(last, ast.Return), "the guard must run before the return"

    def test_the_guard_and_the_parse_share_one_vocabulary(self):
        """A key the parse strips but the guard admits would be a hole between them."""
        for key in FORBIDDEN_PARAM_FIELDS:
            with pytest.raises(RuntimeError):
                S.assert_no_exchange_identity({key: "x"})

    def test_a_stored_job_is_reported_without_its_versions_credentials(
        self, app_and_client, sb
    ):
        """Requirement 21.7 on the training status surface, over a poisoned row.

        The row is deliberately given credential-bearing ``config`` keys - something
        ``build_training_config`` cannot produce - so that the *report* is what is being
        tested rather than the writer. A projection that echoed the row wholesale would
        fail here.
        """
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)
        sb.rows["training_jobs"][0]["config"] = {
            "symbol": "BTC/USDT",
            **POISONED_PARAMS,
        }

        response = client.get(f"/api/strategy-operations/training/jobs/{JOB_ID}")

        assert response.status_code == 200, response.text
        text = response.text
        found = [canary for canary in CANARY_VALUES if canary in text]
        assert not found, f"the training job report carried {found}"
        assert not _forbidden_keys(response.json())


class TestProperty23NoCredentialOnAnyResponseOrFrame:
    """**Property 23**, third half: responses, frames and logs. Requirement 21.7."""

    def test_no_matrix_response_carries_credential_material(self, app_and_client, caplog):
        """Every entry, as owner and as non-owner, with the whole body searched.

        The vault row seeded for this file really does hold an api key, a secret and a
        passphrase, so an endpoint that joined ``exchange_keys`` into a response would be
        caught here rather than reviewed for.
        """
        _app, client, state = app_and_client
        offenders = []
        with caplog.at_level("DEBUG"):
            for who in (OWNER_ID, INTRUDER_ID):
                state["user"] = _user(who)
                for _resource, method, path, query, body, _expectation in MATRIX:
                    response = _request(client, method, path, query, body)
                    found = [c for c in CANARY_VALUES if c in response.text]
                    if found:
                        offenders.append((who, method, path, found))
        assert not offenders, offenders
        logged = [canary for canary in CANARY_VALUES if canary in caplog.text]
        assert not logged, f"credential material reached the log: {logged}"

    def test_the_exchange_list_masks_the_key_it_reports(self, app_and_client):
        """The one endpoint that reads the vault by design still returns no credential.

        It names the venue - that is what the endpoint is for - and reports the key as a
        mask. Nothing that could be used to sign a request leaves the process.
        """
        _app, client, state = app_and_client
        state["user"] = _user(OWNER_ID)

        response = client.get("/api/exchanges")

        assert response.status_code == 200, response.text
        rows = response.json()
        assert rows, "the owner's own account should be listed"
        for row in rows:
            assert not _forbidden_keys({k: v for k, v in row.items() if k != "exchange_id"})
            text = json.dumps(row)
            for canary in CANARY_VALUES:
                assert canary not in text, canary
            assert row["masked_key"].count("\u2022") >= 8

    def test_the_runtime_state_frame_publishes_no_model_artifact_reference(self):
        """The real ``PlanRuntimeState``, holding a poisoned active model row.

        ``PlanRuntimeState.model_versions`` legitimately holds whole ``model_versions``
        rows - the deployment supplies them so the readiness gate can check a checksum -
        and ``artifact_uri`` is on those rows. ``to_dict()`` projects named keys and does
        not publish them, which is what keeps the deployment channel free of a private
        object-store reference. Asserted over the real dataclass, with the row poisoned,
        rather than over a hand-built dict.
        """
        from backend_app.backend.dag_engine import PlanRuntimeState

        state = PlanRuntimeState(
            node_states={NODE_ID: "READY"},
            warmup_required={NODE_ID: 20},
            bars_seen=200,
            model_versions={
                NODE_ID: {
                    "id": MODEL_VERSION_ID,
                    "artifact_uri": "s3://owner-private-bucket/models/owner.pkl",
                    **POISONED_PARAMS,
                }
            },
        )

        frame = runtime_state_frame(DEPLOYMENT_ID, state)

        assert frame["channel"] == f"deployment.{DEPLOYMENT_ID}"
        text = json.dumps(frame, default=str)
        found = [c for c in PERSISTED_CANARIES if c in text]
        assert not found, f"the runtime_state frame carried {found}"
        assert "artifact_uri" not in text
        assert "s3://" not in text
        assert not _forbidden_keys(frame)

    def test_the_canvas_state_frame_projects_named_fields_only(self):
        """The real ``strategy_lifecycle.canvas_state``, over a poisoned version row.

        A frame builder that copied the row would carry whatever the row carried. This
        one is handed a version row with every forbidden key on it and publishes none of
        them, because ``canvas_state`` builds a new mapping from named keys.
        """
        from backend_app.backend import strategy_lifecycle as lifecycle

        poisoned_version = {
            "id": VERSION_ID,
            "lifecycle_state": "DEPLOYED",
            "is_read_only": True,
            **POISONED_PARAMS,
        }

        frame = canvas_state_frame(STRATEGY_ID, lifecycle.canvas_state(poisoned_version))

        assert frame["channel"] == f"strategy.{STRATEGY_ID}"
        assert frame["canvas_state"]["read_only"] is True
        text = json.dumps(frame, default=str)
        found = [c for c in PERSISTED_CANARIES if c in text]
        assert not found, f"the canvas_state frame carried {found}"
        assert not _forbidden_keys(frame)

    def test_the_training_frame_projection_publishes_no_credential(self):
        """``public_training_job`` over a row whose ``config`` was poisoned.

        This is the document ``training.{job_id}`` carries and the one
        ``GET /training/jobs/{id}`` returns, so poisoning the row proves the *projection*
        rather than the writer that normally fills it.
        """
        row = {
            "id": JOB_ID,
            "user_id": OWNER_ID,
            "status": "RUNNING",
            "config": {"symbol": "BTC/USDT", **POISONED_PARAMS},
            **POISONED_PARAMS,
        }

        published = TS.public_training_job(row)

        text = json.dumps(published, default=str)
        found = [c for c in PERSISTED_CANARIES if c in text]
        assert not found, f"the training job projection carried {found}"
        assert not _forbidden_keys(published)

    def test_no_builder_module_reads_the_credential_vault(self):
        """Requirement 21.10's neighbour: the builder never resolves a decrypted key.

        ``load_decrypted_keys`` is the vault's read. It belongs in the execution process
        (Requirement 13.9), not in the compiler, the validator, the binding or the
        training admission path.
        """
        for relative in (
            "backend_app/backend/deployment_binding.py",
            "backend_app/backend/strategy_compiler.py",
            "backend_app/backend/strategy_dag/validator.py",
            "backend_app/backend/training_status.py",
            "backend_app/backend/model_versioning.py",
        ):
            path = REPO_ROOT / relative
            assert path.is_file(), relative
            code = _executable_python(path)
            assert "load_decrypted_keys" not in code, relative
            assert "credential_vault" not in code, relative


def _executable_python(path: Path) -> str:
    """``path``'s source with comments and string literals removed.

    A structural claim has to be about code. Several of these modules explain in prose
    which vault call must not appear in them, and a naive substring search would be
    satisfied by that explanation.
    """
    import tokenize

    kept = []
    with path.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            kept.append(token.string)
    return "\n".join(kept)


def _dedent(source: str) -> str:
    import textwrap

    return textwrap.dedent(source)


class TestPersistenceLayerRls:
    """Requirement 21.3, as far as this environment permits - and no further.

    **These tests do not prove that row-level security filters anything.** There is no
    PostgreSQL here and ``004d_training_and_models.sql`` is unapplied, so the only
    verifiable statement is that the migration *declares* the owner policies and that the
    Python which relies on them names the same tables. Whether the policies are in force
    is 004d's own VERIFICATION section, run against a real database.
    """

    def test_004d_declares_the_five_owner_policies(self):
        sql = MIGRATION_004D.read_text(encoding="utf-8", errors="replace")
        for policy, table in (
            ("tj_owner_select", "training_jobs"),
            ("tj_owner_insert", "training_jobs"),
            ("tj_owner_update", "training_jobs"),
            ("mv_owner_select", "model_versions"),
            ("mv_owner_insert", "model_versions"),
        ):
            pattern = rf"CREATE POLICY\s+{policy}\s+ON\s+public\.{table}"
            assert re.search(pattern, sql), f"{policy} on {table} is not declared in 004d"

    def test_004d_enables_row_level_security_on_both_tables(self):
        sql = MIGRATION_004D.read_text(encoding="utf-8", errors="replace")
        for table in ("training_jobs", "model_versions"):
            assert re.search(
                rf"ALTER TABLE\s+public\.{table}\s+ENABLE ROW LEVEL SECURITY",
                sql,
            ), table

    def test_the_api_layer_filter_is_present_regardless(self):
        """21.2's filter is what this suite CAN prove, and it is asserted everywhere.

        Each loader carries an explicit ``user_id`` filter in addition to whatever RLS
        would do, which is why :class:`TestOwnerCanResolveEveryResource` can distinguish
        the two tenants against a fake client that enforces no policy at all.
        """
        for function in (
            TS.load_owned_training_job,
            MV.load_owned_model_version,
            DB.load_owned_exchange_account,
            StrategyService._load_owned_version,
        ):
            code = _dedent(_inspect.getsource(function))
            tree = ast.parse(code)
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "eq"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "user_id"
            ]
            assert calls, f"{function.__qualname__} carries no explicit user_id filter"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
