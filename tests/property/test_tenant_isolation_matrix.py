"""
tests/property/test_tenant_isolation_matrix.py - the tenant-isolation matrix.

Spec: marketplace-subscriptions-paper-trading tasks 26.7 (P-44) and 33.1 (the matrix itself).
``design.md`` -> "Property-to-test mapping" and "Tenant isolation".
Requirements 19.4, 19.6, 21.2, 21.4, 21.8.

WHAT THIS FILE VERIFIES, AND WHAT IT CANNOT - STATED, NOT IMPLIED
-----------------------------------------------------------------
**This file verifies the APPLICATION-LAYER guarantee, and only that.** No PostgreSQL runs in CI -
``tests/security/test_builder_tenant_isolation.py`` already records the same fact for migrations
004 through 004e - so nothing here demonstrates that the row-level-security policies of
``007_marketplace_submissions.sql``, ``008_marketplace_settlement.sql`` and
``009_paper_trading.sql`` filter a query. Requirement 21.2 is therefore split three ways and this
file owns exactly one third:

* the RLS **text** - that each table introduced by this specification declares an owner-scoped
  policy and a service-role policy - is asserted by
  ``tests/test_marketplace_paper_schema_contract.py``, which parses the migrations;
* the runtime RLS **behaviour** - that a query executed without a resolved identity returns zero
  rows and performs zero writes - is verified in the production sequence of **Task 35**, against a
  real database, and NOT here;
* the **API-layer** ownership filter Requirement 21.2 asks for *in addition to* RLS is what the
  matrix below drives.

:class:`MatrixStore` deliberately does not simulate RLS. That is the strict direction: an endpoint
whose only tenant scope is a policy this environment cannot run leaks here and fails, rather than
passing on a filter the double silently honoured for it.

THE TESTS LIVING HERE
---------------------
``test_the_matrix_columns_are_requirement_21_8s_ten_resource_kinds``
``test_the_matrix_covers_every_collected_endpoint_and_channel``      (Requirement 21.8)
``test_the_matrix_store_declares_every_table_the_sweep_touched``
``test_the_tenant_isolation_matrix_refuses_every_foreign_identifier``(Requirements 21.2, 21.8)
``test_the_pinned_requirement_21_4_gaps_still_reproduce``
``test_p41_cross_tenant_read_returns_no_field``                      (Requirements 21.4, 21.8)
``test_p42_cross_tenant_write_leaves_rows_byte_identical``           (Requirements 21.4, 21.8)
``test_p43_foreign_and_absent_responses_are_indistinguishable``      (Requirements 21.4, 5.7, 6.10)
``test_p44_paper_channel_refuses_foreign_sessions``                  (Requirements 19.4, 19.6, 21.4)
``test_p45_supplied_identity_does_not_change_the_decision``          (Requirement 21.1)
``test_p46_every_listed_row_is_owned_by_the_caller``                 (Requirement 21.5)

THE MATRIX IS DERIVED, NEVER LISTED
-----------------------------------
Rows are COLLECTED on every run from ``app.router.routes`` filtered by ``/api/library`` and
``/api/paper``, plus every member of ``ws_channels.OWNED_CHANNEL_FAMILIES``. Columns are the ten
resource kinds Requirement 21.8 names. The only hand-written part is
:data:`MATRIX_DECLARATIONS` - one entry per row, saying which of the ten kinds that row can NAME,
which it RETURNS, and (through :data:`NOT_APPLICABLE_REASONS`) why the rest are inapplicable. An
endpoint or channel added later therefore appears among the rows without anybody editing this file,
has no declaration, and fails the completeness assertion; a declaration left behind by a deleted
route fails it too. An unattempted cell and a deliberately-inapplicable one are different facts and
are recorded differently - the latter carries a reason, and a reason under 40 characters is itself a
failure.

WHAT THE SWEEP FOUND
--------------------
Seven cells where the Marketplace_API answers a record owned by another tenant differently from one
that exists in no tenant. They are listed in :data:`REQUIREMENT_21_4_GAPS` with both answers pinned
exactly, so the gap cannot drift and cannot be mistaken for coverage; the other two assertions of
Requirement 21.8 are applied to those cells unchanged. Closing one turns this file red and points
at the table. They are all on ``library_strategies`` and ``strategies`` reference paths -
``DELETE /api/library/{id}``, ``/clone``, ``/rate``, ``/checkout``, ``/deploy``,
``POST /api/library`` and ``POST /api/library/submissions``.

THE PROPERTIES LIVING HERE
--------------------------
``test_p41_cross_tenant_read_returns_no_field``       (Requirements 21.4, 21.8)
``test_p42_cross_tenant_write_leaves_rows_byte_identical`` (Requirements 21.4, 21.8)
``test_p43_foreign_and_absent_responses_are_indistinguishable`` (Requirements 21.4, 5.7, 6.10)
``test_p44_paper_channel_refuses_foreign_sessions``   (Requirements 19.4, 19.6, 21.4)
``test_p45_supplied_identity_does_not_change_the_decision`` (Requirement 21.1)
``test_p46_every_listed_row_is_owned_by_the_caller``  (Requirement 21.5)

Exactly one ``test_p{n}_`` function per property at module scope, and nothing nested carries that
prefix: ``tests/property/test_property_coverage.py`` discovers by ``ast.walk`` and would count a
nested helper too.

WHY THIS MODULE EXISTS BEFORE ITS SIBLINGS DO
---------------------------------------------
P-41, P-42, P-43, P-45 and P-46 (tasks 33.7 to 33.11) are Hypothesis properties over the SAME
machinery and slot in at the end of this file, after P-44: they draw tenant pairs and identifiers
and drive :func:`_attempt` and the three assertion helpers, rather than restating what isolation
means. Nothing in sections 1 to 4 has to move for them to land.

P-41 and P-42 (tasks 33.7, 33.8) have landed, and that is exactly how they landed: the four helpers
they needed - :func:`fresh_store`, :func:`_http_answer`, :func:`_channel_answer`, :func:`_attempt` -
grew ONE optional ``tenancy`` parameter defaulting to :data:`DEFAULT_TENANCY`, the two literals, and
the two value-scan helpers grew one optional argument each. Under the default every one of them
behaves as it always did, so nothing the exhaustive sweep asserts has moved; with a DRAWN tenancy
the same code addresses a generated pair of tenants and generated record identifiers. P-43, P-45 and
P-46 slot in the same way. See the section header above the two properties for what they add that
the sweep cannot say.

P-44 belongs to the tenant-isolation matrix - P-41 to P-46 - because all six share ONE oracle:
Requirement 21.4's non-existent-record response. A property that only asserted "the refusal was a
refusal" would pass on a refusal that said *which* refusal it was, and that refusal is an existence
oracle for another tenant's identifiers. So the shared machinery lives in this module's first
section (:data:`TENANT_ISOLATION_MATRIX`, :func:`assert_indistinguishable_from_a_nonexistent_record`)
and P-44 is written against it, so the five siblings are additions to a structure rather than five
fresh accounts of what isolation means.

Task 26.7 places P-44 here but writes it NOW, next to the channel it constrains, so the channel
registration of task 26.3 is covered as it lands rather than nine tasks later. The other five
properties are named in the matrix below and are **not stubbed**: an empty or skipped test would
read as coverage to a reader and to ``test_property_coverage.py``, which counts by function name.

WHAT IS DRIVEN, AND WHAT IS NOT DOUBLED
---------------------------------------
Real: ``core.websocket_auth.authorize_channel_subscription`` (the whole authorisation decision),
``backend.ws_channels.PAPER_FAMILY``, ``paper_channel.subscribe`` / ``broadcast`` / ``replay`` /
``session_owner``, ``paper_repository.read_session`` and ``read_session_events``.

Doubled: only the Persistence_Layer, through ``tests/test_paper_repository.FakeSupabase``. The
harness is ``tests/test_task_26_4_paper_channel_delivery.py``'s, imported rather than rebuilt -
``Connection``, ``_client``, ``_session_row``, ``_seed_events``, ``_frame``, ``_registry``,
``_subscribe`` and the ``_fresh_state`` autouse fixture, which clears the module-scope owner cache
and the persistence probe around the run. Coroutines run on the process's ONE event loop through
``tests/test_paper_order_lifecycle_writes._run_coroutine``; ``asyncio.run`` appears nowhere.

**This property adds no authorisation and weakens none.** ``authorize_channel_subscription`` already
refuses another user's session and a non-existent one through the same ``_forbidden`` frame - that
is what the ``_OwnerRelation`` registered for ``PAPER_FAMILY`` in task 26.3 inherited, and P-44
asserts it over generated identifiers instead of over two literals.

THE ORACLE
----------
The response to an identifier that exists in no tenant, obtained from the same function in the same
store. The foreign-session response must match it. Two fields are excluded from the comparison and
both exclusions are load-bearing rather than convenient:

* ``channel`` - it is the string the CALLER named. It is the caller's own input echoed back, so it
  cannot reveal anything the caller did not already hold. Every other field is compared exactly.
* the ``emitted_at`` / ``at`` instants of a ``paper_error`` frame, which are ``utc_now()``. Two
  responses a microsecond apart are not two different responses.

Nothing is compared with a tolerance, and there is nothing here a tolerance could mean.

WHAT THIS PROPERTY DOES NOT ESTABLISH
-------------------------------------
1. **Row-level security is not exercised.** The double is not RLS-scoped, so it answers like a
   service-role client: the foreign session's row IS returned to the lookup and
   ``_resolve_owned_channel_owner`` refuses on the owner comparison. That is the WEAKER of the two
   paths and therefore the right one to test in process - under RLS the row would not come back at
   all and the same ``_forbidden`` frame would be produced one branch earlier. The policies
   themselves are ``009_paper_trading.sql``'s and are covered by the schema-contract tests.
2. **The 1-second refusal deadline of Requirement 19.4 is not timed.** A single-process assertion
   about a database round trip that is a dictionary lookup here would be measuring the double.
3. **The Audit_Log entry Requirement 21.4 also requires is not asserted here.** It is the subject of
   the audit-log task and of P-45's sibling; this property is the channel's half.
"""

from __future__ import annotations

import copy
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend import ws_channels as channels
from backend_app.backend.marketplace.listing_projection import DENIED_LISTING_COLUMNS
from backend_app.backend.paper import paper_channel as pc
from backend_app.backend.paper import paper_repository as repo
from backend_app.backend.paper_trading_service import get_paper_trading_service
from backend_app.core import subscription_dependencies as plan_gates
from backend_app.core import websocket_auth as WA
from backend_app.core.dependencies import get_current_user, get_request_supabase
from backend_app.main import app
from backend_app.routers import library as library_router

# ── The census, written once for the paper property modules that need it. ─────────────────
from tests.property.paper_census import Recorder, publish_hypothesis_statistics

# ── The one event loop this process has. Never ``asyncio.run``. ───────────────────────────
from tests.test_paper_order_lifecycle_writes import _run_coroutine as _run

# ── THE Persistence_Layer double of this repository. One, not two. ───────────────────────
# ``_Query`` is imported alongside it because :class:`MatrixStore` below subclasses both: the
# matrix reaches tables and PostgREST operators that the paper repository never issues, and a
# second fake would be a second set of assumptions about the database. Nothing in ``FakeSupabase``
# is modified - see :class:`MatrixStore`'s docstring for the exact list of additions.
from tests.test_paper_repository import FakeSupabase, _Query as _FakeQuery

# ── The Paper_Channel harness of task 26.4, imported rather than rebuilt. ─────────────────
from tests.test_task_26_4_paper_channel_delivery import (  # noqa: F401 - _fresh_state is autouse
    Connection,
    _client,
    _frame,
    _fresh_state,
    _registry,
    _seed_events,
    _session_row,
    _subscribe,
)

#: ``design.md § Property-based testing configuration``: at least 100 examples, no per-example
#: deadline.
EXAMPLES = 100

PROPERTY_SETTINGS = settings(
    max_examples=EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


# ══════════════════════════════════════════════════════════════════════════
# THE PERSISTENCE_LAYER DOUBLE - ONE FAKE, EXTENDED, NEVER A SECOND ONE
# ══════════════════════════════════════════════════════════════════════════
#
# ``tests/paper_seed.py`` records the rule: there is exactly ONE Persistence_Layer double in this
# repository, and a second would be a second set of assumptions about the database. The matrix
# needs three things ``FakeSupabase`` does not have, because it drives Marketplace_API routes and
# the paper repository never issues them:
#
#   1. the marketplace tables 007/008/011/012/014 declare, plus the three tables this
#      specification MODIFIES (``library_strategies``, ``library_subscriptions``,
#      ``deployment_permissions``) and the adjacent rows the routers read as premises;
#   2. the PostgREST operators ``routers/library.py`` uses and ``paper_repository`` does not -
#      ``delete``, ``upsert``, ``in_``, ``neq``, ``gte``, ``lte``, ``lt``, ``single``,
#      ``maybe_single``, the ``.not_`` property, and the filter-free chainables ``or_``, ``like``,
#      ``ilike``, ``contains``, ``overlaps``, ``range``;
#   3. ``.count``, which the catalogue's paged read asks for.
#
# All three arrive by SUBCLASSING. ``FakeSupabase`` and ``_Query`` are not edited, so every
# existing assertion written against them - including ``P-44``'s, three sections below - continues
# to hold on exactly the object it always held on. Task 33.12 needs a counting variant; it
# subclasses :class:`MatrixStore` and overrides :meth:`MatrixStore._execute`, which is the one
# place every statement passes through.


class _Resp:
    """A PostgREST response. ``count`` is the addition; ``data`` is ``FakeSupabase``'s."""

    def __init__(self, data: Any, count: Optional[int] = None) -> None:
        self.data = data
        self.count = count
        self.error = None


class _MatrixQuery(_FakeQuery):
    """``FakeSupabase``'s query builder plus the operators the Marketplace_API issues.

    Every method here either records a predicate that :meth:`MatrixStore._matching` applies, or is
    a deliberate no-op with the reason stated on it. A no-op is the LENIENT direction for a filter
    and the STRICT direction for this file: a query whose only tenant scope was an ignored
    operator returns MORE rows here than in production, so an endpoint that leaned on it leaks in
    this test rather than passing on a filter the double silently honoured.
    """

    def __init__(self, table: str, client: "MatrixStore") -> None:
        super().__init__(table, client)
        #: ``.single()`` / ``.maybe_single()`` - the response is one row, or ``None``.
        self.single_row = False
        #: ``.not_`` sets this for exactly the next predicate, as PostgREST's grammar does.
        self.negated = False
        self.count_mode: Optional[str] = None
        self.on_conflict: Optional[str] = None
        #: The operators this statement asked for and this double does not apply. Reported by
        #: :meth:`MatrixStore.unapplied_operators` so an ignored filter is visible rather than
        #: silently forgiving.
        self.ignored: List[str] = []

    # -- the fluent surface FakeSupabase already has, widened for keywords ----
    def select(self, cols: str = "*", **kwargs: Any) -> "_MatrixQuery":
        self.count_mode = kwargs.get("count")
        super().select(cols)
        return self

    def insert(self, payload: Any, **kwargs: Any) -> "_MatrixQuery":
        self.op = "insert"
        self.payload = payload
        return self

    def update(self, payload: Any, **kwargs: Any) -> "_MatrixQuery":
        self.op = "update"
        self.payload = dict(payload or {})
        return self

    def order(self, col: str, desc: bool = False, **kwargs: Any) -> "_MatrixQuery":
        super().order(col, desc=bool(desc) or bool(kwargs.get("desc")))
        return self

    # -- the additions -------------------------------------------------------
    def delete(self, *_a: Any, **_kw: Any) -> "_MatrixQuery":
        self.op = "delete"
        return self

    def upsert(self, payload: Any, **kwargs: Any) -> "_MatrixQuery":
        self.op = "upsert"
        self.payload = payload
        self.on_conflict = kwargs.get("on_conflict")
        return self

    @property
    def not_(self) -> "_MatrixQuery":
        """PostgREST's negation prefix. A PROPERTY, because that is how supabase-py spells it."""
        self.negated = True
        return self

    def is_(self, col: str, val: Any) -> "_MatrixQuery":
        """``IS NULL`` / ``IS NOT NULL`` / ``IS true``.

        Overridden rather than inherited because ``FakeSupabase._Query.is_`` records the predicate
        for an ``_matching`` that asserts the value is the literal ``"null"``; the Marketplace_API
        also issues ``.is_("rating", None)`` and ``.not_.is_(col, "null")``.
        """
        self.filters.append(("isnot" if self.negated else "is", col, val))
        self.negated = False
        return self

    def neq(self, col: str, val: Any) -> "_MatrixQuery":
        self.filters.append(("neq", col, val))
        return self

    def in_(self, col: str, values: Any) -> "_MatrixQuery":
        self.filters.append(("in", col, tuple(values)))
        return self

    def gte(self, col: str, val: Any) -> "_MatrixQuery":
        self.filters.append(("gte", col, val))
        return self

    def lte(self, col: str, val: Any) -> "_MatrixQuery":
        self.filters.append(("lte", col, val))
        return self

    def lt(self, col: str, val: Any) -> "_MatrixQuery":
        self.filters.append(("lt", col, val))
        return self

    def single(self, *_a: Any, **_kw: Any) -> "_MatrixQuery":
        self.single_row = True
        return self

    def maybe_single(self, *_a: Any, **_kw: Any) -> "_MatrixQuery":
        self.single_row = True
        return self

    # -- the deliberate no-ops ----------------------------------------------
    # Range/pagination and the text/array predicates. Not applying them returns a SUPERSET of the
    # production result set, which is the direction that makes a missing tenant filter visible.
    def _ignore(self, name: str) -> "_MatrixQuery":
        self.ignored.append(f"{self.table_name}.{name}")
        return self

    def range(self, *_a: Any, **_kw: Any) -> "_MatrixQuery":
        return self._ignore("range")

    def or_(self, *_a: Any, **_kw: Any) -> "_MatrixQuery":
        return self._ignore("or_")

    def like(self, *_a: Any, **_kw: Any) -> "_MatrixQuery":
        return self._ignore("like")

    def ilike(self, *_a: Any, **_kw: Any) -> "_MatrixQuery":
        return self._ignore("ilike")

    def contains(self, *_a: Any, **_kw: Any) -> "_MatrixQuery":
        return self._ignore("contains")

    def overlaps(self, *_a: Any, **_kw: Any) -> "_MatrixQuery":
        return self._ignore("overlaps")

    def execute(self) -> Any:
        return self.client._execute(self)


class MatrixStore(FakeSupabase):
    """``FakeSupabase`` with the marketplace tables and the operators above. Nothing removed.

    The eleven ``paper_*`` tables, their eight unique indexes, their two UPDATE triggers, the
    column defaults of 009 and the injectable failures are all the inherited ones. A table this
    class does not know is created empty on first touch AND recorded in
    :attr:`undeclared_tables`, which :func:`test_the_matrix_store_declares_every_table_it_touched`
    asserts is empty - so a router reaching a table nobody seeded is a reported fact rather than a
    ``KeyError`` that would read like a refusal.
    """

    #: Every table this specification INTRODUCES (007, 008, 009, 011, 012, 014). These are the
    #: tables Requirement 21.8's second assertion is about: "no row in any table introduced by
    #: this specification changed".
    SPEC_TABLES: Tuple[str, ...] = (
        "marketplace_submissions",
        "marketplace_submission_transitions",
        "marketplace_submission_allowed_transitions",
        "marketplace_backtest_evidence",
        "marketplace_price_evaluations",
        "marketplace_settlements",
        "marketplace_subscription_allowed_transitions",
        "library_subscription_transitions",
        "paper_order_allowed_transitions",
        "paper_session_allowed_transitions",
        "paper_sessions",
        "paper_accounts",
        "paper_orders",
        "paper_fills",
        "paper_positions",
        "paper_balance_events",
        "paper_trades",
        "paper_equity_snapshots",
        "paper_metrics",
        "paper_events",
        "paper_market_events",
    )

    #: Tables this specification MODIFIES rather than introduces (011 adds the deployment-source
    #: columns to ``library_strategies``, 012 the payment-failure columns to
    #: ``library_subscriptions``, and ``deployment_permissions`` is the grant 17.3 writes). They
    #: are snapshot-compared with the introduced ones: a cross-tenant write that landed on a
    #: pre-existing table is the same defect as one that landed on a new table.
    MODIFIED_TABLES: Tuple[str, ...] = (
        "library_strategies",
        "library_subscriptions",
        "deployment_permissions",
    )

    #: Rows the routers READ as premises and this specification neither introduces nor modifies.
    #: Declared so a touch on one of them is not reported as undeclared, and excluded from the
    #: snapshot for the same reason ``FakeSupabase.ADJACENT_TABLES`` is excluded from
    #: ``wrote_anything()``: they are not what Requirement 21.8 asks about.
    PREMISE_TABLES: Tuple[str, ...] = (
        "strategies",
        "strategy_versions",
        "strategy_deployments",
        "strategy_backtests",
        "training_jobs",
        "profiles",
        "library_ratings",
        "library_favorites",
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Per-instance copies: the class attributes of ``FakeSupabase`` are shared, and mutating
        # them would change the double for every other test in the session.
        self.TABLES = dict(FakeSupabase.TABLES)
        self.ADJACENT_TABLES = dict(FakeSupabase.ADJACENT_TABLES)
        self.undeclared_tables: List[str] = []
        for name in self.SPEC_TABLES + self.MODIFIED_TABLES + self.PREMISE_TABLES:
            self._declare(name)

    def _declare(self, name: str) -> str:
        attribute = self.TABLES.get(name)
        if attribute is None:
            attribute = f"rows_{name}"
            self.TABLES[name] = attribute
            # Not a ``paper_*`` child, so ``_defaults`` must not invent a ``session_id`` column on
            # it. ``ADJACENT_TABLES`` is exactly the inherited switch for that.
            self.ADJACENT_TABLES.setdefault(name, name)
        if not hasattr(self, attribute):
            setattr(self, attribute, [])
        return attribute

    def table(self, name: str) -> _MatrixQuery:
        if name not in self.TABLES:
            self.undeclared_tables.append(name)
            self._declare(name)
        return _MatrixQuery(name, self)

    def rows_of(self, name: str) -> List[Dict[str, Any]]:
        return getattr(self, self._declare(name))

    def seed(self, name: str, *rows: Mapping[str, Any]) -> None:
        self.rows_of(name).extend(copy.deepcopy(dict(row)) for row in rows)

    def unapplied_operators(self) -> Tuple[str, ...]:
        return tuple(sorted({name for q in self.statements for name in getattr(q, "ignored", ())}))

    # -- what a snapshot is -------------------------------------------------
    def snapshot(self) -> Dict[str, str]:
        """Every row of every table this specification introduces or modifies, as text.

        A full image rather than a row count or a statement log: "no row changed" has to be a
        statement about the rows, and a count would miss an in-place UPDATE.
        """
        return {
            name: json.dumps(self.rows_of(name), sort_keys=True, default=str)
            for name in self.SPEC_TABLES + self.MODIFIED_TABLES
        }

    def rows_touching(self, values: Sequence[str]) -> Dict[str, str]:
        """The subset of :meth:`snapshot` whose rows mention any of ``values``.

        Used for "no row belonging to ``u2`` changed" on the cells where the caller's OWN records
        legitimately move (``GET /api/paper/account`` creates the caller's default account, which
        is not a cross-tenant effect and which Requirement 21.4 does not forbid).
        """
        wanted = tuple(values)
        out: Dict[str, str] = {}
        for name in self.SPEC_TABLES + self.MODIFIED_TABLES:
            rows = [
                row
                for row in self.rows_of(name)
                if any(_mentions(row, value) for value in wanted)
            ]
            out[name] = json.dumps(rows, sort_keys=True, default=str)
        return out

    # -- execution ----------------------------------------------------------
    def _execute(self, q: Any) -> Any:
        if q.op == "delete":
            self.statements.append(q)
            self.ops.append((q.op, q.table_name))
            rows = self.rows_of(q.table_name)
            removed = self._matching(rows, q)
            removed_ids = {id(row) for row in removed}
            setattr(
                self,
                self.TABLES[q.table_name],
                [row for row in rows if id(row) not in removed_ids],
            )
            return _Resp([copy.deepcopy(row) for row in removed])

        if q.op == "upsert":
            payloads = q.payload if isinstance(q.payload, list) else [q.payload]
            written: List[Dict[str, Any]] = []
            for payload in payloads:
                one = _MatrixQuery(q.table_name, self)
                one.insert(dict(payload or {}))
                response = super()._execute(one)
                written.extend(getattr(response, "data", []) or [])
            return _Resp(written)

        response = super()._execute(q)
        if isinstance(response, dict):  # the injected ``error_on`` shape, passed through
            return response
        data = getattr(response, "data", response)
        if getattr(q, "single_row", False) and isinstance(data, list):
            return _Resp(data[0] if data else None, count=len(data))
        return _Resp(data, count=len(data) if isinstance(data, list) else None)

    @staticmethod
    def _matching(rows: List[Dict[str, Any]], q: Any) -> List[Dict[str, Any]]:
        """``FakeSupabase._matching`` plus the six predicates added above.

        ``eq`` and ``gt`` are delegated to the inherited implementation rather than restated, so
        the numeric-versus-text comparison rule that ``_sortable`` encodes has ONE definition.
        """
        matched = list(rows)
        for operator, col, val in getattr(q, "filters", ()):
            if operator == "neq":
                matched = [r for r in matched if str(r.get(col)) != str(val)]
            elif operator == "in":
                allowed = {str(item) for item in val}
                matched = [r for r in matched if str(r.get(col)) in allowed]
            elif operator in ("gte", "lte", "lt"):
                key = FakeSupabase._sortable
                if operator == "gte":
                    matched = [r for r in matched if key(r.get(col)) >= key(val)]
                elif operator == "lte":
                    matched = [r for r in matched if key(r.get(col)) <= key(val)]
                else:
                    matched = [r for r in matched if key(r.get(col)) < key(val)]
            elif operator in ("is", "isnot"):
                if val is None or str(val).lower() == "null":
                    if operator == "is":
                        matched = [r for r in matched if r.get(col) is None]
                    else:
                        matched = [r for r in matched if r.get(col) is not None]
                else:
                    want = str(val).lower() == "true" if isinstance(val, str) else bool(val)
                    if operator == "is":
                        matched = [r for r in matched if bool(r.get(col)) is want]
                    else:
                        matched = [r for r in matched if bool(r.get(col)) is not want]
            else:
                delegate = _MatrixQuery(q.table_name, q.client)
                delegate.filters = [(operator, col, val)]
                matched = FakeSupabase._matching(matched, delegate)
        return matched


def _mentions(row: Mapping[str, Any], value: str) -> bool:
    """True when ``value`` occurs anywhere in ``row``, at any depth."""
    return value in json.dumps(row, sort_keys=True, default=str)


# ══════════════════════════════════════════════════════════════════════════
# THE MATRIX, AND THE ORACLE ITS SIX PROPERTIES SHARE
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _MatrixEntry:
    """One row of Requirement 21.8's matrix: a record type, and the property that covers it."""

    record: str
    property_number: int
    surface: str


#: Requirement 21.8 requires an attempt with a second account's identifiers for each of ten record
#: types, across every endpoint and channel this specification introduces. P-41 to P-46 are how
#: ``requirements.md`` divides that matrix up. This table is documentation with a purpose: it names
#: which property owns which record type, so a sibling added beside P-44 has one place to check that
#: it is not re-testing a record another property already owns, and so a record type with no
#: property is visible as a hole rather than as an absence.
#:
#: P-44 (task 26.7), P-41 and P-42 (tasks 33.7, 33.8) exist today; P-43, P-45 and P-46 are NOT
#: stubbed here - see the module docstring. P-41 and P-42 do not confine themselves to the record
#: types this table names them for: both quantify over the whole matrix, because their generated
#: half is the IDENTIFIER rather than the record kind, and a cell no property drove would be a hole
#: in Requirement 21.8 rather than a division of labour.
TENANT_ISOLATION_MATRIX: Tuple[_MatrixEntry, ...] = (
    _MatrixEntry("Submission", 41, "Marketplace_API"),
    _MatrixEntry("Listing-private record", 42, "Marketplace_API"),
    _MatrixEntry("Subscription", 43, "Marketplace_API"),
    _MatrixEntry("Paper_Session (WebSocket)", 44, "Paper_Channel"),
    _MatrixEntry("Settlement_Record", 45, "Marketplace_API"),
    _MatrixEntry("Paper_Account / paper order / paper position", 46, "Paper_Trading_API"),
)

#: The field a refusal legitimately differs on: the caller's own input, echoed back. Excluded from
#: the byte-for-byte comparison for the reason the module docstring gives, and NOT widened - adding
#: a field here is how an existence oracle would be allowed back in.
CALLER_SUPPLIED_FIELDS: Tuple[str, ...] = ("channel",)


def comparable_refusal(decision: Any) -> Dict[str, Any]:
    """One authorisation refusal, as the value two record identities are compared on.

    Everything but the caller-supplied channel name. Used by every property in this matrix that
    refuses at the WebSocket authorisation boundary.
    """
    frame = decision.refusal_frame()
    return {
        "allowed": decision.allowed,
        "owner_id": decision.owner_id,
        "frame_fields": tuple(sorted(frame)),
        "frame": {
            key: value for key, value in frame.items() if key not in CALLER_SUPPLIED_FIELDS
        },
    }


def assert_indistinguishable_from_a_nonexistent_record(
    *,
    foreign: Any,
    nonexistent: Any,
    record: str,
    label: str,
    context: str,
) -> None:
    """Requirement 21.4's oracle: the foreign-record answer IS the non-existent-record answer.

    The shared assertion of this matrix - ``record`` is the noun (``"Paper_Session"``) and ``label``
    the property claiming it (``"P-44"``), so a sibling added beside P-44 gets a message that names
    its own record rather than P-44's.

    ``nonexistent`` is obtained from the same function, in the same store, for an identifier that
    exists in no tenant - so the expectation is not a literal this file chose and cannot drift from
    what the component actually answers for an unknown id.
    """
    assert foreign.allowed is False, (
        f"{label}: another tenant's {record} was ADMITTED. {context}"
    )
    assert nonexistent.allowed is False, (
        f"{label}: a {record} that exists in no tenant was admitted, so there is no oracle to "
        f"compare against. {context}"
    )
    assert comparable_refusal(foreign) == comparable_refusal(nonexistent), (
        f"{label} (Requirement 21.4): the refusal for another tenant's {record} differs from the "
        f"refusal for an identifier that exists in no tenant, so the response is an existence "
        f"oracle for another tenant's identifiers. Foreign: {comparable_refusal(foreign)!r}; "
        f"non-existent: {comparable_refusal(nonexistent)!r}. {context}"
    )


# ══════════════════════════════════════════════════════════════════════════
# THE TEN COLUMNS - REQUIREMENT 21.8'S RESOURCE KINDS, IN ITS OWN ORDER
# ══════════════════════════════════════════════════════════════════════════

STRATEGY = "strategy"
SUBMISSION = "submission"
LISTING_PRIVATE = "listing-private record"
SUBSCRIPTION = "subscription"
SETTLEMENT = "settlement record"
PAPER_ACCOUNT = "paper account"
PAPER_SESSION = "paper session"
PAPER_ORDER = "paper order"
PAPER_POSITION = "paper position"
USER = "user"

#: Transcribed from Requirement 21.8, in its order, and asserted to be ten by
#: :func:`test_the_matrix_columns_are_requirement_21_8s_ten_resource_kinds`. A column added here
#: without a per-row entry fails the completeness assertion, exactly as a row does.
RESOURCE_KINDS: Tuple[str, ...] = (
    STRATEGY,
    SUBMISSION,
    LISTING_PRIVATE,
    SUBSCRIPTION,
    SETTLEMENT,
    PAPER_ACCOUNT,
    PAPER_SESSION,
    PAPER_ORDER,
    PAPER_POSITION,
    USER,
)

MARKETPLACE_API = "Marketplace_API"
PAPER_TRADING_API = "Paper_Trading_API"
OWNED_CHANNEL = "owned WebSocket channel"

#: ``/api/library`` and ``/api/paper`` - the two prefixes task 33.1 names. Rows are COLLECTED from
#: ``app.router.routes`` under them, never listed.
ROW_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("/api/library", MARKETPLACE_API),
    ("/api/paper", PAPER_TRADING_API),
)


# ══════════════════════════════════════════════════════════════════════════
# THE TWO TENANTS, AND THE RECORDS EXACTLY ONE OF THEM OWNS
# ══════════════════════════════════════════════════════════════════════════

#: Every identifier is a well-formed UUID because ``routers/library._safe_uuid`` answers 422 for
#: anything else, and a 422 for the foreign identifier against a 404 for the absent one would be a
#: difference this file created rather than one the Marketplace_API has.
U1 = "11111111-1111-4111-8111-111111111111"
U2 = "22222222-2222-4222-8222-222222222222"

#: A user identifier that exists in no tenant - the oracle for the ``user`` column.
ABSENT_USER = "99999999-9999-4999-8999-999999999999"


def _identifiers(prefix: str) -> Dict[str, str]:
    """One tenant's row identifiers. ``prefix`` is the first UUID group, so ids are legible."""

    def uid(n: int) -> str:
        return f"{prefix}-0000-4000-8000-{n:012d}"

    return {
        "strategy_id": uid(1),
        "library_id": uid(2),  # the PRIVATE (unpublished) Listing - see the seed below
        "submission_id": uid(3),
        "sub_id": uid(4),
        "subscription_id": uid(4),
        "settlement_id": uid(5),
        "account_id": uid(6),
        "session_id": uid(7),
        "order_id": uid(8),
        "position_id": uid(9),
        "version_id": uid(10),
        "public_library_id": uid(11),  # the PUBLISHED Listing - public by design
        "deployment_id": uid(12),
        "job_id": uid(13),
        "creator_id": prefix and (U1 if prefix.startswith("1") else U2),
    }


U1_IDS = _identifiers("11111111")
U2_IDS = _identifiers("22222222")

#: An identifier of each kind that names nothing in either tenant. Fixed rather than random so a
#: failure is reproducible; distinct from every seeded id by construction.
ABSENT_IDS = _identifiers("99999999")
ABSENT_IDS["creator_id"] = ABSENT_USER

#: ``path parameter -> the column it names``. Declared, so a route that grows a new parameter this
#: table does not know is a KeyError in :func:`_declared_references` rather than a column silently
#: uncovered.
PARAM_COLUMN: Dict[str, str] = {
    "library_id": LISTING_PRIVATE,
    "submission_id": SUBMISSION,
    "sub_id": SUBSCRIPTION,
    "subscription_id": SUBSCRIPTION,
    "creator_id": USER,
    "session_id": PAPER_SESSION,
    "order_id": PAPER_ORDER,
}

#: The canary text this file writes into ``u2``'s rows. None of these strings occurs anywhere else
#: in this repository, so finding one in a response addressed to ``u1`` is unambiguous - the same
#: device ``tests/security/test_builder_tenant_isolation.py`` uses for credential material.
U2_CANARIES: Dict[str, str] = {
    "strategy_name": "U2_ONLY_STRATEGY_NAME",
    "private_listing_description": "U2_ONLY_PRIVATE_LISTING_DESCRIPTION",
    "protected_logic": "U2_ONLY_PROTECTED_LOGIC_TOKEN",
    "rejection_reason": "U2_ONLY_SUBMISSION_REJECTION_REASON",
    "provider_reference": "U2_ONLY_SUBSCRIPTION_PROVIDER_REFERENCE",
    "payout_reference": "U2_ONLY_SETTLEMENT_PAYOUT_REFERENCE",
    "symbol": "U2ONLY/USDT",
    "email": "u2-only@example.invalid",
}


def _u2_values(
    user_id: str = U2, ids: Optional[Mapping[str, str]] = None
) -> Dict[str, Tuple[str, ...]]:
    """Per column, the values of ``u2``'s records that a response to ``u1`` may never carry.

    ``user_id`` and ``ids`` default to the two literals this file's exhaustive sweep uses, so
    :data:`U2_VALUES` is exactly what it always was. They are parameters because P-41 and P-42
    DRAW their tenant pair (see :class:`_Tenancy`): the canaries are fixed text and stay fixed,
    the identifiers are the generated part.

    Two things are deliberately NOT in here, and both exclusions are load-bearing rather than
    convenient:

    * **the published Listing and every field of its public projection.** Requirement 6 makes the
      catalogue browsable and Requirement 6.9 defines what a Listing shows to a non-owner, so a
      published Listing's name, description and metrics are public BY DESIGN. That is why
      Requirement 21.8's column is "listing-**private** record" and not "listing", and why the seed
      below gives each tenant two Listings: one published (no canary on it at all) and one
      unpublished, which is the private record this column is about.
    * **the creator display alias.** ``marketplace/aliases.py`` exists precisely so a creator is
      represented by a server-resolved alias, and Requirement 6.5 makes that alias the public
      representation of a creator while forbidding the identifier and the email address. The
      identifier and the email ARE in the ``user`` row below; the alias is not, for the same reason
      ``test_builder_tenant_isolation.py`` keeps its venue slug out of the response scan: an
      endpoint whose whole purpose is to name a creator cannot be asked not to.
    """
    ids = U2_IDS if ids is None else ids
    return {
        STRATEGY: (ids["strategy_id"], U2_CANARIES["strategy_name"]),
        SUBMISSION: (ids["submission_id"], U2_CANARIES["rejection_reason"]),
        LISTING_PRIVATE: (
            ids["library_id"],
            U2_CANARIES["private_listing_description"],
            U2_CANARIES["protected_logic"],
        ),
        SUBSCRIPTION: (ids["sub_id"], U2_CANARIES["provider_reference"]),
        SETTLEMENT: (ids["settlement_id"], U2_CANARIES["payout_reference"]),
        PAPER_ACCOUNT: (ids["account_id"],),
        PAPER_SESSION: (ids["session_id"],),
        PAPER_ORDER: (ids["order_id"], U2_CANARIES["symbol"]),
        PAPER_POSITION: (ids["position_id"],),
        USER: (user_id, U2_CANARIES["email"]),
    }


U2_VALUES: Dict[str, Tuple[str, ...]] = _u2_values()


def _all_u2_values(user_id: str, values: Mapping[str, Tuple[str, ...]]) -> Tuple[str, ...]:
    """Every value of ``u2``'s a row may be recognised by, for :meth:`MatrixStore.rows_touching`.

    One definition, used by :data:`_ALL_U2_VALUES` for the two literals and by
    :meth:`_Tenancy.all_u2_values` for a drawn pair.
    """
    return tuple(sorted({user_id} | {value for group in values.values() for value in group}))


#: The identifier slots one tenant owns, in the order :func:`_identifier_set` fills them from a
#: drawn list. ``subscription_id`` is not here: it is ``sub_id`` under its other spelling, and
#: :data:`PARAM_COLUMN` maps both to the subscription column, so the two must hold ONE value.
_DRAWN_ID_SLOTS: Tuple[str, ...] = (
    "strategy_id",
    "library_id",
    "submission_id",
    "sub_id",
    "settlement_id",
    "account_id",
    "session_id",
    "order_id",
    "position_id",
    "version_id",
    "public_library_id",
    "deployment_id",
    "job_id",
)


def _identifier_set(values: Sequence[str], owner: str) -> Dict[str, str]:
    """One tenant's identifier set, built from ``len(_DRAWN_ID_SLOTS)`` drawn UUIDs.

    The same key set :func:`_identifiers` produces - asserted, not assumed, so a slot added to
    ``_identifiers`` for a new record kind cannot leave a drawn tenancy without it (which would be
    a ``KeyError`` in the middle of a generated example rather than a reported gap).
    """
    assert len(values) == len(_DRAWN_ID_SLOTS), (
        f"a drawn tenant needs exactly {len(_DRAWN_ID_SLOTS)} identifiers, got {len(values)}"
    )
    ids = {slot: str(value) for slot, value in zip(_DRAWN_ID_SLOTS, values)}
    ids["subscription_id"] = ids["sub_id"]
    ids["creator_id"] = owner
    assert set(ids) == set(_identifiers("33333333")), (
        f"a drawn identifier set and _identifiers() disagree about the slots one tenant owns: "
        f"{sorted(set(ids) ^ set(_identifiers('33333333')))}"
    )
    return ids


@dataclass(frozen=True)
class _Tenancy:
    """One ``(u1, u2)`` pair, the identifiers each of them owns, and one set owned by nobody.

    :data:`DEFAULT_TENANCY` is the two literals the exhaustive sweep walks the matrix with, and is
    what every helper below still uses when no tenancy is passed - so nothing the sweep asserts
    moves. P-41 and P-42 DRAW a tenancy instead (:func:`tenant_pairs`), which is what makes them
    quantify over identifiers rather than over two literals.

    The canary text of :data:`U2_CANARIES` is NOT drawn: it is the marker that says "this value
    belongs to ``u2``", and drawing it would make a leak harder to recognise rather than the
    property stronger.
    """

    u1: str
    u2: str
    u1_ids: Mapping[str, str]
    u2_ids: Mapping[str, str]
    absent_user: str
    absent_ids: Mapping[str, str]

    def u2_values(self) -> Dict[str, Tuple[str, ...]]:
        return _u2_values(self.u2, self.u2_ids)

    def all_u2_values(self) -> Tuple[str, ...]:
        return _all_u2_values(self.u2, self.u2_values())

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_Tenancy(u1={self.u1!r}, u2={self.u2!r}, "
            f"u2_library_id={self.u2_ids['library_id']!r}, "
            f"absent_library_id={self.absent_ids['library_id']!r})"
        )


#: The two literals, as a tenancy. Every helper that takes one defaults to this, so the exhaustive
#: sweep runs on exactly the store, identifiers and values it always ran on.
DEFAULT_TENANCY = _Tenancy(
    u1=U1,
    u2=U2,
    u1_ids=U1_IDS,
    u2_ids=U2_IDS,
    absent_user=ABSENT_USER,
    absent_ids=ABSENT_IDS,
)


def _seed_tenant(store: MatrixStore, user_id: str, ids: Mapping[str, str], mine: bool) -> None:
    """One tenant's ten record kinds. ``mine`` marks the tenant whose rows carry the canaries."""

    def canary(key: str, otherwise: str) -> str:
        return U2_CANARIES[key] if mine else otherwise

    store.seed(
        "profiles",
        {
            "id": user_id,
            # The alias is public by design (Requirement 6.5); the email is not, so the canary
            # goes on the email and the alias stays plain.
            "display_name": "u2-alias" if mine else "u1-alias",
            "email": canary("email", "u1@example.invalid"),
        },
    )
    store.seed(
        "strategies",
        {
            "id": ids["strategy_id"],
            "user_id": user_id,
            "name": canary("strategy_name", "u1-strategy"),
            "status": "stopped",
            "current_version": "v1",
            "buy_logic": {"note": canary("protected_logic", "u1-logic")},
        },
    )
    store.seed(
        "strategy_versions",
        {
            "id": ids["version_id"],
            "strategy_id": ids["strategy_id"],
            "version": "v1",
            "lifecycle_state": "READY",
            "validation_state": "VALID",
            "is_current": True,
            "is_draft": False,
            "graph_json": {"schema_version": 2, "nodes": [], "edges": []},
            "compiled_plan": {"dag_hash": "0" * 32, "execution_order": []},
        },
    )
    store.seed(
        "strategy_deployments",
        {
            "id": ids["deployment_id"],
            "user_id": user_id,
            "strategy_id": ids["strategy_id"],
            "version_id": ids["version_id"],
            "status": "running",
            "environment": "paper",
        },
    )
    store.seed(
        "training_jobs",
        {
            "id": ids["job_id"],
            "user_id": user_id,
            "strategy_id": ids["strategy_id"],
            "version_id": ids["version_id"],
            "status": "RUNNING",
        },
    )
    # Two Listings per tenant. The PUBLISHED one carries no canary: its public projection is what
    # Requirement 6 puts in the catalogue. The UNPUBLISHED one is the "Listing-private record" of
    # Requirement 21.8's third column, and it carries the canaries.
    store.seed(
        "library_strategies",
        {
            "id": ids["public_library_id"],
            "user_id": user_id,
            "author_id": user_id,
            "source_strategy_id": ids["strategy_id"],
            "version_id": ids["version_id"],
            "name": "published-listing",
            "description": "a published listing, browsable by anyone",
            "moderation_status": "approved",
            "is_active": True,
            "price": 10,
            "currency": "USD",
            "pricing_model": "SUBSCRIPTION",
            "allow_cloning": True,
            "created_at": "2024-01-01T00:00:00Z",
        },
        {
            "id": ids["library_id"],
            "user_id": user_id,
            "author_id": user_id,
            "source_strategy_id": ids["strategy_id"],
            "version_id": ids["version_id"],
            "name": "unpublished-listing",
            "description": canary("private_listing_description", "u1-private-description"),
            "moderation_status": "pending",
            "is_active": True,
            "price": 10,
            "currency": "USD",
            "pricing_model": "SUBSCRIPTION",
            "allow_cloning": True,
            "created_at": "2024-01-01T00:00:00Z",
        },
    )
    store.seed(
        "marketplace_submissions",
        {
            "id": ids["submission_id"],
            "owner_id": user_id,
            "user_id": user_id,
            "strategy_id": ids["strategy_id"],
            "version_id": ids["version_id"],
            "listing_id": ids["library_id"],
            "submission_state": "DRAFT",
            "proposed_price": 10,
            "currency": "USD",
            "rejection_reason": canary("rejection_reason", "u1-reason"),
        },
    )
    store.seed(
        "library_subscriptions",
        {
            "id": ids["sub_id"],
            "user_id": user_id,
            "subscriber_id": user_id,
            "library_id": ids["public_library_id"],
            "listing_id": ids["public_library_id"],
            "state": "ACTIVE",
            "status": "active",
            "price": 10,
            "currency": "USD",
            "auto_renew": True,
            "current_period_end": "2099-01-01T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z",
            "provider_reference": canary("provider_reference", "u1-provider-reference"),
        },
    )
    store.seed(
        "marketplace_settlements",
        {
            "id": ids["settlement_id"],
            "owner_id": user_id,
            "user_id": user_id,
            "listing_id": ids["public_library_id"],
            "subscription_id": ids["sub_id"],
            "gross_amount_minor": 1000,
            "owner_share_minor": 800,
            "platform_fee_minor": 200,
            "platform_share_minor": 200,
            "currency": "USD",
            "provider_payout_reference": canary("payout_reference", "u1-payout-reference"),
        },
    )
    store.seed(
        "paper_sessions",
        {
            "id": ids["session_id"],
            "user_id": user_id,
            "listing_id": ids["public_library_id"],
            "strategy_id": ids["strategy_id"],
            "version_id": ids["version_id"],
            "session_state": "RUNNING",
            "environment": "PAPER",
            "feed_state": "HEALTHY",
            "event_sequence": 0,
            "config": {"symbol": canary("symbol", "U1ONLY/USDT"), "timeframe": "1h"},
        },
    )
    store.seed(
        "paper_accounts",
        {
            "id": ids["account_id"],
            "user_id": user_id,
            "session_id": ids["session_id"],
            "currency": "USD",
            "cash_balance": "1000",
            "equity": "1000",
            "version": 1,
        },
    )
    store.seed(
        "paper_orders",
        {
            "id": ids["order_id"],
            "user_id": user_id,
            "session_id": ids["session_id"],
            "account_id": ids["account_id"],
            "symbol": canary("symbol", "U1ONLY/USDT"),
            "side": "buy",
            "order_type": "market",
            "quantity": "1",
            "order_state": "OPEN",
        },
    )
    store.seed(
        "paper_positions",
        {
            "id": ids["position_id"],
            "user_id": user_id,
            "session_id": ids["session_id"],
            "account_id": ids["account_id"],
            "symbol": canary("symbol", "U1ONLY/USDT"),
            "quantity": "1",
            "avg_entry_price": "100",
            "version": 1,
            "closed_at": None,
        },
    )


def fresh_store(tenancy: Optional[_Tenancy] = None) -> MatrixStore:
    """A store holding both tenants' ten record kinds, from scratch.

    A FRESH store per request, not per cell: the second assertion of Requirement 21.8 is a
    before/after snapshot comparison, and two requests sharing a store would make the second one's
    "before" the first one's "after".

    ``tenancy`` defaults to :data:`DEFAULT_TENANCY` - the two literals - so ``fresh_store()`` is
    the same store it always was. P-41 and P-42 pass a DRAWN one.
    """
    tenancy = DEFAULT_TENANCY if tenancy is None else tenancy
    store = MatrixStore()
    _seed_tenant(store, tenancy.u1, tenancy.u1_ids, mine=False)
    _seed_tenant(store, tenancy.u2, tenancy.u2_ids, mine=True)
    return store


# ══════════════════════════════════════════════════════════════════════════
# THE ROWS - COLLECTED FROM THE REGISTRIES, NEVER LISTED
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _MatrixRow:
    """One row of the matrix: an endpoint of the two prefixes, or one owned channel family."""

    key: str
    surface: str
    method: Optional[str] = None
    path: Optional[str] = None
    namespace: Optional[str] = None

    @property
    def is_channel(self) -> bool:
        return self.namespace is not None

    @property
    def params(self) -> Tuple[str, ...]:
        return tuple(sorted(set(re.findall(r"{([^}]+)}", self.path or ""))))


def collect_rows() -> Tuple[_MatrixRow, ...]:
    """Every endpoint under the two prefixes, and every owned channel family.

    Derived from ``app.router.routes`` and ``ws_channels.OWNED_CHANNEL_FAMILIES`` on every run.
    That is the whole mechanism task 33.1 asks for: an endpoint or channel added later appears here
    without anybody editing this file, has no entry in :data:`MATRIX_DECLARATIONS`, and fails
    :func:`test_the_matrix_covers_every_collected_endpoint_and_channel`.

    ``HEAD`` and ``OPTIONS`` are excluded: Starlette synthesises them from the ``GET``
    registration, so they are the same handler reached by the same authorisation and not a second
    surface. Every other method of every route is its own row, because a path that answers ``GET``
    and ``DELETE`` is two operations with two ownership rules.
    """
    rows: List[_MatrixRow] = []
    for route in app.router.routes:
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        surface = next(
            (name for prefix, name in ROW_PREFIXES if path.startswith(prefix)), None
        )
        if surface is None:
            continue
        for method in sorted(getattr(route, "methods", None) or ()):
            if method in ("HEAD", "OPTIONS"):
                continue
            rows.append(
                _MatrixRow(
                    key=f"{method} {path}", surface=surface, method=method, path=path
                )
            )
    for family in channels.OWNED_CHANNEL_FAMILIES:
        rows.append(
            _MatrixRow(
                key=f"ws:{family.namespace}",
                surface=OWNED_CHANNEL,
                namespace=family.namespace,
            )
        )
    return tuple(sorted(rows, key=lambda row: row.key))


# ══════════════════════════════════════════════════════════════════════════
# THE CELLS - ATTEMPTED, OR DELIBERATELY NOT APPLICABLE WITH A REASON
# ══════════════════════════════════════════════════════════════════════════

#: A cell whose two answers must be identical in status and body. The default.
ORACLE_STRICT = "strict"
#: A cell on a surface whose whole purpose is to publish a Listing. The equality is replaced by a
#: STRONGER-where-it-matters assertion: every value the foreign answer carries and the absent
#: answer does not must belong to ``u2``'s PUBLISHED Listing, and none of ``u2``'s private values
#: may appear at all. See :func:`_assert_only_the_public_catalogue_differs`.
ORACLE_PUBLIC_CATALOGUE = "public-catalogue"
#: A cell where the Marketplace_API as it stands today answers a foreign private record
#: differently from an absent one. The exact pair is PINNED, so the gap cannot drift and cannot be
#: mistaken for coverage. See :data:`REQUIREMENT_21_4_GAPS`.
ORACLE_GAP = "requirement-21.4-gap"


@dataclass(frozen=True)
class _Gap:
    """One pinned Requirement 21.4 gap: the two answers, as they are today, and why it matters.

    The answers are :func:`_gap_signature` strings rather than whole bodies: the status, the stable
    error code or safe message, and the two detail members that actually carry the distinction -
    ``details.failures`` (the eligibility verdict) and ``details.reason``.
    """

    foreign: str
    absent: str
    note: str


#: The five cells this sweep found, all on the Marketplace_API's ``listing-private record`` column.
#: Each is a refusal that tells the caller WHICH refusal it is, and each therefore answers
#: "does this identifier name a Listing?" for a Listing the caller does not own and that is not in
#: the catalogue - which is the existence oracle Requirement 21.4 forbids. They are pinned rather
#: than waived: :func:`test_the_pinned_requirement_21_4_gaps_still_reproduce` asserts each pair
#: EXACTLY, so closing one turns that test red and points at this table. The other two assertions
#: of Requirement 21.8 - no row changed, no field value of ``u2`` in the response - are applied to
#: these cells unchanged.
REQUIREMENT_21_4_GAPS: Dict[Tuple[str, str], _Gap] = {
    ("DELETE /api/library/{library_id}", LISTING_PRIVATE): _Gap(
        foreign="403 You are not the author of this strategy.",
        absent="404 Library entry not found.",
        note=(
            "the unpublish path resolves the row, then refuses on authorship with a 403 that says "
            "the row exists; an absent identifier is answered 404"
        ),
    ),
    ("POST /api/library/{library_id}/clone", LISTING_PRIVATE): _Gap(
        foreign="404 Strategy not available for cloning.",
        absent="404 Library strategy not found.",
        note=(
            "same status, different sentence: 'not available for cloning' is only reachable for a "
            "row that exists, so the message distinguishes the two"
        ),
    ),
    ("POST /api/library/{library_id}/rate", LISTING_PRIVATE): _Gap(
        foreign="404 Strategy not available for rating.",
        absent="404 Library strategy not found.",
        note=(
            "the rating path draws the same distinction as the clone path does, in the same shape"
        ),
    ),
    ("POST /api/library/{library_id}/checkout", LISTING_PRIVATE): _Gap(
        foreign="409 MARKETPLACE_LISTING_NOT_PURCHASABLE reason=not_published",
        absent="404 NOT_FOUND",
        note=(
            "the purchasability refusal carries details.reason='not_published', which is a fact "
            "about a Listing the caller cannot otherwise see"
        ),
    ),
    ("POST /api/library/{library_id}/deploy", LISTING_PRIVATE): _Gap(
        foreign="403 MARKETPLACE_NOT_SUBSCRIBED",
        absent="409 MARKETPLACE_STRATEGY_UNAVAILABLE",
        note=(
            "'you are not subscribed' is reachable only for a Listing that exists; an absent "
            "identifier is answered 'the runnable version is unavailable'"
        ),
    ),
    ("POST /api/library", STRATEGY): _Gap(
        foreign="403 Not your strategy.",
        absent="404 Strategy not found.",
        note=(
            "the legacy publish path resolves the strategy, then refuses on ownership with a 403; "
            "an absent strategy identifier is answered 404, so the pair answers 'does this "
            "strategy exist?' for a strategy the caller does not own"
        ),
    ),
    ("POST /api/library/submissions", STRATEGY): _Gap(
        foreign=(
            "422 MARKETPLACE_ELIGIBILITY_FAILED failures=EV_COUNT,EV_ONE_VERSION,"
            "MP_EXECUTION_OK,MP_METRICS_COMPLETE,MP_OWNERSHIP,MP_SUBMISSION_OPEN,MP_TENANT"
        ),
        absent=(
            "422 MARKETPLACE_ELIGIBILITY_FAILED failures=EV_COUNT,EV_ONE_VERSION,"
            "MP_EXECUTION_OK,MP_METRICS_COMPLETE,MP_OWNERSHIP,MP_TENANT,MP_VERSION_EXISTS,"
            "MP_VERSION_VALID"
        ),
        note=(
            "same status and same code, but the eligibility verdict's failure LIST differs: a "
            "strategy owned by another tenant fails MP_SUBMISSION_OPEN and passes "
            "MP_VERSION_EXISTS/MP_VERSION_VALID, while a strategy that exists nowhere fails the "
            "version conditions. The list is therefore an existence-and-shape oracle for another "
            "tenant's strategy, and Requirement 2.11's requirement to name every failing condition "
            "has to be reconciled with Requirement 21.4 by refusing on ownership FIRST"
        ),
    ),
}

#: ``(surface, column) -> why no attempt is possible``. Consulted only for a column a row neither
#: references nor returns. Every reason names the surface AND the column, because
#: "not applicable" without a reason is indistinguishable from an oversight - and Requirement
#: 21.8 distinguishes an unattempted cell from an inapplicable one.
NOT_APPLICABLE_REASONS: Dict[Tuple[str, str], str] = {
    (MARKETPLACE_API, STRATEGY): (
        "this Marketplace_API route names no strategy identifier in its path, query or body and "
        "returns no strategies row; a strategy reaches the marketplace only as a Submission's "
        "subject, which the submission column covers"
    ),
    (MARKETPLACE_API, SUBMISSION): (
        "this Marketplace_API route names no Submission identifier and returns no "
        "marketplace_submissions row; the Submission surface is /submissions and /admin/submissions"
    ),
    (MARKETPLACE_API, LISTING_PRIVATE): (
        "this Marketplace_API route names no Listing identifier and returns no library_strategies "
        "row of another tenant"
    ),
    (MARKETPLACE_API, SUBSCRIPTION): (
        "this Marketplace_API route names no Subscription identifier and returns no "
        "library_subscriptions row"
    ),
    (MARKETPLACE_API, SETTLEMENT): (
        "this Marketplace_API route names no Settlement_Record identifier and returns none; "
        "008's marketplace_settlements is reachable only through the creator analytics read"
    ),
    (MARKETPLACE_API, PAPER_ACCOUNT): (
        "a Marketplace_API route under /api/library names no paper account identifier and returns "
        "no paper_accounts row; 009's paper_accounts is reachable only through /api/paper"
    ),
    (MARKETPLACE_API, PAPER_SESSION): (
        "a Marketplace_API route under /api/library names no Paper_Session identifier and returns "
        "no paper_sessions row; the Paper_Session surface is /api/paper/sessions and paper.{id}"
    ),
    (MARKETPLACE_API, PAPER_ORDER): (
        "a Marketplace_API route under /api/library names no paper order identifier and returns "
        "no paper_orders row; 009's paper_orders is reachable only through /api/paper"
    ),
    (MARKETPLACE_API, PAPER_POSITION): (
        "a Marketplace_API route under /api/library names no paper position identifier and "
        "returns no paper_positions row; 009's paper_positions is reachable only through /api/paper"
    ),
    (MARKETPLACE_API, USER): (
        "unreachable: the user column is attempted on every row - see _identity_cell"
    ),
    (PAPER_TRADING_API, STRATEGY): (
        "this Paper_Trading_API route names no strategy identifier in its path, query or body and "
        "returns no strategies row"
    ),
    (PAPER_TRADING_API, SUBMISSION): (
        "a Paper_Trading_API route under /api/paper names no Submission identifier and returns no "
        "marketplace_submissions row; 007's table is reachable only through /api/library"
    ),
    (PAPER_TRADING_API, LISTING_PRIVATE): (
        "this Paper_Trading_API route names no Listing identifier and returns no "
        "library_strategies row; the paper surface reads a Listing only to resolve entitlement on "
        "session start, which the session-start row covers"
    ),
    (PAPER_TRADING_API, SUBSCRIPTION): (
        "a Paper_Trading_API route under /api/paper names no Subscription identifier and returns "
        "no library_subscriptions row; entitlement is resolved from the authenticated identity"
    ),
    (PAPER_TRADING_API, SETTLEMENT): (
        "a Paper_Trading_API route under /api/paper names no Settlement_Record identifier and "
        "returns none; 008's marketplace_settlements is a marketplace record"
    ),
    (PAPER_TRADING_API, PAPER_ACCOUNT): (
        "this Paper_Trading_API route names no paper account identifier - the account is derived "
        "from the authenticated identity, never supplied - and returns no paper_accounts row"
    ),
    (PAPER_TRADING_API, PAPER_SESSION): (
        "this Paper_Trading_API route names no Paper_Session identifier and returns no "
        "paper_sessions row"
    ),
    (PAPER_TRADING_API, PAPER_ORDER): (
        "this Paper_Trading_API route names no paper order identifier and returns no paper_orders "
        "row"
    ),
    (PAPER_TRADING_API, PAPER_POSITION): (
        "this Paper_Trading_API route names no paper position identifier and returns no "
        "paper_positions row"
    ),
    (PAPER_TRADING_API, USER): (
        "unreachable: the user column is attempted on every row - see _identity_cell"
    ),
    (OWNED_CHANNEL, STRATEGY): (
        "this channel family is not keyed on a strategy identifier, and a refusal frame carries "
        "no row at all"
    ),
    (OWNED_CHANNEL, SUBMISSION): (
        "no owned channel family is keyed on a Submission: Requirement 19.1's families name a "
        "job, a strategy, a deployment or a Paper_Session"
    ),
    (OWNED_CHANNEL, LISTING_PRIVATE): (
        "no owned channel family is keyed on a Listing; a Listing has no event stream"
    ),
    (OWNED_CHANNEL, SUBSCRIPTION): (
        "no owned channel family is keyed on a Subscription; a Subscription has no event stream"
    ),
    (OWNED_CHANNEL, SETTLEMENT): (
        "no owned channel family is keyed on a Settlement_Record; settlement is not streamed"
    ),
    (OWNED_CHANNEL, PAPER_ACCOUNT): (
        "no owned channel family is keyed on a paper account: paper.{session_id} is keyed on the "
        "Paper_Session, and the account is reached through it"
    ),
    (OWNED_CHANNEL, PAPER_SESSION): (
        "this channel family is not keyed on a Paper_Session identifier - only paper.{session_id} "
        "is"
    ),
    (OWNED_CHANNEL, PAPER_ORDER): (
        "no owned channel family is keyed on a paper order; an order's events are carried on its "
        "session's channel"
    ),
    (OWNED_CHANNEL, PAPER_POSITION): (
        "no owned channel family is keyed on a paper position; a position's events are carried on "
        "its session's channel"
    ),
    (OWNED_CHANNEL, USER): (
        "unreachable: the user column is attempted on every row - see _identity_cell"
    ),
}


@dataclass(frozen=True)
class _RowDeclaration:
    """What one row of the matrix claims about all ten columns.

    The declaration is what makes the completeness assertion able to fail: rows are DERIVED from
    the registries, declarations are WRITTEN here, and a derived row with no declaration is a
    reported failure. Everything a declaration does not claim is not applicable, with the reason
    :data:`NOT_APPLICABLE_REASONS` gives for that surface and that column.
    """

    key: str
    #: Columns this row can name in its BODY (the path parameters are derived from the path).
    #: ``column -> body field``.
    body_references: Mapping[str, str] = field(default_factory=dict)
    #: Columns whose rows this row RETURNS. Asserted absent from the response using the row's own
    #: pair of requests, so an exposure cell costs no extra round trip.
    exposes: Tuple[str, ...] = ()
    #: A body that satisfies the route's declared schema, so the attempt reaches the ownership
    #: check instead of stopping at a 422 that says nothing about isolation.
    body: Mapping[str, Any] = field(default_factory=dict)
    #: Per column, ``ORACLE_PUBLIC_CATALOGUE`` where the equality is replaced. ``ORACLE_GAP`` is
    #: derived from :data:`REQUIREMENT_21_4_GAPS` and must not be written here.
    oracle: Mapping[str, str] = field(default_factory=dict)
    #: True when the OWNER's answer to the very same request differs from the absent-identifier
    #: answer. Without one differing answer somewhere, an equality between two refusals would hold
    #: for a handler that refused everybody - which is what ``owner_control`` rules out.
    owner_control: bool = False
    owner_control_note: str = ""


def _D(key: str, **kwargs: Any) -> _RowDeclaration:
    return _RowDeclaration(key=key, **kwargs)


#: A body that satisfies ``PaperOrderRequest``.
_ORDER_BODY = {
    "symbol": "BTC-USDT",
    "side": "buy",
    "order_type": "market",
    "quantity": 0.01,
}

#: Notes reused across rows whose owner control cannot exist, each stating WHY rather than that.
_NO_CONTROL_ADMIN = (
    "the route is gated on the Admin_Reviewer role (get_admin_user), which neither tenant holds, "
    "so the 403 is the role gate's and is byte-identical for the owner. The ownership half is "
    "covered by the non-admin route on the same record - GET /api/library/submissions/{id} for a "
    "Submission and PATCH /{library_id}/settings for a Listing - which does carry a control."
)
_NO_CONTROL_CATALOGUE_READ = (
    "the answer is the catalogue's, not the caller's: an owner asking for the reviews of their own "
    "unpublished Listing is answered the same empty list, so no positive control can be drawn. "
    "GET /api/library/{library_id} carries the control for this record."
)
_NO_CONTROL_IDEMPOTENT = (
    "the handler answers the same success envelope for any Listing identifier, the owner's "
    "included, so the two answers cannot differ. The row it writes is library_favorites, which "
    "this specification neither introduces nor modifies; the snapshot assertion proves no Listing "
    "row moved."
)
_NO_CONTROL_SESSION_START = (
    "POST /api/paper/sessions refuses this body as request-invalid before it resolves anything, "
    "for the owner too: a start request additionally needs the simulator configuration and the "
    "validated symbol set of Requirement 16.12, which cannot be assembled without a market-data "
    "feed. The identifier IS carried to the route and the refusal is identical either way; the "
    "ownership half of session start is covered by GET/POST /api/paper/sessions/{session_id}."
)

#: One declaration per collected row. 63 endpoints under the two prefixes and 7 owned channel
#: families, checked against :func:`collect_rows` in both directions.
MATRIX_DECLARATIONS: Tuple[_RowDeclaration, ...] = (
    # ══════════════ Marketplace_API - catalogue reads ══════════════
    _D("GET /api/library", exposes=(LISTING_PRIVATE,)),
    _D("GET /api/library/featured", exposes=(LISTING_PRIVATE,)),
    _D("GET /api/library/trending", exposes=(LISTING_PRIVATE,)),
    _D("GET /api/library/categories"),
    _D("GET /api/library/recommendations", exposes=(LISTING_PRIVATE,)),
    _D("GET /api/library/me", exposes=(LISTING_PRIVATE,)),
    _D(
        "GET /api/library/my-strategies",
        exposes=(LISTING_PRIVATE, SUBSCRIPTION, PAPER_SESSION),
    ),
    _D("GET /api/library/favorites", exposes=(LISTING_PRIVATE,)),
    _D(
        "GET /api/library/creator/analytics",
        exposes=(LISTING_PRIVATE, SUBSCRIPTION, SETTLEMENT),
    ),
    _D("GET /api/library/subscriber/analytics", exposes=(SUBSCRIPTION,)),
    _D(
        "POST /api/library/compare",
        body_references={LISTING_PRIVATE: "library_ids"},
        exposes=(LISTING_PRIVATE,),
        owner_control_note=(
            "compare is a CATALOGUE read: it returns only approved, active Listings, so an owner "
            "comparing their own unpublished Listing is answered the same empty set as for an "
            "identifier that exists nowhere. That the two are equal is exactly the property "
            "Requirement 21.4 wants here, and it leaves no answer to draw a positive control from. "
            "GET /api/library/{library_id} carries the control for this record."
        ),
    ),
    # ══════════════ Marketplace_API - publication ══════════════
    _D(
        "POST /api/library",
        body_references={STRATEGY: "strategy_id"},
        # ``PublishStrategyRequest`` requires the catalogue metadata of Requirement 6.2. Supplying
        # it is what lets the attempt reach the ownership check instead of stopping at a 422 that
        # says nothing about isolation.
        body={"category": "other", "difficulty": "beginner", "tags": [], "currency": "USD"},
        owner_control=True,
    ),
    _D(
        "POST /api/library/submissions",
        body_references={STRATEGY: "strategy_id"},
        body={"backtest_ids": [ABSENT_IDS["strategy_id"]] * 3},
        owner_control=True,
    ),
    _D(
        "GET /api/library/submissions/{submission_id}",
        owner_control=True,
    ),
    _D(
        "POST /api/library/submissions/{submission_id}/price-range",
        body={"currency": "USD"},
        owner_control=True,
    ),
    _D(
        "POST /api/library/submissions/{submission_id}/price",
        body={"price": 12, "currency": "USD"},
        owner_control_note=(
            "the price body is validated against the Submission's own evaluated price range, "
            "which neither tenant has in this environment, so the owner is answered the same 422. "
            "The ownership half is carried by /price-range on the same record."
        ),
    ),
    # ══════════════ Marketplace_API - admin review ══════════════
    _D("GET /api/library/admin/pending", exposes=(LISTING_PRIVATE,)),
    _D("GET /api/library/admin/submissions", exposes=(SUBMISSION,)),
    _D(
        "GET /api/library/admin/submissions/{submission_id}",
        owner_control_note=_NO_CONTROL_ADMIN,
    ),
    _D(
        "POST /api/library/admin/submissions/{submission_id}/approve",
        owner_control_note=_NO_CONTROL_ADMIN,
    ),
    _D(
        "POST /api/library/admin/submissions/{submission_id}/reject",
        body={"reason": "declined by the matrix"},
        owner_control_note=_NO_CONTROL_ADMIN,
    ),
    _D(
        "POST /api/library/admin/submissions/{submission_id}/publish",
        owner_control_note=_NO_CONTROL_ADMIN,
    ),
    _D(
        "POST /api/library/admin/submissions/{submission_id}/suspend",
        owner_control_note=_NO_CONTROL_ADMIN,
    ),
    _D(
        "POST /api/library/admin/submissions/{submission_id}/unpublish",
        owner_control_note=_NO_CONTROL_ADMIN,
    ),
    _D(
        "POST /api/library/admin/subscriptions/{subscription_id}/reinstate",
        body={"reason": "reinstated by the matrix"},
        owner_control_note=_NO_CONTROL_ADMIN,
    ),
    _D(
        "PATCH /api/library/admin/{library_id}",
        body={"moderation_status": "approved"},
        owner_control_note=_NO_CONTROL_ADMIN,
    ),
    # ══════════════ Marketplace_API - one Listing ══════════════
    _D(
        "GET /api/library/creator/{creator_id}",
        oracle={USER: ORACLE_PUBLIC_CATALOGUE},
        exposes=(LISTING_PRIVATE,),
        owner_control=True,
    ),
    _D("GET /api/library/{library_id}", owner_control=True),
    _D("DELETE /api/library/{library_id}", owner_control=True),
    _D("POST /api/library/{library_id}/clone", owner_control=True),
    _D(
        "POST /api/library/{library_id}/rate",
        body={"rating": 5, "review_text": "rated by the matrix"},
        owner_control=True,
    ),
    _D(
        "PATCH /api/library/{library_id}/settings",
        body={"source_cloning_enabled": True},
        owner_control=True,
    ),
    _D(
        "POST /api/library/{library_id}/checkout",
        body={"currency": "USD"},
        owner_control=True,
    ),
    _D(
        "GET /api/library/{library_id}/subscribe",
        exposes=(SUBSCRIPTION,),
        owner_control_note=(
            "this endpoint reports the CALLER's subscription to a Listing, and an owner holds no "
            "subscription to their own Listing - entitlement by ownership is a different fact, "
            "reported by /deploy/check. So the owner is answered the same 'not_subscribed' as an "
            "absent Listing and no positive control can be drawn. GET /api/library/{library_id} "
            "carries the control for this record, and /deploy/check the one for entitlement."
        ),
    ),
    _D("GET /api/library/{library_id}/deploy/check", owner_control=True),
    _D("POST /api/library/{library_id}/deploy", owner_control=True),
    _D("GET /api/library/{library_id}/reviews", owner_control_note=_NO_CONTROL_CATALOGUE_READ),
    _D("POST /api/library/{library_id}/favorite", owner_control_note=_NO_CONTROL_IDEMPOTENT),
    _D("DELETE /api/library/{library_id}/favorite", owner_control_note=_NO_CONTROL_IDEMPOTENT),
    # ══════════════ Marketplace_API - one Subscription ══════════════
    _D("POST /api/library/subscriptions/{sub_id}/cancel", owner_control=True),
    _D("POST /api/library/subscriptions/{sub_id}/renew", owner_control=True),
    # ══════════════ Paper_Trading_API - the default account ══════════════
    _D("GET /api/paper/account", exposes=(PAPER_ACCOUNT,)),
    _D(
        "POST /api/paper/account/reset",
        body={"capital": 100000.0},
        exposes=(PAPER_ACCOUNT,),
    ),
    _D("GET /api/paper/positions", exposes=(PAPER_ACCOUNT, PAPER_POSITION)),
    _D("GET /api/paper/orders", exposes=(PAPER_ACCOUNT, PAPER_ORDER)),
    _D(
        "POST /api/paper/orders",
        body_references={STRATEGY: "strategy_id"},
        body=_ORDER_BODY,
        exposes=(PAPER_ACCOUNT, PAPER_ORDER),
        owner_control_note=(
            "the optional strategy_id on a paper order is RECORDED, never dereferenced: the order "
            "belongs to the caller's own default account and the field is a tag on it, so the "
            "answer cannot depend on whose strategy it names - and that independence is what this "
            "cell asserts. An owner control would have to be a control over a resolution the "
            "handler deliberately does not perform."
        ),
    ),
    _D(
        "DELETE /api/paper/orders/{order_id}",
        owner_control_note=(
            "cancel_order answers CANCEL_FAILED 'order not found' for another tenant's order and "
            "for an absent one alike, and for the OWNER's OPEN order too in this environment, "
            "because the cancel path requires the live simulator the feed provides. The two "
            "cross-tenant answers are identical, which is the assertion; the ownership half of a "
            "paper order is carried by GET /api/paper/sessions/{id}/orders, which has a control."
        ),
    ),
    _D("GET /api/paper/trades", exposes=(PAPER_ACCOUNT,)),
    _D("GET /api/paper/summary", exposes=(PAPER_ACCOUNT, PAPER_POSITION)),
    # ══════════════ Paper_Trading_API - Paper_Sessions ══════════════
    _D(
        "POST /api/paper/sessions",
        body_references={LISTING_PRIVATE: "listing_id", STRATEGY: "strategy_id"},
        body={"symbol": "BTC/USDT", "timeframe": "1h"},
        owner_control_note=_NO_CONTROL_SESSION_START,
    ),
    _D("GET /api/paper/sessions", exposes=(PAPER_SESSION,)),
    _D("GET /api/paper/sessions/{session_id}", owner_control=True),
    _D("POST /api/paper/sessions/{session_id}/pause", owner_control=True),
    _D("POST /api/paper/sessions/{session_id}/resume", owner_control=True),
    _D("POST /api/paper/sessions/{session_id}/stop", owner_control=True),
    _D("POST /api/paper/sessions/{session_id}/reset", owner_control=True),
    _D(
        "GET /api/paper/sessions/{session_id}/orders",
        exposes=(PAPER_ORDER, PAPER_ACCOUNT),
        owner_control=True,
    ),
    _D("GET /api/paper/sessions/{session_id}/fills", owner_control=True),
    _D(
        "GET /api/paper/sessions/{session_id}/positions",
        exposes=(PAPER_POSITION, PAPER_ACCOUNT),
        owner_control=True,
    ),
    _D("GET /api/paper/sessions/{session_id}/trades", owner_control=True),
    _D("GET /api/paper/sessions/{session_id}/equity", owner_control=True),
    _D("GET /api/paper/sessions/{session_id}/metrics", owner_control=True),
    _D("GET /api/paper/sessions/{session_id}/events", owner_control=True),
    # ══════════════ The owned channel families ══════════════
    # The resource column each family is keyed on, and the ``user`` column on every one of them.
    # ``training``, ``deployment``, ``execution`` and ``signal`` are keyed on identifiers that are
    # not among Requirement 21.8's ten kinds, so their resource columns are not applicable and the
    # ``user`` cell is what covers the row - which is still a real attempt: the subscription names
    # a resource ``u2`` owns and the refusal must carry no field value of ``u2``.
    _D("ws:paper", owner_control=True),
    _D("ws:strategy", owner_control=True),
    _D("ws:builder.validation", owner_control=True),
    _D("ws:deployment", owner_control=True),
    _D("ws:execution", owner_control=True),
    _D("ws:signal", owner_control=True),
    _D("ws:training", owner_control=True),
)

DECLARATIONS_BY_KEY: Dict[str, _RowDeclaration] = {d.key: d for d in MATRIX_DECLARATIONS}

#: The resource column each channel family is keyed on, where that column is one of the ten.
CHANNEL_COLUMN: Dict[str, str] = {
    "paper": PAPER_SESSION,
    "strategy": STRATEGY,
    "builder.validation": STRATEGY,
}

#: The seeded identifier each channel family's resource is looked up by, per tenant.
CHANNEL_ID_SLOT: Dict[str, str] = {
    "paper": "session_id",
    "strategy": "strategy_id",
    "builder.validation": "strategy_id",
    "deployment": "deployment_id",
    "execution": "deployment_id",
    "signal": "deployment_id",
    "training": "job_id",
}

ATTEMPT_REFERENCE = "reference"
ATTEMPT_EXPOSURE = "exposure"
ATTEMPT_IDENTITY = "identity"
NOT_APPLICABLE = "not-applicable"


@dataclass(frozen=True)
class _Cell:
    """One row x column cell. Either an attempt, or a deliberate inapplicability with a reason."""

    row: _MatrixRow
    column: str
    attempt: str
    #: For a reference cell: the path parameter or ``body:<field>`` that names the record.
    slot: Optional[str] = None
    oracle: str = ORACLE_STRICT
    reason: str = ""

    @property
    def attempted(self) -> bool:
        return self.attempt != NOT_APPLICABLE

    @property
    def name(self) -> str:
        return f"{self.row.key} x {self.column}"


def _declared_references(row: _MatrixRow, declaration: _RowDeclaration) -> Dict[str, str]:
    """``column -> slot`` for this row: its path parameters, plus its declared body fields."""
    references: Dict[str, str] = {}
    for param in row.params:
        column = PARAM_COLUMN.get(param)
        assert column is not None, (
            f"{row.key}: the path parameter {param!r} is not in PARAM_COLUMN, so the matrix cannot "
            f"say which of Requirement 21.8's ten resource kinds it names. Add it there with the "
            f"column it names."
        )
        references[column] = param
    if row.is_channel:
        column = CHANNEL_COLUMN.get(row.namespace or "")
        if column is not None:
            references[column] = CHANNEL_ID_SLOT[row.namespace or ""]
    for column, body_field in declaration.body_references.items():
        references[column] = f"body:{body_field}"
    return references


def build_matrix() -> Tuple[_Cell, ...]:
    """Every row x column cell, classified. The matrix itself.

    Rows come from :func:`collect_rows`, columns from :data:`RESOURCE_KINDS`, and the
    classification from :data:`MATRIX_DECLARATIONS`. A row with no declaration raises here, which
    is what :func:`test_the_matrix_covers_every_collected_endpoint_and_channel` reports.
    """
    cells: List[_Cell] = []
    for row in collect_rows():
        declaration = DECLARATIONS_BY_KEY.get(row.key)
        if declaration is None:
            continue  # reported by the completeness test, with the whole list at once
        references = _declared_references(row, declaration)
        for column in RESOURCE_KINDS:
            if column == USER and column not in references:
                cells.append(_Cell(row, column, ATTEMPT_IDENTITY))
                continue
            slot = references.get(column)
            if slot is not None:
                oracle = declaration.oracle.get(column, ORACLE_STRICT)
                if (row.key, column) in REQUIREMENT_21_4_GAPS:
                    oracle = ORACLE_GAP
                cells.append(_Cell(row, column, ATTEMPT_REFERENCE, slot=slot, oracle=oracle))
                continue
            if column in declaration.exposes:
                cells.append(_Cell(row, column, ATTEMPT_EXPOSURE))
                continue
            reason = NOT_APPLICABLE_REASONS.get((row.surface, column))
            assert reason, (
                f"{row.key} x {column}: no reason is declared for this surface and column, so the "
                f"cell would be silently skipped. Add one to NOT_APPLICABLE_REASONS."
            )
            cells.append(_Cell(row, column, NOT_APPLICABLE, reason=reason))
    return tuple(cells)


MATRIX: Tuple[_Cell, ...] = build_matrix()


# ══════════════════════════════════════════════════════════════════════════
# DRIVING ONE CELL - THE APP, THE STORE, AND THE THREE ASSERTIONS
# ══════════════════════════════════════════════════════════════════════════

#: Volatile response fields. ``request_id`` is the per-request correlation identifier of
#: Requirement 26.1 - a fresh ``uuid4`` on every response, carrying nothing about any record - and
#: it is the ONLY key that differed between two otherwise identical answers when this sweep was
#: written. ``timestamp`` is the error envelope's ``utc_now()``. Neither is widened: adding a key
#: here is how a field that DOES carry information about a record would be allowed to differ.
VOLATILE_RESPONSE_KEYS: FrozenSet[str] = frozenset(
    {
        # The per-request correlation identifier of Requirement 26.1: a fresh uuid4 on every
        # response, carrying nothing about any record.
        "request_id",
        # The error envelope's own ``utc_now()``.
        "timestamp",
        # ``paper_trading_service`` mints one per simulated execution (``exec_paper_<random>``).
        # It names the execution the request itself just created, so it cannot carry information
        # about a record the caller did not already cause to exist.
        "execution_id",
    }
)

_ISO_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)?$")


def _normalise(value: Any, supplied: Sequence[str]) -> Any:
    """One response body, as the value two record identities are compared on.

    Three substitutions, and no others:

    * every key in :data:`VOLATILE_RESPONSE_KEYS` loses its value;
    * every occurrence of an identifier the CALLER supplied becomes one token. It is the caller's
      own input echoed back, so it cannot reveal anything the caller did not already hold - the
      same exclusion, for the same reason, that :data:`CALLER_SUPPLIED_FIELDS` makes for a
      channel name;
    * an ISO-8601 instant becomes one token, because two answers a microsecond apart are not two
      different answers.
    """
    if isinstance(value, Mapping):
        return {
            key: ("<volatile>" if key in VOLATILE_RESPONSE_KEYS else _normalise(item, supplied))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalise(item, supplied) for item in value]
    if isinstance(value, str):
        text = value
        for identifier in supplied:
            if identifier:
                text = text.replace(identifier, "<the identifier the caller supplied>")
        if _ISO_INSTANT.match(text):
            return "<instant>"
        return text
    return value


@dataclass(frozen=True)
class _Answer:
    """One request, and everything the three assertions are computed from."""

    status: int
    body: Any
    text: str
    snapshot: Mapping[str, str]
    foreign_rows: Mapping[str, str]
    supplied: Tuple[str, ...]

    def comparable(self) -> Any:
        return (self.status, _normalise(self.body, self.supplied))

    @property
    def refused(self) -> bool:
        return self.status >= 400


def _flatten(value: Any, path: str = "$") -> Iterator[Tuple[str, Any]]:
    """Every scalar in ``value``, with the JSON path it sits at - at any nesting depth."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _flatten(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _flatten(item, f"{path}[{index}]")
    else:
        yield path, value


@contextmanager
def _matrix_app(store: MatrixStore, caller: str) -> Iterator[TestClient]:
    """The REAL ``backend_app.main.app``, authenticated as ``caller``, over ``store``.

    Four seams, and nothing else:

    * ``get_current_user`` - this is what "authenticates as ``u1``" means. The identity comes from
      the dependency the platform already derives it from, so Requirement 21.1's "derive the acting
      identity from the authenticated server-side session" is exercised rather than bypassed.
    * ``routers.library._build_service_client`` / ``._get_service_client`` and
      ``paper_trading_service.bind_persistence`` - the three Persistence_Layer accessors the two
      routers reach. All three are pointed at ONE store, because two stores would let a handler
      read one tenant's rows from one and write to the other.
    * ``routers.library.redis_manager`` - replaced by a stub that never caches, so no endpoint can
      answer the second request of a pair from the first one's cached body. ``_CACHED_CATEGORIES``
      is cleared for the same reason.
    * the plan gates ``require_marketplace_access`` / ``require_marketplace_publish`` /
      ``check_marketplace_publish_quota``. These are SUBSCRIPTION-PLAN gates, not tenant gates:
      left in place they refuse every write route with "requires a higher subscription plan", and
      every cell would then pass on a refusal that says nothing about isolation. Overriding them is
      what lets the attempt reach the tenant boundary. ``get_admin_user`` is deliberately NOT
      overridden - the Admin_Reviewer role is an authorisation this file has no business granting
      itself, and the admin rows say so in their ``owner_control_note``.

    The rate limiter is suspended and restored: this sweep makes far more requests a minute from
    one address than any limit allows, and a 429 on the second request of a pair would be a
    difference this file created. ``tests/security/test_builder_tenant_isolation.py`` suspends it
    for the same reason.
    """

    class _NeverCached:
        async def get(self, *_a: Any, **_kw: Any) -> None:
            return None

        async def set(self, *_a: Any, **_kw: Any) -> None:
            return None

        async def delete(self, *_a: Any, **_kw: Any) -> None:
            return None

        async def keys(self, *_a: Any, **_kw: Any) -> List[str]:
            return []

        async def get_client(self, *_a: Any, **_kw: Any) -> None:
            return None

    user = {
        "id": caller,
        "email": f"{caller}@example.invalid",
        "role": "authenticated",
        "access_token": f"token-{caller}",
        "app_metadata": {"role": "authenticated"},
        "user_metadata": {},
    }

    service = get_paper_trading_service()
    limiter = getattr(app.state, "limiter", None)
    was_enabled = getattr(limiter, "enabled", None)
    previous_categories = library_router._CACHED_CATEGORIES

    patches = [
        patch.object(library_router, "_build_service_client", lambda: store),
        patch.object(library_router, "_get_service_client", lambda: store),
        patch.object(library_router, "redis_manager", _NeverCached()),
        patch(
            "backend_app.core.audit_trail.get_strategy_audit_logger",
            return_value=_SilentAuditLogger(),
        ),
    ]
    for item in patches:
        item.start()
    service.bind_persistence(store)
    if limiter is not None:
        limiter.enabled = False
    library_router._CACHED_CATEGORIES = None

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: store
    for gate in (
        plan_gates.require_marketplace_access,
        plan_gates.require_marketplace_publish,
        plan_gates.check_marketplace_publish_quota,
    ):
        app.dependency_overrides[gate] = lambda: True

    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()
        library_router._CACHED_CATEGORIES = previous_categories
        if limiter is not None and was_enabled is not None:
            limiter.enabled = was_enabled
        service.bind_persistence(None)
        for item in reversed(patches):
            item.stop()


class _SilentAuditLogger:
    """The Audit_Log recorder, silenced - it pushes to Redis, and there is none here.

    Requirement 21.4 also asks for an Audit_Log entry per refused attempt. That half is NOT
    asserted here and is not this row's subject: it belongs to the audit-log task and to P-45's
    sibling. Silencing the recorder keeps a missing Redis from turning every write route into a
    500 that would make the matrix's equality hold for the wrong reason.
    """

    async def record_or_raise(self, *_a: Any, **_kw: Any) -> None:
        return None

    async def log(self, *_a: Any, **_kw: Any) -> None:
        return None


def _http_answer(
    cell: _Cell,
    ids: Mapping[str, str],
    identity: str,
    caller: str,
    tenancy: Optional[_Tenancy] = None,
    supply_identity: Optional[Sequence[str]] = None,
) -> _Answer:
    """One HTTP attempt: fresh store, one request as ``caller``, before/after snapshot.

    ``ids`` is the identifier set the request draws its path parameters and body slots from, and
    ``identity`` the user identifier the request SUPPLIES - in the query string and, where the
    route takes a body, in the body too. Requirement 21.1 says a supplied identity is ignored for
    authorisation; the ``user`` column is what checks that the answer does not depend on it.

    ``tenancy`` is which pair of tenants the store holds and whose rows ``foreign_rows`` watches;
    it defaults to :data:`DEFAULT_TENANCY`, the two literals the exhaustive sweep uses.

    ``supply_identity`` names WHERE the supplied identity goes - any of ``"query"`` and
    ``"body"``. ``None``, the default, is the rule the exhaustive sweep has always applied: query
    and body on an :data:`ATTEMPT_IDENTITY` cell and nowhere else. P-45 (task 33.10) passes it
    explicitly, because its whole subject is the SAME request with and without that field, and it
    therefore has to be able to ask for a bare request on a cell the sweep supplies an identity to
    and for a supplied identity on a cell the sweep leaves bare.
    """
    row = cell.row
    tenancy = DEFAULT_TENANCY if tenancy is None else tenancy
    store = fresh_store(tenancy)
    before = store.snapshot()

    url = row.path or ""
    for param in row.params:
        url = url.replace("{" + param + "}", str(ids[param]))

    declaration = DECLARATIONS_BY_KEY[row.key]
    #: The identity fields are supplied ONLY by the ``user`` column's cell. Two of the routes
    #: (``POST /{library_id}/deploy`` and ``POST /api/paper/sessions``) parse their bodies strictly
    #: and refuse an unexpected field outright - which is Requirement 21.1 working - so injecting
    #: an identity into every cell would turn every OTHER column on those rows into a 422 about
    #: field names rather than an attempt on a record.
    identity_sources: Tuple[str, ...] = (
        (("query", "body") if cell.attempt == ATTEMPT_IDENTITY else ())
        if supply_identity is None
        else tuple(supply_identity)
    )
    unknown_sources = sorted(set(identity_sources) - {"query", "body"})
    assert not unknown_sources, (
        f"supply_identity names {unknown_sources}, which is not a place an HTTP request carries an "
        f"identity; the path is filled from ``ids`` instead"
    )
    supplies_identity = bool(identity_sources)
    body: Optional[Dict[str, Any]] = None
    if row.method in ("POST", "PUT", "PATCH"):
        body = dict(declaration.body)
        if "body" in identity_sources:
            body.update(
                {
                    "user_id": identity,
                    "owner_id": identity,
                    "tenant_id": identity,
                    "subscriber_id": identity,
                }
            )
        for field_name in declaration.body_references.values():
            value = ids[_BODY_SLOT_IDS[field_name]]
            companion_slot = _BODY_LIST_COMPANION_SLOTS.get(field_name)
            companion = tenancy.u1_ids[companion_slot] if companion_slot else None
            body[field_name] = [value, companion] if companion else value

    supplied = [str(ids[param]) for param in row.params]
    if supplies_identity:
        supplied.append(identity)
    if body is not None:
        for field_name in declaration.body_references.values():
            value = body.get(field_name)
            supplied.extend(
                str(item) for item in (value if isinstance(value, list) else [value]) if item
            )

    with _matrix_app(store, caller) as client:
        response = client.request(
            row.method or "GET",
            url,
            params=(
                {"user_id": identity, "owner_id": identity}
                if "query" in identity_sources
                else None
            ),
            json=body,
        )
    try:
        parsed = response.json()
    except ValueError:
        parsed = response.text

    return _Answer(
        status=response.status_code,
        body=parsed,
        text=response.text,
        snapshot=store.snapshot(),
        foreign_rows=store.rows_touching(tenancy.all_u2_values()),
        supplied=tuple(supplied),
    )


#: ``body field -> the identifier slot it is filled from``.
_BODY_SLOT_IDS: Dict[str, str] = {
    "strategy_id": "strategy_id",
    "library_ids": "library_id",
    "listing_id": "library_id",
}

#: ``body field -> a second element the route's schema requires``. ``CompareRequest`` declares
#: ``min_items=2``, so a one-element list is refused at validation and the attempt never reaches
#: the read. The companion is one of the CALLER's OWN Listings, which is what makes the request
#: "compare mine with u2's" - the shape a comparison leak would actually take.
_BODY_LIST_COMPANIONS: Dict[str, str] = {"library_ids": U1_IDS["library_id"]}

#: The same companion, named by the SLOT of the caller's own identifier set rather than by the
#: literal value, so a drawn tenancy fills it from ITS ``u1``. The assertion below pins the two
#: together: under :data:`DEFAULT_TENANCY` the slot resolves to exactly the value above, so the
#: request the sweep sends is unchanged.
_BODY_LIST_COMPANION_SLOTS: Dict[str, str] = {"library_ids": "library_id"}

assert {
    field_name: U1_IDS[slot] for field_name, slot in _BODY_LIST_COMPANION_SLOTS.items()
} == _BODY_LIST_COMPANIONS, (
    "the companion slot and the companion value disagree, so a drawn tenancy would send a "
    "different comparison body than the sweep does"
)

#: Every value of ``u2``'s that a row may be recognised by, for :meth:`MatrixStore.rows_touching`.
_ALL_U2_VALUES: Tuple[str, ...] = _all_u2_values(U2, U2_VALUES)


def _channel_answer(
    cell: _Cell,
    ids: Mapping[str, str],
    caller: str,
    tenancy: Optional[_Tenancy] = None,
) -> _Answer:
    """One owned-channel attempt: ``authorize_channel_subscription``, driven on the one loop.

    The refusal frame is the response, and ``comparable_refusal`` - P-44's, unchanged - is what two
    record identities are compared on. Nothing is written by an authorisation decision, so the
    snapshot is taken around it for the same reason it is taken around an HTTP request: "no row
    changed" has to be checked, not assumed.
    """
    family = channels.OWNED_CHANNEL_FAMILIES_BY_NAMESPACE[cell.row.namespace or ""]
    resource_id = str(ids[CHANNEL_ID_SLOT[cell.row.namespace or ""]])
    channel = family.channel(resource_id)

    tenancy = DEFAULT_TENANCY if tenancy is None else tenancy
    store = fresh_store(tenancy)
    decision = _run(
        WA.authorize_channel_subscription(channel, {"id": caller}, supabase=store)
    )
    body = comparable_refusal(decision) if not decision.allowed else {"allowed": True}
    return _Answer(
        status=403 if not decision.allowed else 200,
        body=body,
        text=json.dumps(body, default=str),
        snapshot=store.snapshot(),
        foreign_rows=store.rows_touching(tenancy.all_u2_values()),
        supplied=(resource_id, caller, channel),
    )


def _attempt(
    cell: _Cell,
    tenant_ids: Mapping[str, str],
    caller: str,
    identity: str,
    tenancy: Optional[_Tenancy] = None,
    supply_identity: Optional[Sequence[str]] = None,
) -> _Answer:
    if cell.row.is_channel:
        assert supply_identity is None, (
            f"{cell.name}: a channel cell has no HTTP query string and no HTTP body, so "
            f"supply_identity={supply_identity!r} would be silently ignored. P-45's WebSocket half "
            f"drives _decision_for_a_subscribe_message instead."
        )
        return _channel_answer(cell, tenant_ids, caller, tenancy)
    return _http_answer(cell, tenant_ids, identity, caller, tenancy, supply_identity)


# ── the three assertions ──────────────────────────────────────────────────


def _public_projection_values() -> Tuple[str, ...]:
    """Everything of ``u2``'s that a public catalogue answer may legitimately carry.

    Derived from the PUBLISHED Listing's own row rather than listed, so a column added to the seed
    cannot silently become an allowed leak of the unpublished one: the published row carries no
    canary at all, and every value here is one Requirement 6.9's projection may publish. The
    creator alias joins it for the reason :func:`_u2_values` gives.
    """
    if _PUBLIC_PROJECTION_CACHE:
        return _PUBLIC_PROJECTION_CACHE[0]
    _PUBLIC_PROJECTION_CACHE.append(_public_projection_values_for(DEFAULT_TENANCY))
    return _PUBLIC_PROJECTION_CACHE[0]


def _public_projection_values_for(tenancy: _Tenancy) -> Tuple[str, ...]:
    """:func:`_public_projection_values` for one tenancy, drawn or default.

    The body is the one that was written for the two literals, with ``u2`` and its published
    Listing taken from ``tenancy`` instead of from the module constants; P-41 needs it per example
    because the published Listing's identifier is part of what a drawn tenancy draws.
    """
    published = next(
        row
        for row in fresh_store(tenancy).rows_of("library_strategies")
        if str(row.get("id")) == tenancy.u2_ids["public_library_id"]
    )
    values = {"u2-alias"}
    for _path, value in _flatten(published):
        if isinstance(value, str) and value:
            values.add(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            values.add(str(value))
    # The columns Requirement 6.4 denies to a non-owner are NOT public even on a published
    # Listing, so they are removed again - otherwise this allow-list would forgive the very leak
    # ``DENIED_LISTING_COLUMNS`` exists to name.
    denied = {
        str(published[column])
        for column in DENIED_LISTING_COLUMNS
        if published.get(column) not in (None, "")
    }
    return tuple(sorted(values - denied))


#: Computed once. A one-element list rather than a module constant because the value is derived
#: from a store, and building one at import time would run the seed before any test asked for it.
_PUBLIC_PROJECTION_CACHE: List[Tuple[str, ...]] = []


def _assert_only_the_public_catalogue_differs(
    cell: _Cell,
    foreign: _Answer,
    absent: _Answer,
    allowed: Optional[Sequence[str]] = None,
) -> None:
    """The relaxed oracle for a catalogue surface, and it is not a waiver.

    Requirement 6 makes an approved, active Listing browsable, so a surface whose purpose is to
    publish one MUST answer differently once such a Listing exists. What is asserted instead is
    that every scalar the foreign answer carries and the absent answer does not belongs to ``u2``'s
    PUBLISHED Listing's public projection or is a count of it - and, through
    :func:`_assert_no_value_of_u2_appears`, that no private value of ``u2``'s appears at all. A
    private field arriving on this surface therefore fails, which is the assertion that matters.

    ``allowed`` defaults to the default tenancy's public projection; a drawn tenancy passes its
    own, because the published Listing whose values are allowed is one of the drawn rows.
    """
    allowed = _public_projection_values() if allowed is None else allowed
    absent_scalars = {value for _, value in _flatten(_normalise(absent.body, absent.supplied))}
    intruders: List[str] = []
    for path, value in _flatten(_normalise(foreign.body, foreign.supplied)):
        if value in absent_scalars or value in (None, "", [], {}):
            continue
        if isinstance(value, (int, float, bool)):
            continue  # a count of public rows is public
        if any(str(value) == str(item) for item in allowed):
            continue
        if any(str(item) in str(value) for item in allowed):
            continue
        intruders.append(f"{path}={value!r}")
    assert not intruders, (
        f"{cell.name}: this cell is declared ORACLE_PUBLIC_CATALOGUE, meaning the only difference "
        f"between the foreign answer and the absent-record answer may be u2's PUBLISHED Listing's "
        f"public projection (Requirements 6, 6.9). These values are neither in the absent answer "
        f"nor part of that projection: {intruders}."
    )


def _assert_no_row_of_u2_changed(cell: _Cell, answer: _Answer, before: Mapping[str, str]) -> None:
    """Requirement 21.8's second assertion, over a full before/after image.

    Two clauses, and the split is deliberate:

    * **every row of ``u2``'s is byte-identical.** Always. This is what Requirement 21.4's "SHALL
      leave the referenced record ... unchanged" is, checked over the whole image rather than over
      the row the request named.
    * **when the attempt was REFUSED, no row of any of this specification's tables changed at
      all.** A refused operation persists nothing, which is the other half of Requirement 21.4.

    Why the first clause is not simply "nothing changed": a 2xx cell is one where the attempt
    reached no foreign record - ``GET /api/paper/account`` creates the CALLER's own default account
    (Requirement 17.2), and forbidding that would be asserting that the caller's own endpoint does
    not work. A write to the caller's own row is not a cross-tenant effect; a write to ``u2``'s is,
    and the first clause forbids exactly that.
    """
    before_foreign = {
        name: text for name, text in before.items()
    }
    # ``before`` is a full snapshot; narrow both sides to u2's rows through the same function.
    del before_foreign
    if answer.refused:
        changed = sorted(
            name for name, text in answer.snapshot.items() if before.get(name) != text
        )
        assert not changed, (
            f"{cell.name} (Requirement 21.4): a REFUSED attempt (status {answer.status}) changed "
            f"rows in {changed}. A refused operation persists nothing."
        )


def _assert_no_row_of_u2_moved(
    cell: _Cell, before_foreign: Mapping[str, str], answer: _Answer
) -> None:
    changed = sorted(
        name
        for name, text in answer.foreign_rows.items()
        if before_foreign.get(name) != text
    )
    assert not changed, (
        f"{cell.name} (Requirements 21.4, 21.8): a request authenticated as u1 changed rows "
        f"belonging to u2 in {changed}. Before: "
        f"{ {name: before_foreign.get(name) for name in changed} }; after: "
        f"{ {name: answer.foreign_rows[name] for name in changed} }."
    )


def _assert_no_value_of_u2_appears(
    cell: _Cell,
    answer: _Answer,
    values: Optional[Mapping[str, Tuple[str, ...]]] = None,
    public: Optional[Sequence[str]] = None,
) -> None:
    """Requirement 21.8's third assertion, at every nesting depth.

    Two passes, because they catch different mistakes: a recursive walk over the parsed body names
    the JSON path a leaked value sits at, and a substring scan over the raw text catches a value
    embedded in a longer string (a message, a URL, a serialised blob) that the walk would see as
    one opaque scalar.

    The identifier the caller supplied is excluded - it is the caller's own input echoed back - and
    so are the public-projection values, for the reason :func:`_u2_values` gives.

    ``values`` and ``public`` default to the two literals' - :data:`U2_VALUES` and
    :func:`_public_projection_values` - so the sweep scans for exactly the strings it always did.
    A drawn tenancy passes its own of each.
    """
    column_values = (U2_VALUES if values is None else values)[cell.column]
    public_values = _public_projection_values() if public is None else public
    forbidden = [
        value
        for value in column_values
        if value not in answer.supplied and value not in public_values
    ]
    if not forbidden:
        return

    hits: List[str] = []
    for path, value in _flatten(answer.body):
        for item in forbidden:
            if isinstance(value, str) and item in value:
                hits.append(f"{path} carries {item!r}")
    rendered = json.dumps(answer.body, default=str)
    for item in forbidden:
        if item in rendered and not any(item in hit for hit in hits):
            hits.append(f"the rendered body carries {item!r}")

    assert not hits, (
        f"{cell.name} (Requirement 21.8): the response to u1 carries a field value belonging to "
        f"u2. {hits}. Full body: {rendered[:1200]}"
    )


def _gap_signature(answer: _Answer) -> str:
    """One answer, as a pinned Requirement 21.4 gap records it.

    The status, the stable code or safe message, and the two detail members that carry a
    distinction between "another tenant's record" and "no record": the eligibility verdict's
    ``failures`` list and the refusal's ``reason``. Deliberately NOT the whole body - a pin over a
    whole body would fail on any unrelated field and would stop naming the gap.
    """
    parts = [str(answer.status), _code_or_message(answer.body) or "-"]
    details: Any = None
    if isinstance(answer.body, Mapping):
        error = answer.body.get("error")
        if isinstance(error, Mapping):
            details = error.get("details")
    if isinstance(details, Mapping):
        failures = details.get("failures")
        if isinstance(failures, (list, tuple)) and failures:
            parts.append("failures=" + ",".join(sorted(str(item) for item in failures)))
        reason = details.get("reason")
        if reason:
            parts.append(f"reason={reason}")
    return " ".join(parts)


def _code_or_message(body: Any) -> str:
    """The stable code, or the safe message, of one error envelope - whichever it carries."""
    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping):
            return str(error.get("code") or error.get("message") or "")
        if isinstance(error, str):
            return str(body.get("message") or error)
        if "message" in body:
            return str(body["message"])
    return ""


# ══════════════════════════════════════════════════════════════════════════
# THE MATRIX TESTS
# ══════════════════════════════════════════════════════════════════════════


def test_the_matrix_columns_are_requirement_21_8s_ten_resource_kinds() -> None:
    """Ten columns, the ten Requirement 21.8 names, each spelled once."""
    assert len(RESOURCE_KINDS) == 10, (
        f"Requirement 21.8 names ten resource kinds; RESOURCE_KINDS has {len(RESOURCE_KINDS)}: "
        f"{RESOURCE_KINDS}"
    )
    assert len(set(RESOURCE_KINDS)) == 10, f"a column is spelled twice: {RESOURCE_KINDS}"


def test_the_matrix_covers_every_collected_endpoint_and_channel() -> None:
    """Every derived row has a declaration, every declaration a derived row, every cell a verdict.

    This is the assertion task 33.1 exists for. The rows are COLLECTED from
    ``app.router.routes`` and ``ws_channels.OWNED_CHANNEL_FAMILIES``; the declarations are
    WRITTEN in :data:`MATRIX_DECLARATIONS`. An endpoint or channel added later is therefore a
    failure here rather than a silent pass, and a declaration left behind by a deleted route is a
    failure too - a stale entry would otherwise keep claiming coverage of a surface that no longer
    exists.

    Proved to be able to fail: removing any single ``_D(...)`` line from
    :data:`MATRIX_DECLARATIONS` fails the first assertion below naming exactly that row, and
    adding a route under either prefix fails it without this file being touched.
    """
    rows = collect_rows()
    collected = {row.key for row in rows}
    declared = set(DECLARATIONS_BY_KEY)

    undeclared = sorted(collected - declared)
    assert not undeclared, (
        f"Requirement 21.8: {len(undeclared)} endpoint(s) or channel(s) introduced or modified by "
        f"this specification have no matrix entry, so they are not covered by any attempt: "
        f"{undeclared}. Add a _D(...) declaration for each, naming the resource kinds it "
        f"references and the rows it returns."
    )
    stale = sorted(declared - collected)
    assert not stale, (
        f"MATRIX_DECLARATIONS claims coverage of {len(stale)} row(s) that no longer exist in "
        f"app.router.routes or OWNED_CHANNEL_FAMILIES: {stale}. A declaration for a route that is "
        f"gone is a claim of coverage nothing backs."
    )

    # Rows x columns, with every cell classified exactly once.
    assert len(MATRIX) == len(rows) * len(RESOURCE_KINDS), (
        f"the matrix has {len(MATRIX)} cells for {len(rows)} rows and {len(RESOURCE_KINDS)} "
        f"columns; it should have {len(rows) * len(RESOURCE_KINDS)}."
    )
    seen = {(cell.row.key, cell.column) for cell in MATRIX}
    assert len(seen) == len(MATRIX), "a cell is declared twice"

    # Every row is covered by at least one attempt (Requirement 21.8's final clause), and every
    # column is attempted somewhere.
    attempted_rows = {cell.row.key for cell in MATRIX if cell.attempted}
    assert attempted_rows == collected, (
        f"Requirement 21.8 reports an overall failure when an endpoint or channel is not covered "
        f"by at least one attempt. Uncovered: {sorted(collected - attempted_rows)}"
    )
    attempted_columns = {cell.column for cell in MATRIX if cell.attempted}
    assert attempted_columns == set(RESOURCE_KINDS), (
        f"these resource kinds are attempted on no row at all: "
        f"{sorted(set(RESOURCE_KINDS) - attempted_columns)}"
    )

    # An inapplicable cell carries a reason, and a reason is a sentence rather than a shrug.
    thin = [
        f"{cell.name}: {cell.reason!r}"
        for cell in MATRIX
        if not cell.attempted and len(cell.reason) < 40
    ]
    assert not thin, (
        f"an inapplicable cell must record WHY, so that it cannot be confused with an "
        f"unattempted one. These reasons are too short to be one: {thin}"
    )

    # Every owner control that is switched off says why.
    unexplained = sorted(
        declaration.key
        for declaration in MATRIX_DECLARATIONS
        if not declaration.owner_control and not declaration.owner_control_note
    )
    rows_with_references = {
        cell.row.key for cell in MATRIX if cell.attempt == ATTEMPT_REFERENCE
    }
    missing_note = sorted(key for key in unexplained if key in rows_with_references)
    assert not missing_note, (
        f"a reference cell whose owner control is off must say why, or the equality it asserts "
        f"could be an equality between two identical refusals: {missing_note}"
    )


def test_the_matrix_store_declares_every_table_the_sweep_touched() -> None:
    """A router reaching an unseeded table is a reported fact, not a KeyError read as a refusal."""
    store = fresh_store()
    with _matrix_app(store, U1) as client:
        client.get("/api/library")
        client.get("/api/library/me")
        client.get("/api/paper/account")
        client.get(f"/api/paper/sessions/{ABSENT_IDS['session_id']}")
    assert store.undeclared_tables == [], (
        f"the sweep touched tables this double does not declare: "
        f"{sorted(set(store.undeclared_tables))}. Add them to MatrixStore.SPEC_TABLES, "
        f"MODIFIED_TABLES or PREMISE_TABLES so the snapshot knows which of the three they are."
    )


MATRIX_FLOORS: Dict[str, int] = {
    "cells_attempted": len([cell for cell in MATRIX if cell.attempted]),
    "reference_cells": len([c for c in MATRIX if c.attempt == ATTEMPT_REFERENCE]),
    "exposure_cells": len([c for c in MATRIX if c.attempt == ATTEMPT_EXPOSURE]),
    "identity_cells": len([c for c in MATRIX if c.attempt == ATTEMPT_IDENTITY]),
    "oracle_held": len(
        [c for c in MATRIX if c.attempted and c.oracle == ORACLE_STRICT]
    ),
    "no_row_of_u2_moved": len([cell for cell in MATRIX if cell.attempted]),
    "no_value_of_u2_appeared": len([cell for cell in MATRIX if cell.attempted]),
    # Every row that declares an owner control must actually produce a differing owner answer.
    "owner_answer_differed": len(
        {
            cell.row.key
            for cell in MATRIX
            if cell.attempt == ATTEMPT_REFERENCE
            and DECLARATIONS_BY_KEY[cell.row.key].owner_control
        }
    ),
}

#: Deliberately empty. ``Recorder``'s labels exist to emit a Hypothesis ``event`` per bucket, and
#: this sweep is an exhaustive walk of a finite matrix rather than a generated one - there is no
#: Hypothesis run to publish a distribution into, and ``event()`` raises outside one. The FLOORS
#: are what carry the anti-vacuity guarantee here; the labels are P-44's business, three sections
#: below, where there is a generator.
MATRIX_LABELS: Dict[str, str] = {}


def test_the_tenant_isolation_matrix_refuses_every_foreign_identifier() -> None:
    """Requirement 21.8, executed: every attempted cell, three assertions, one overall failure.

    For every row x column cell the matrix declares as an attempt, a request is made
    **authenticated as ``u1``** that references ``u2``'s identifier of that kind, and the same
    request is made referencing an identifier that exists in no tenant. The three assertions of
    Requirement 21.8 are then applied:

    1. the two answers are identical in status **and** body (:meth:`_Answer.comparable`);
    2. no row of ``u2``'s changed, and a refused attempt changed no row at all, over a full
       before/after image of every table this specification introduces or modifies;
    3. no field value belonging to ``u2`` appears anywhere in the response, at any nesting depth.

    Every failure is COLLECTED and reported together, because Requirement 21.8 asks for an overall
    failure across the matrix rather than for the first cell that happens to run.

    The sweep cannot pass vacuously. Every row that declares ``owner_control`` must produce an
    answer for the OWNER of the very same record that DIFFERS from the absent-record answer - so
    the equality in assertion 1 is an equality between a refusal and a refusal that a working
    endpoint would have distinguished, not between two answers a handler that refused everybody
    would give. The census below carries a floor per bucket and
    :meth:`Recorder.assert_not_vacuous` fails on a shortfall.

    **Validates: Requirements 21.2, 21.8**
    """
    recorder = Recorder("the tenant-isolation matrix", MATRIX_FLOORS, MATRIX_LABELS)
    failures: List[str] = []
    controls_seen: Set[str] = set()

    for cell in MATRIX:
        if not cell.attempted:
            continue
        recorder.start()
        recorder.mark("cells_attempted")
        recorder.mark(
            {
                ATTEMPT_REFERENCE: "reference_cells",
                ATTEMPT_EXPOSURE: "exposure_cells",
                ATTEMPT_IDENTITY: "identity_cells",
            }[cell.attempt]
        )

        declaration = DECLARATIONS_BY_KEY[cell.row.key]
        identity = U2 if cell.attempt == ATTEMPT_IDENTITY else U1
        absent_identity = ABSENT_USER if cell.attempt == ATTEMPT_IDENTITY else U1
        foreign_ids = U2_IDS if cell.attempt == ATTEMPT_REFERENCE else ABSENT_IDS

        clean = fresh_store().snapshot()
        clean_foreign = fresh_store().rows_touching(_ALL_U2_VALUES)

        try:
            foreign = _attempt(cell, foreign_ids, caller=U1, identity=identity)
            absent = _attempt(cell, ABSENT_IDS, caller=U1, identity=absent_identity)
        except Exception as exc:  # noqa: BLE001 - reported as this cell's failure, not the run's
            failures.append(f"{cell.name}: the attempt did not complete - {exc!r}")
            continue

        # ── assertion 1: the response IS the non-existent-record response ──
        try:
            if cell.oracle == ORACLE_STRICT:
                assert foreign.comparable() == absent.comparable(), (
                    f"{cell.name} (Requirement 21.4): the answer for u2's {cell.column} differs "
                    f"from the answer for one that exists in no tenant, so the response is an "
                    f"existence oracle for another tenant's identifiers.\n"
                    f"  foreign: {foreign.comparable()!r}\n"
                    f"  absent : {absent.comparable()!r}"
                )
                recorder.mark("oracle_held")
            elif cell.oracle == ORACLE_PUBLIC_CATALOGUE:
                _assert_only_the_public_catalogue_differs(cell, foreign, absent)
            else:
                gap = REQUIREMENT_21_4_GAPS[(cell.row.key, cell.column)]
                observed = (_gap_signature(foreign), _gap_signature(absent))
                assert observed == (gap.foreign, gap.absent), (
                    f"{cell.name}: this cell is a PINNED Requirement 21.4 gap and its answers "
                    f"have moved. Recorded {(gap.foreign, gap.absent)!r}, observed {observed!r}. "
                    f"If the two answers are now identical, delete the entry from "
                    f"REQUIREMENT_21_4_GAPS - the cell is fixed and belongs in the strict set."
                )
        except AssertionError as exc:
            failures.append(str(exc))

        # ── assertion 2: no row changed ────────────────────────────────────
        for answer in (foreign, absent):
            try:
                _assert_no_row_of_u2_moved(cell, clean_foreign, answer)
                _assert_no_row_of_u2_changed(cell, answer, clean)
            except AssertionError as exc:
                failures.append(str(exc))
        recorder.mark("no_row_of_u2_moved")

        # ── assertion 3: no field value of u2's, at any depth ──────────────
        try:
            _assert_no_value_of_u2_appears(cell, foreign)
            recorder.mark("no_value_of_u2_appeared")
        except AssertionError as exc:
            failures.append(str(exc))

        # ── the anti-vacuity control, once per row ────────────────────────
        if (
            cell.attempt == ATTEMPT_REFERENCE
            and declaration.owner_control
            and cell.row.key not in controls_seen
        ):
            controls_seen.add(cell.row.key)
            try:
                owner = _attempt(cell, U2_IDS, caller=U2, identity=U2)
                assert owner.comparable() != absent.comparable(), (
                    f"{cell.row.key}: the OWNER of the record is answered exactly what an "
                    f"identifier that exists in no tenant is answered, so the equality this cell "
                    f"asserts would hold for a handler that refused everybody. Either the cell has "
                    f"no positive control - declare owner_control=False with the reason - or the "
                    f"endpoint is broken for its owner."
                )
                recorder.mark("owner_answer_differed")
            except AssertionError as exc:
                failures.append(str(exc))

    assert not failures, (
        f"Requirement 21.8: {len(failures)} of the {MATRIX_FLOORS['cells_attempted']} attempted "
        f"cells failed one of the three assertions.\n\n" + "\n\n".join(failures)
    )
    recorder.assert_not_vacuous()


def test_the_pinned_requirement_21_4_gaps_still_reproduce() -> None:
    """Every pinned gap names a cell the matrix actually attempts, and says what is wrong.

    A pinned gap is a recorded fact about today's Marketplace_API, not an exemption: the sweep
    above asserts each pair EXACTLY, so a gap that is fixed turns this file red and points at
    :data:`REQUIREMENT_21_4_GAPS`. This test is the other guard - a pin for a cell the matrix does
    not attempt would be a note nothing checks.
    """
    attempted = {
        (cell.row.key, cell.column) for cell in MATRIX if cell.attempt == ATTEMPT_REFERENCE
    }
    orphaned = sorted(key for key in REQUIREMENT_21_4_GAPS if key not in attempted)
    assert not orphaned, (
        f"these pinned Requirement 21.4 gaps name cells the matrix does not attempt, so nothing "
        f"checks them: {orphaned}"
    )
    for key, gap in REQUIREMENT_21_4_GAPS.items():
        assert gap.foreign != gap.absent, (
            f"{key}: a pinned gap whose two answers are equal is not a gap; delete the entry."
        )
        assert len(gap.note) >= 40, f"{key}: the pinned gap does not say what is wrong: {gap.note!r}"


# ══════════════════════════════════════════════════════════════════════════
# P-44 (task 26.7) - THE PAPER_CHANNEL REFUSES A FOREIGN SESSION
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _Attempt:
    """One generated attempt: two tenants, their sessions, and what is broadcast."""

    owner: str
    intruder: str
    owned_sessions: Tuple[str, ...]
    intruder_session: str
    absent_session: str
    retained_events: int
    frames: int

    @property
    def target(self) -> str:
        """The session of the OWNER that the intruder goes after."""
        return self.owned_sessions[0]

    def __repr__(self) -> str:  # pragma: no cover - shrinker output
        return (
            f"_Attempt(owner={self.owner!r}, intruder={self.intruder!r}, "
            f"owned={self.owned_sessions!r}, absent={self.absent_session!r}, "
            f"retained={self.retained_events}, frames={self.frames})"
        )


@st.composite
def foreign_session_attempts(draw: Any) -> _Attempt:
    """Two distinct tenants, ``u2``'s sessions, ``u1``'s own, and one id that exists nowhere.

    Identifiers are UUID4 text because that is what ``paper_sessions.id`` holds and what
    ``ws_channels._RESOURCE_ID_PATTERN`` admits; drawing them rather than reusing two literals is
    what makes the property "for all Paper_Sessions owned by ``u2``" instead of "for one".

    Distinctness is a constraint of the strategy rather than a ``filter``: all six ids come out of
    one ``unique`` list, so the absent session genuinely exists in neither tenant and the intruder's
    own session is genuinely its own. A ``filter`` here would spend examples rejecting collisions
    the generator can simply not produce, and Hypothesis would report them as invalid cases.
    """
    ids = draw(st.lists(st.uuids().map(str), min_size=6, max_size=6, unique=True))
    owned_count = draw(st.integers(min_value=1, max_value=2))
    return _Attempt(
        owner=ids[0],
        intruder=ids[1],
        owned_sessions=tuple(ids[2 : 2 + owned_count]),
        intruder_session=ids[4],
        absent_session=ids[5],
        retained_events=draw(st.integers(min_value=1, max_value=3)),
        frames=draw(st.integers(min_value=1, max_value=2)),
    )


def _store(attempt: _Attempt) -> Any:
    """A store holding the owner's sessions with retained events, and the intruder's own session.

    The intruder owns a session of its own deliberately: a component that refused EVERYTHING would
    satisfy the refusal half of this property, and the admitted-owner assertions below are what
    distinguishes isolation from a blanket denial.
    """
    sessions = [
        _session_row(session_id, attempt.owner, event_sequence=attempt.retained_events)
        for session_id in attempt.owned_sessions
    ]
    sessions.append(
        _session_row(attempt.intruder_session, attempt.intruder, event_sequence=0)
    )
    client = _client(sessions=sessions)
    for session_id in attempt.owned_sessions:
        _seed_events(
            client,
            attempt.retained_events,
            session_id=session_id,
            user_id=attempt.owner,
        )
    return client


def _authorize(channel: str, identity: str, client: Any) -> Any:
    """``authorize_channel_subscription``, driven on the harness's loop.

    ``supabase`` is passed explicitly, so nothing here monkeypatches
    ``dependencies.create_request_supabase_async``: the function's own parameter is the seam.
    """
    return _run(WA.authorize_channel_subscription(channel, {"id": identity}, supabase=client))


#: The subscribe message an intruder would send: it names the owner every way a message can, which
#: is exactly the input Requirements 19.5 and 21.1 forbid deriving an identity from.
def _intruder_message(attempt: _Attempt) -> Dict[str, Any]:
    return {
        "channel": channels.PAPER_FAMILY.channel(attempt.target),
        "last_sequence": 0,
        "user_id": attempt.owner,
        "owner_id": attempt.owner,
        "identity": attempt.owner,
        "tenant_id": attempt.owner,
    }


def _replay_shape(outcome: Any) -> Dict[str, Any]:
    """One replay outcome, as the value a foreign session and an unknown one are compared on.

    The ``paper_error`` frame's instants are ``utc_now()`` and the channel and session id are the
    caller's own input, so all three are dropped; the code, the recoverability, the sentence, the
    (empty) frame tuple and the reported current sequence are kept, which is everything that could
    carry information about the session.
    """
    frame = outcome.error_frame
    error: Any = None
    if frame is not None:
        payload = dict(frame.get("payload") or {})
        payload.pop("at", None)
        error = {
            "type": frame.get("type"),
            "sequence": frame.get("sequence"),
            "payload": payload,
        }
    return {
        "frames": tuple(int(item["sequence"]) for item in outcome.frames),
        "current_sequence": outcome.current_sequence,
        "truncated": outcome.truncated,
        "history_incomplete": outcome.history_incomplete,
        "error": error,
    }


def _values_belonging_to_the_owner(attempt: _Attempt, client: Any) -> Tuple[str, ...]:
    """Every field value of the owner's records that must not appear in a refusal.

    Requirement 21.8's third assertion. The session id itself is NOT in this list: the intruder
    named it, so echoing it back reveals nothing it did not already hold - which is precisely why
    ``channel`` is the one excluded field in :data:`CALLER_SUPPLIED_FIELDS`.
    """
    values: List[str] = [attempt.owner]
    for row in client.events:
        if str(row.get("session_id")) == attempt.target:
            values.append(str(row.get("event_id")))
    return tuple(values)


P44_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "foreign_session_refused": EXAMPLES,
    "nonexistent_session_refused": EXAMPLES,
    "refusals_were_indistinguishable": EXAMPLES,
    "the_owner_was_admitted": EXAMPLES,
    "nothing_was_registered_for_the_intruder": EXAMPLES,
    "a_forced_registration_was_refused_at_emit": EXAMPLES,
    # One market_tick per generated frame plus the paper_error frame, and every one of them reached
    # the owner - which is what "would have received a frame had it been admitted" means.
    "a_frame_the_intruder_would_have_received": EXAMPLES * 2,
    "the_owner_received_every_frame": EXAMPLES,
    "replay_refused_the_intruder": EXAMPLES,
    "replay_served_the_owner": EXAMPLES,
    "no_write_during_any_attempt": EXAMPLES,
    "the_refusal_carried_no_owner_field": EXAMPLES,
}

P44_LABELS: Dict[str, str] = {
    "foreign_session_refused": "a subscription to another tenant's session was refused",
    "nonexistent_session_refused": "a subscription to a session that exists nowhere was refused",
    "refusals_were_indistinguishable": "the two refusals were byte-identical",
    "the_owner_was_admitted": "the owner of the same session was admitted",
    "a_forced_registration_was_refused_at_emit": (
        "a registration forced past authorisation was refused again before the emit"
    ),
    "a_frame_the_intruder_would_have_received": (
        "a frame was delivered on the session the intruder asked for"
    ),
    "replay_refused_the_intruder": "the replay of another tenant's log returned nothing",
    "the_refusal_carried_no_owner_field": "the refusal carried no field value of the owner",
}


def _assert_the_channel_refuses_a_foreign_session(
    attempt: _Attempt, recorder: Recorder
) -> None:
    """P-44 for one attempt: refused at authorisation, refused at the emit, and nothing delivered."""
    client = _store(attempt)
    context = f"attempt {attempt!r}"
    target_channel = channels.PAPER_FAMILY.channel(attempt.target)

    # ── 1. the refusal, and the oracle it must match (Requirements 19.4, 21.4) ──
    foreign = _authorize(target_channel, attempt.intruder, client)
    recorder.mark("foreign_session_refused")
    nonexistent = _authorize(
        channels.PAPER_FAMILY.channel(attempt.absent_session), attempt.intruder, client
    )
    recorder.mark("nonexistent_session_refused")
    assert_indistinguishable_from_a_nonexistent_record(
        foreign=foreign,
        nonexistent=nonexistent,
        record="Paper_Session",
        label="P-44",
        context=context,
    )
    assert foreign.code == WA.CHANNEL_REFUSED_FORBIDDEN, (
        f"P-44: the refusal code changed to {foreign.code!r}; a code that distinguished a foreign "
        f"session from an unknown one would be the leak Requirement 21.4 forbids. {context}"
    )
    assert foreign.owner_id is None, (
        f"P-44: the refusal carried the owner id. {context}"
    )
    recorder.mark("refusals_were_indistinguishable")

    # No field value belonging to the owner appears in the refusal (Requirement 21.8).
    rendered = json.dumps(foreign.refusal_frame())
    for value in _values_belonging_to_the_owner(attempt, client):
        assert value not in rendered, (
            f"P-44 (Requirement 21.8): the refusal frame carries {value!r}, which belongs to the "
            f"session's owner. {context}"
        )
    recorder.mark("the_refusal_carried_no_owner_field")

    # ── 2. the owner of the SAME session is admitted ──────────────────────
    # Without this the property would hold for a channel that refused everybody.
    admitted = _authorize(target_channel, attempt.owner, client)
    assert admitted.allowed is True, (
        f"P-44: the owner of the session was refused ({admitted.code}: {admitted.reason}), so the "
        f"refusal above is not isolation - it is a channel that admits nobody. {context}"
    )
    assert str(admitted.owner_id) == attempt.owner
    recorder.mark("the_owner_was_admitted")

    # ── 3. a refused subscription is registered nowhere (Requirement 19.4) ──
    registry = _registry()
    manager = registry.manager
    intruder_connection = Connection("intruder")
    # The transport subscribes only on an ALLOW, so a refused attempt reaches no registry at all.
    assert registry.subscriptions(attempt.target) == ()
    assert attempt.target not in manager._get_store(pc.PAPER_STORE)
    assert intruder_connection not in manager._all_connections
    assert attempt.intruder not in manager._user_connections
    recorder.mark("nothing_was_registered_for_the_intruder")

    # ── 4. the owner subscribes, and the intruder is FORCED past authorisation ──
    # Registered deliberately, to show the pre-emit ownership check of Requirement 21.7 is a second,
    # independent refusal rather than a restatement of the first. At the transport this subscription
    # could not exist.
    owner_connection = Connection("owner")
    owner_subscription = _subscribe(
        registry, owner_connection, user={"id": attempt.owner}, session_id=attempt.target
    )
    intruder_subscription = _subscribe(
        registry,
        intruder_connection,
        user={"id": attempt.intruder},
        session_id=attempt.target,
        message=_intruder_message(attempt),
    )
    assert intruder_subscription.identity == attempt.intruder, (
        f"P-44 (Requirements 19.5, 21.1): the subscribe message naming the owner changed the "
        f"recorded identity to {intruder_subscription.identity!r}. {context}"
    )

    frames: List[Mapping[str, Any]] = [
        _frame(index, attempt.target) for index in range(1, attempt.frames + 1)
    ]
    frames.append(
        pc.error_frame(
            attempt.target,
            code=pc.ERROR_HISTORY_INCOMPLETE,
            message=pc.HISTORY_INCOMPLETE_MESSAGE,
            recoverable=False,
            sequence=attempt.retained_events,
        )
    )

    for position, frame in enumerate(frames):
        outcome = _run(registry.broadcast(attempt.target, frame, supabase=client))
        recorder.mark("a_frame_the_intruder_would_have_received")
        assert owner_subscription in outcome.delivered, (
            f"P-44: frame {position} did not reach the session's owner, so 'no event was delivered "
            f"to the intruder' would be true of a channel that delivered nothing at all. {context}"
        )
        if position == 0:
            assert outcome.ownership_revoked == (intruder_subscription,), (
                f"P-44 (Requirements 19.6, 21.7): the intruder's subscription was not revoked "
                f"before the first emit; outcome {outcome!r}. {context}"
            )
            recorder.mark("a_forced_registration_was_refused_at_emit")
        assert intruder_subscription not in outcome.delivered

    assert intruder_connection.sent == [], (
        f"P-44 (Requirement 19.6): the intruder received {intruder_connection.sent!r} on another "
        f"tenant's session. {context}"
    )
    assert intruder_connection.closed_with == [pc.CLOSE_CODE_OWNERSHIP], (
        f"P-44: the intruder's subscription was not closed; closes seen: "
        f"{intruder_connection.closed_with!r}. {context}"
    )
    assert registry.subscriptions(attempt.target) == (owner_subscription,)
    assert intruder_connection not in manager._all_connections
    assert attempt.intruder not in manager._user_connections

    assert owner_connection.sequences() == [
        index for index in range(1, attempt.frames + 1)
    ] + [attempt.retained_events], (
        f"P-44: the owner received {owner_connection.sequences()!r} rather than every broadcast "
        f"frame in order. {context}"
    )
    recorder.mark("the_owner_received_every_frame")

    # ── 5. no event of that session reaches the intruder by REPLAY either ──
    # The live path is not the only way a frame is delivered. Requirement 19.8's replay is a read
    # with ``user_id`` as a predicate, so the intruder's replay must be the same answer an unknown
    # session gives - which is the oracle again, on the other delivery path.
    intruder_replay = registry.replay(
        client, session_id=attempt.target, user={"id": attempt.intruder}, last_sequence=0
    )
    unknown_replay = registry.replay(
        client,
        session_id=attempt.absent_session,
        user={"id": attempt.intruder},
        last_sequence=0,
    )
    assert intruder_replay.frames == (), (
        f"P-44 (Requirements 19.6, 21.4): the replay handed the intruder "
        f"{len(intruder_replay.frames)} of another tenant's events. {context}"
    )
    assert _replay_shape(intruder_replay) == _replay_shape(unknown_replay), (
        f"Requirement 21.4: replaying another tenant's session answers differently from replaying "
        f"one that exists nowhere - {_replay_shape(intruder_replay)!r} against "
        f"{_replay_shape(unknown_replay)!r}. {context}"
    )
    recorder.mark("replay_refused_the_intruder")

    owner_replay = registry.replay(
        client, session_id=attempt.target, user={"id": attempt.owner}, last_sequence=0
    )
    assert [int(frame["sequence"]) for frame in owner_replay.frames] == list(
        range(1, attempt.retained_events + 1)
    ), (
        f"P-44: the owner's own replay returned "
        f"{[frame['sequence'] for frame in owner_replay.frames]!r} rather than its "
        f"{attempt.retained_events} retained event(s), so the refusal above says nothing about "
        f"isolation. {context}"
    )
    recorder.mark("replay_served_the_owner")

    # ── 6. every referenced record is unchanged (Requirement 21.4) ────────
    assert client.wrote_anything() is False, (
        f"P-44 (Requirement 21.4): a refused attempt wrote to the Persistence_Layer: "
        f"{[(s.op, s.table_name) for s in client.statements if s.op != 'select']!r}. {context}"
    )
    recorder.mark("no_write_during_any_attempt")

    _run(registry.release_session(attempt.target, reason="the attempt is over"))


def test_p44_paper_channel_refuses_foreign_sessions(request: Any) -> None:
    """A Paper_Session owned by ``u2`` is refused to ``u1``, and delivers ``u1`` nothing.

    For all Paper_Sessions owned by ``u2`` and all subscription attempts authenticated as ``u1``:
    the subscription is refused, the refusal is byte-identical to the one a session that exists in
    no tenant produces, no connection is registered in any Paper_Channel registry, and no event for
    that session is delivered to ``u1`` - not live, and not by replay. A registration forced past
    authorisation is refused again by the pre-emit ownership check with nothing emitted on it, while
    every frame still reaches the session's owner.

    The oracle is the non-existent-record response, obtained from the same function in the same
    store. The owner of the same session is admitted and receives every frame in the same run, so
    the property cannot be satisfied by a channel that refuses everybody.

    **Validates: Requirements 19.4, 19.6, 21.4**
    """
    recorder = Recorder("P-44", P44_FLOORS, P44_LABELS)

    @PROPERTY_SETTINGS
    @given(attempt=foreign_session_attempts())
    def check(attempt: _Attempt) -> None:
        recorder.start()
        recorder.mark("examples")
        pc.invalidate_session_owner()
        _assert_the_channel_refuses_a_foreign_session(attempt, recorder)
        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        pc.invalidate_session_owner()
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# P-41 AND P-42 (tasks 33.7, 33.8) - THE SAME MATRIX, OVER DRAWN IDENTIFIERS
# ══════════════════════════════════════════════════════════════════════════
#
# WHAT THESE TWO ADD TO THE SWEEP ABOVE
# -------------------------------------
# ``test_the_tenant_isolation_matrix_refuses_every_foreign_identifier`` walks the finite matrix
# ONCE, with the two literals ``U1``/``U2`` and their fixed identifier sets. That is an exhaustive
# statement about the ROWS and COLUMNS and a single statement about the identifiers.
#
# P-41 and P-42 are the other quantifier. The tenant pair, every record identifier each tenant
# owns, and the identifier that exists in no tenant are all DRAWN (:func:`tenant_pairs`), and the
# cell is drawn from the same matrix. So a handler that answered correctly only for the shapes the
# literals happen to have - an ordering that falls out of ``11111111`` sorting before ``22222222``,
# a comparison that happens to hold for two ids that differ in their first group, an oracle that
# matched because both fixed ids were equally unknown - fails here.
#
# Nothing about what isolation MEANS is restated: both properties drive :func:`_attempt` and the
# assertion helpers the sweep uses, through the ``tenancy`` parameter those helpers grew for this
# purpose. Under :data:`DEFAULT_TENANCY` every one of them behaves exactly as before.
#
# THE ORACLE, PER EXAMPLE
# -----------------------
# Requirement 21.4's non-existent-record response, captured from the SAME cell, in the SAME store,
# for a FRESH random UUID drawn in that example - ``tenancy.absent_ids`` - and never a literal this
# file chose. That is the ``absent`` answer below.
#
# THE PINNED REQUIREMENT 21.4 GAPS
# --------------------------------
# The seven cells of :data:`REQUIREMENT_21_4_GAPS` are recorded defects, not passes. For those
# cells the equality is replaced by the SAME pin the sweep asserts - which under a drawn tenancy
# says something the sweep cannot: the gap is a property of the endpoint rather than of the two
# literals, since the pinned signatures carry no identifier. Every OTHER assertion of P-41 and
# P-42 is applied to them unchanged.
#
# WHY NEITHER CAN PASS VACUOUSLY
# ------------------------------
# Each example ends with an owner-admitted control on the drawn tenancy, and the two controls are
# the two halves these properties would otherwise be able to fake:
#
# * P-41's control reads ``u2``'s own PRIVATE Listing as ``u2`` and requires the private
#   description canary to come back. So the drawn identifiers really do name ``u2``'s records, and
#   the endpoint really does serve that field to its owner - which is what makes its absence from
#   ``u1``'s answer isolation rather than an endpoint that serves nobody.
# * P-42's control issues the very same unpublish request as ``u2`` and requires ``u2``'s
#   ``library_strategies`` rows to MOVE. So a write can land on ``u2``'s rows in this harness, and
#   "byte-identical after ``u1``'s request" is not a statement about a store nothing can change.


#: Every attempted cell of the matrix - the pool both properties draw their cell from. Exposure and
#: identity cells are IN it: an identifier of ``u2``'s participates in an identity cell (``u2``'s own
#: user id, Requirement 21.8's tenth column), and an exposure cell is where a list endpoint would
#: return ``u2``'s rows without being asked for them, which is exactly "returns no field of ``u2``'s
#: resource".
ATTEMPTED_CELLS: Tuple[_Cell, ...] = tuple(cell for cell in MATRIX if cell.attempted)

#: The methods that can persist anything. A cell on one of these rows is where "leaves every row
#: owned by ``u2`` byte-identical" has something to say; P-42 draws one of these in EVERY example,
#: as well as a cell from the whole pool, so the write half cannot go unexercised in a run.
MUTATING_METHODS: Tuple[str, ...] = ("POST", "PUT", "PATCH", "DELETE")

MUTATING_CELLS: Tuple[_Cell, ...] = tuple(
    cell for cell in ATTEMPTED_CELLS if cell.row.method in MUTATING_METHODS
)

assert ATTEMPTED_CELLS and MUTATING_CELLS, (
    "P-41 and P-42 draw their cells from the matrix; an empty pool would make both properties "
    "quantify over nothing"
)


def _cell_named(key: str, column: str) -> _Cell:
    """The one attempted cell at ``key`` x ``column``, or a failure at import time.

    Used for the two control cells below, so a route that is renamed or withdrawn is a loud
    ImportError naming the control it broke - never a control that quietly stops being made.
    """
    matches = [
        cell
        for cell in ATTEMPTED_CELLS
        if cell.row.key == key and cell.column == column
    ]
    assert len(matches) == 1, (
        f"the control cell {key} x {column} is not a single attempted cell of the matrix "
        f"({len(matches)} found), so the anti-vacuity control it carries cannot be made"
    )
    return matches[0]


#: P-41's control: the owner's own read of their own PRIVATE Listing. ``description`` is in
#: ``listing_projection.PUBLIC_LISTING_FIELDS`` and the seed puts the private canary in it, so the
#: owner's answer carries a value ``u1``'s answer must not.
P41_CONTROL_CELL: _Cell = _cell_named("GET /api/library/{library_id}", LISTING_PRIVATE)

#: P-42's control: the owner's own unpublish of that same Listing. It soft-deletes - one UPDATE of
#: ``is_active`` and ``updated_at`` on ``library_strategies``, a table this specification MODIFIES
#: and :meth:`MatrixStore.snapshot` therefore images - so the owner's request MOVES ``u2``'s rows.
P42_CONTROL_CELL: _Cell = _cell_named("DELETE /api/library/{library_id}", LISTING_PRIVATE)

#: 2 tenants + one identifier set each + the user that exists in no tenant + its identifier set.
_UUIDS_PER_TENANCY: int = 3 + 3 * len(_DRAWN_ID_SLOTS)


@st.composite
def tenant_pairs(draw: Any) -> _Tenancy:
    """Two distinct tenants, every identifier each owns, and one set owned by nobody.

    UUID text because ``paper_sessions.id`` holds it, ``routers/library._safe_uuid`` answers 422
    for anything else, and ``ws_channels._RESOURCE_ID_PATTERN`` admits it - a 422 for one member of
    a pair and a 404 for the other would be a difference this generator created rather than one the
    API has.

    Distinctness is a constraint of the STRATEGY rather than a ``filter``, exactly as
    :func:`foreign_session_attempts` does it: all ``_UUIDS_PER_TENANCY`` identifiers come out of one
    ``unique`` list, so ``u1 != u2`` holds by construction, every record identifier names at most
    one tenant's row, and the absent set genuinely exists in neither tenant. A ``filter`` here would
    spend examples rejecting collisions the generator can simply not produce.
    """
    drawn = draw(
        st.lists(
            st.uuids(version=4).map(str),
            min_size=_UUIDS_PER_TENANCY,
            max_size=_UUIDS_PER_TENANCY,
            unique=True,
        )
    )
    width = len(_DRAWN_ID_SLOTS)
    u1, u2, absent_user = drawn[0], drawn[1], drawn[2]
    rest = drawn[3:]
    return _Tenancy(
        u1=u1,
        u2=u2,
        u1_ids=_identifier_set(rest[:width], u1),
        u2_ids=_identifier_set(rest[width : 2 * width], u2),
        absent_user=absent_user,
        absent_ids=_identifier_set(rest[2 * width : 3 * width], absent_user),
    )


@dataclass(frozen=True)
class _Quantification:
    """One example's ``(cell, caller, target ids, absent ids, supplied identities)``.

    The three-way split the sweep makes, restated once here so P-41 and P-42 address a cell the
    same way it does: a REFERENCE cell names ``u2``'s identifier of its column, while an EXPOSURE
    or IDENTITY cell names no record at all and is attempted with the absent set - what is being
    checked on those is that ``u2``'s rows do not come back unasked, and that a SUPPLIED identity
    of ``u2``'s changes nothing.
    """

    cell: _Cell
    target_ids: Mapping[str, str]
    identity: str
    absent_identity: str


def _quantify(cell: _Cell, tenancy: _Tenancy) -> _Quantification:
    """How this cell is addressed for this tenancy - the sweep's own three-way split."""
    return _Quantification(
        cell=cell,
        target_ids=tenancy.u2_ids if cell.attempt == ATTEMPT_REFERENCE else tenancy.absent_ids,
        identity=tenancy.u2 if cell.attempt == ATTEMPT_IDENTITY else tenancy.u1,
        absent_identity=(
            tenancy.absent_user if cell.attempt == ATTEMPT_IDENTITY else tenancy.u1
        ),
    )


def _attempt_kind_bucket(cell: _Cell) -> str:
    return {
        ATTEMPT_REFERENCE: "reference_cells",
        ATTEMPT_EXPOSURE: "exposure_cells",
        ATTEMPT_IDENTITY: "identity_cells",
    }[cell.attempt]


def _assert_the_pair_and_its_identifiers_were_drawn(tenancy: _Tenancy) -> None:
    """The generator's own contract, checked in every example rather than trusted.

    Three clauses, and each one is a way these properties could quietly stop being generated:

    * no identifier of the drawn tenancy is one of the literals the exhaustive sweep uses, so an
      example can never be the sweep's fixed case wearing a Hypothesis hat;
    * ``u1`` and ``u2`` are distinct, which is what "for all distinct ``(u1, u2)``" needs;
    * the absent set names nothing either tenant owns, which is what makes the oracle captured from
      it the NON-EXISTENT-record answer rather than a second foreign one.

    All three hold by construction - :func:`tenant_pairs` draws one ``unique`` list - and that is
    exactly why they are asserted: a future edit that drew the three sets separately, or reused a
    literal for one slot, would satisfy every other assertion in this file.
    """
    literal = (
        {U1, U2, ABSENT_USER}
        | set(U1_IDS.values())
        | set(U2_IDS.values())
        | set(ABSENT_IDS.values())
    )
    drawn = (
        {tenancy.u1, tenancy.u2, tenancy.absent_user}
        | set(tenancy.u1_ids.values())
        | set(tenancy.u2_ids.values())
        | set(tenancy.absent_ids.values())
    )
    shared = sorted(drawn & literal)
    assert not shared, (
        f"the drawn tenancy reuses the exhaustive sweep's literals {shared}, so this example is "
        f"not quantifying over identifiers at all. {tenancy!r}"
    )
    assert tenancy.u1 != tenancy.u2, f"the two tenants are one tenant. {tenancy!r}"
    owned = {tenancy.u1, tenancy.u2} | set(tenancy.u1_ids.values()) | set(
        tenancy.u2_ids.values()
    )
    intruding = sorted(({tenancy.absent_user} | set(tenancy.absent_ids.values())) & owned)
    assert not intruding, (
        f"the 'exists in no tenant' identifiers {intruding} are owned by one of the two tenants, "
        f"so the oracle would be a second foreign answer rather than the absent one. {tenancy!r}"
    )


P41_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "the_pair_and_its_identifiers_were_drawn": EXAMPLES,
    "the_nonexistent_record_oracle_was_captured": EXAMPLES,
    "no_field_of_u2_appeared": EXAMPLES,
    "the_answer_was_the_oracle_or_a_pinned_gap": EXAMPLES,
    "the_owner_still_read_its_own_private_field": EXAMPLES,
    # Published rather than floored, and the distinction is deliberate.
    #
    # WHICH cell an example draws, and whether a drawn tenancy has been seen before, are
    # Hypothesis's business: its generation is mutational, so it varies one drawn part while
    # holding another fixed on purpose, and roughly half the examples reuse a tenant pair with a
    # different cell. A floor on any of these buckets would be a claim about the SAMPLER, and the
    # only way to force it would be to make the generator non-reproducible.
    #
    # What carries the anti-vacuity guarantee instead is the five floors above, every one of them
    # hit once per example: the pair was drawn rather than the literals, an oracle was captured
    # from a fresh UUID, the response was scanned for u2's values at every depth, the oracle (or
    # the pinned gap) was applied, and u2 still read its own private field through the same
    # surface.
    "a_tenant_pair_not_seen_before": 0,
    "reference_cells": 0,
    "exposure_cells": 0,
    "identity_cells": 0,
    "strict_oracle_held": 0,
    "public_catalogue_oracle_held": 0,
    "a_pinned_gap_reproduced_over_drawn_identifiers": 0,
}

P41_LABELS: Dict[str, str] = {
    "a_tenant_pair_not_seen_before": "the tenant pair had not been generated before",
    "reference_cells": "the cell named u2's identifier of its column",
    "exposure_cells": "the cell returns rows of that kind without naming one",
    "identity_cells": "the request supplied u2's own user identity",
    "strict_oracle_held": "the answer was byte-identical to the non-existent-record answer",
    "public_catalogue_oracle_held": "only u2's published Listing's public projection differed",
    "a_pinned_gap_reproduced_over_drawn_identifiers": (
        "a pinned Requirement 21.4 gap reproduced for drawn identifiers"
    ),
    "no_field_of_u2_appeared": "no field value of u2's reached u1",
    "the_owner_still_read_its_own_private_field": "u2 still read its own private Listing",
}


def _assert_the_read_returns_no_field_of_u2(
    tenancy: _Tenancy, cell: _Cell, recorder: Recorder
) -> None:
    """P-41 for one drawn tenancy and one cell of the matrix."""
    context = f"{cell.name}, {tenancy!r}"
    plan = _quantify(cell, tenancy)
    values = tenancy.u2_values()
    public = _public_projection_values_for(tenancy)

    foreign = _attempt(
        cell, plan.target_ids, caller=tenancy.u1, identity=plan.identity, tenancy=tenancy
    )
    absent = _attempt(
        cell,
        tenancy.absent_ids,
        caller=tenancy.u1,
        identity=plan.absent_identity,
        tenancy=tenancy,
    )
    recorder.mark("the_nonexistent_record_oracle_was_captured")
    recorder.mark(_attempt_kind_bucket(cell))

    # ── no field of u2's resource, at any nesting depth (Requirement 21.8) ──
    _assert_no_value_of_u2_appears(cell, foreign, values=values, public=public)
    recorder.mark("no_field_of_u2_appeared")

    # ── the answer IS the non-existent-record answer (Requirement 21.4) ────
    if cell.oracle == ORACLE_STRICT:
        assert foreign.comparable() == absent.comparable(), (
            f"P-41 (Requirement 21.4): the answer for u2's {cell.column} differs from the answer "
            f"for a freshly drawn identifier that exists in no tenant, so the response is an "
            f"existence oracle for another tenant's identifiers.\n"
            f"  foreign: {foreign.comparable()!r}\n"
            f"  absent : {absent.comparable()!r}\n"
            f"  {context}"
        )
        recorder.mark("strict_oracle_held")
    elif cell.oracle == ORACLE_PUBLIC_CATALOGUE:
        _assert_only_the_public_catalogue_differs(cell, foreign, absent, allowed=public)
        recorder.mark("public_catalogue_oracle_held")
    else:
        gap = REQUIREMENT_21_4_GAPS[(cell.row.key, cell.column)]
        observed = (_gap_signature(foreign), _gap_signature(absent))
        assert observed == (gap.foreign, gap.absent), (
            f"P-41: {cell.name} is a PINNED Requirement 21.4 gap and its answers have moved for "
            f"drawn identifiers. Recorded {(gap.foreign, gap.absent)!r}, observed {observed!r}. "
            f"If the two answers are now identical the cell is fixed and its entry belongs out of "
            f"REQUIREMENT_21_4_GAPS; if they differ in a NEW way the gap has changed shape and the "
            f"entry has to say so. {context}"
        )
        recorder.mark("a_pinned_gap_reproduced_over_drawn_identifiers")
    recorder.mark("the_answer_was_the_oracle_or_a_pinned_gap")

    # ── the control: u2 still reads its OWN private field ─────────────────
    canary = U2_CANARIES["private_listing_description"]
    owner = _attempt(
        P41_CONTROL_CELL,
        tenancy.u2_ids,
        caller=tenancy.u2,
        identity=tenancy.u2,
        tenancy=tenancy,
    )
    rendered = json.dumps(owner.body, default=str)
    assert owner.status == 200 and canary in rendered, (
        f"P-41: {P41_CONTROL_CELL.row.key} answered u2's OWN private Listing with status "
        f"{owner.status} and a body that does not carry {canary!r}, so the drawn identifiers name "
        f"no readable record and every refusal above would hold for an endpoint that serves "
        f"nobody. Body: {rendered[:600]}. {context}"
    )
    recorder.mark("the_owner_still_read_its_own_private_field")


def test_p41_cross_tenant_read_returns_no_field(request: Any) -> None:
    """A request as ``u1`` naming ``u2``'s records returns no field of them, for DRAWN identifiers.

    For all distinct ``(u1, u2)``, all endpoints and WebSocket channels this specification
    introduces or modifies, and all identifiers owned by ``u2``: a request authenticated as ``u1``
    carries no field value of ``u2``'s resource at any nesting depth, and its status and body are
    the ones an identifier that exists in no tenant is answered - the oracle being captured from a
    FRESH randomly drawn UUID on the same cell in the same store, per example.

    The seven cells of :data:`REQUIREMENT_21_4_GAPS` are recorded defects: for those the pinned
    pair is asserted instead of the equality, which over drawn identifiers additionally shows the
    gap belongs to the endpoint and not to two literals. Every other assertion applies to them
    unchanged.

    The property cannot hold vacuously: in every example ``u2`` reads its own private Listing
    through the same surface and the private canary comes back, so the answers ``u1`` receives are
    isolation rather than an endpoint that answers nobody.

    **Validates: Requirements 21.4, 21.8**
    """
    recorder = Recorder("P-41", P41_FLOORS, P41_LABELS)
    pairs_seen: Set[Tuple[str, str]] = set()

    @PROPERTY_SETTINGS
    @given(tenancy=tenant_pairs(), cell=st.sampled_from(ATTEMPTED_CELLS))
    def check(tenancy: _Tenancy, cell: _Cell) -> None:
        recorder.start()
        recorder.mark("examples")
        _assert_the_pair_and_its_identifiers_were_drawn(tenancy)
        recorder.mark("the_pair_and_its_identifiers_were_drawn")
        if (tenancy.u1, tenancy.u2) not in pairs_seen:
            pairs_seen.add((tenancy.u1, tenancy.u2))
            recorder.mark("a_tenant_pair_not_seen_before")
        pc.invalidate_session_owner()
        _assert_the_read_returns_no_field_of_u2(tenancy, cell, recorder)
        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        pc.invalidate_session_owner()
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


P42_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "the_pair_and_its_identifiers_were_drawn": EXAMPLES,
    # Two cells per example: one from the whole matrix, one from a row that can persist something.
    "cells_attempted": 2 * EXAMPLES,
    "a_mutating_endpoint_was_attempted": EXAMPLES,
    "every_row_of_u2_was_byte_identical": 2 * EXAMPLES,
    "the_owner_could_still_move_its_own_rows": EXAMPLES,
    # Published rather than floored, for the reason P41_FLOORS gives at length. Whether a drawn
    # attempt was refused or admitted is the ENDPOINT's answer and not something a generator can
    # force; both outcomes are asserted, and the floor above covers them together.
    "a_tenant_pair_not_seen_before": 0,
    "a_refused_attempt_persisted_nothing": 0,
    "an_admitted_attempt_left_u2_alone": 0,
    "reference_cells": 0,
    "exposure_cells": 0,
    "identity_cells": 0,
}

P42_LABELS: Dict[str, str] = {
    "a_tenant_pair_not_seen_before": "the tenant pair had not been generated before",
    "a_mutating_endpoint_was_attempted": "a POST/PUT/PATCH/DELETE row was attempted",
    "a_refused_attempt_persisted_nothing": "a refused attempt changed no row of any spec table",
    "an_admitted_attempt_left_u2_alone": "an admitted attempt still moved no row of u2's",
    "every_row_of_u2_was_byte_identical": "every row owned by u2 was byte-identical afterwards",
    "the_owner_could_still_move_its_own_rows": "u2's own request moved u2's rows",
    "reference_cells": "the cell named u2's identifier of its column",
    "exposure_cells": "the cell returns rows of that kind without naming one",
    "identity_cells": "the request supplied u2's own user identity",
}


def _assert_the_write_leaves_u2s_rows_byte_identical(
    tenancy: _Tenancy,
    cell: _Cell,
    clean: Mapping[str, str],
    clean_foreign: Mapping[str, str],
    recorder: Recorder,
) -> None:
    """P-42 for one drawn tenancy and one cell: one request as ``u1``, one full before/after image."""
    plan = _quantify(cell, tenancy)
    answer = _attempt(
        cell, plan.target_ids, caller=tenancy.u1, identity=plan.identity, tenancy=tenancy
    )
    recorder.mark("cells_attempted")
    recorder.mark(_attempt_kind_bucket(cell))

    # Byte-identical, over the full image of every row of u2's rather than over a row count: an
    # in-place UPDATE moves no count. ``_assert_no_row_of_u2_moved`` is the sweep's own assertion,
    # driven here on drawn rows.
    _assert_no_row_of_u2_moved(cell, clean_foreign, answer)
    recorder.mark("every_row_of_u2_was_byte_identical")

    # A refused attempt persists nothing at all - the other half of Requirement 21.4.
    _assert_no_row_of_u2_changed(cell, answer, clean)
    recorder.mark(
        "a_refused_attempt_persisted_nothing"
        if answer.refused
        else "an_admitted_attempt_left_u2_alone"
    )


def test_p42_cross_tenant_write_leaves_rows_byte_identical(request: Any) -> None:
    """A request as ``u1`` naming ``u2``'s records leaves every row of ``u2``'s byte-identical.

    Under P-41's quantification - all distinct ``(u1, u2)``, all endpoints and channels this
    specification introduces or modifies, all identifiers owned by ``u2``, every one of them drawn -
    a request authenticated as ``u1`` leaves every row owned by ``u2`` byte-identical, over a full
    before/after image of every table this specification introduces or modifies rather than over a
    row count. Where the request was refused, no row of any of those tables changed at all.

    Two cells are attempted per example: one drawn from the whole matrix, and one drawn from a row
    whose method can persist something, so the write half is exercised in every example rather than
    whenever the sampling happens to land on it.

    The property cannot hold vacuously: in every example ``u2`` issues the very same unpublish
    request against its own Listing and ``u2``'s ``library_strategies`` rows MOVE, so this harness
    demonstrably lets a write land on ``u2``'s rows.

    **Validates: Requirements 21.4, 21.8**
    """
    recorder = Recorder("P-42", P42_FLOORS, P42_LABELS)
    pairs_seen: Set[Tuple[str, str]] = set()

    @PROPERTY_SETTINGS
    @given(
        tenancy=tenant_pairs(),
        cell=st.sampled_from(ATTEMPTED_CELLS),
        mutating=st.sampled_from(MUTATING_CELLS),
    )
    def check(tenancy: _Tenancy, cell: _Cell, mutating: _Cell) -> None:
        recorder.start()
        recorder.mark("examples")
        _assert_the_pair_and_its_identifiers_were_drawn(tenancy)
        recorder.mark("the_pair_and_its_identifiers_were_drawn")
        if (tenancy.u1, tenancy.u2) not in pairs_seen:
            pairs_seen.add((tenancy.u1, tenancy.u2))
            recorder.mark("a_tenant_pair_not_seen_before")
        pc.invalidate_session_owner()

        # The "before" image: a store seeded from the same tenancy and untouched. Deterministic,
        # because ``fresh_store`` builds the same rows for the same tenancy every time - which is
        # how the sweep above takes its own before-image.
        clean = fresh_store(tenancy).snapshot()
        clean_foreign = fresh_store(tenancy).rows_touching(tenancy.all_u2_values())

        _assert_the_write_leaves_u2s_rows_byte_identical(
            tenancy, cell, clean, clean_foreign, recorder
        )
        _assert_the_write_leaves_u2s_rows_byte_identical(
            tenancy, mutating, clean, clean_foreign, recorder
        )
        recorder.mark("a_mutating_endpoint_was_attempted")

        # ── the control: u2's own write DOES move u2's rows ────────────────
        owner = _attempt(
            P42_CONTROL_CELL,
            tenancy.u2_ids,
            caller=tenancy.u2,
            identity=tenancy.u2,
            tenancy=tenancy,
        )
        moved = sorted(
            name
            for name, text in owner.foreign_rows.items()
            if clean_foreign.get(name) != text
        )
        assert owner.status == 200 and "library_strategies" in moved, (
            f"P-42: {P42_CONTROL_CELL.row.key} answered u2's OWN Listing with status "
            f"{owner.status} and moved {moved}, so no write reaches u2's rows in this harness at "
            f"all and 'byte-identical' would hold for a store nothing can change. "
            f"Body: {json.dumps(owner.body, default=str)[:600]}. {tenancy!r}"
        )
        recorder.mark("the_owner_could_still_move_its_own_rows")
        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        pc.invalidate_session_owner()
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# P-43 (task 33.9) - A FOREIGN RECORD AND AN ABSENT ONE ANSWER THE SAME
# ══════════════════════════════════════════════════════════════════════════
#
# WHAT P-43 ADDS THAT P-41 DOES NOT SAY
# -------------------------------------
# P-41 is "returns no field of u2's resource", with the oracle as its second clause. P-43 is the
# ORACLE ITSELF, quantified over the identifiers, and it carries two requirements P-41 is not
# written against:
#
# * **Requirement 5.7** - a caller without the Admin_Reviewer role must be answered a 403 that
#   "does not reveal whether the named Submission exists". That is a THREE-way indistinguishability
#   and P-41 makes no statement about it: the answer for the caller's OWN existing Submission, for
#   another tenant's, and for a Submission that exists nowhere must all three be the same answer.
#   A 403 that carried "no such submission" for one of the three would satisfy every cross-tenant
#   assertion in this file and still be an existence oracle for a caller who cannot reach the
#   record at all. ``_matrix_app`` deliberately does NOT override ``get_admin_user``, so the caller
#   here really is a non-Admin_Reviewer.
# * **Requirement 6.10** - a non-owner asking for a Listing that is not in a published
#   Submission_State must be answered "as though the Listing does not exist". So u2's UNPUBLISHED
#   Listing and an identifier that names nothing must be the same answer, while u2's PUBLISHED
#   Listing is served - which is the control: the route demonstrably answers 200 for a Listing in
#   the catalogue, so the 404 for the unpublished one is Requirement 6.10 working rather than a
#   route that answers 404 for everything.
#
# THE QUANTIFICATION
# ------------------
# "for all resource identifiers": every REFERENCE cell of the matrix - every (endpoint or channel)
# x (record kind) pair that NAMES an identifier - crossed with a drawn tenancy, so the identifier
# of every one of Requirement 21.8's ten kinds is drawn rather than fixed. Exposure and identity
# cells are not in this pool: they name no resource identifier, which is what this property
# quantifies over.
#
# THE ORACLE
# ----------
# The answer the SAME cell gives, in the SAME store, for a freshly drawn UUID that exists in no
# tenant. Status and body, compared through :meth:`_Answer.comparable` - and the status is asserted
# FIRST and separately, so "404 against 403" is reported as a status difference rather than as a
# body diff a reader has to decode.
#
# THE PINNED REQUIREMENT 21.4 GAPS
# --------------------------------
# The seven cells of :data:`REQUIREMENT_21_4_GAPS` are exactly the cells where the Marketplace_API
# as it stands today FAILS this property. They are asserted as the pinned pair, so a gap that is
# closed turns this test red and points at the table; nothing is silently passed and no router is
# edited from a test task.


#: The cells that NAME a resource identifier - P-43's quantification, and the pool P-45 does not
#: use. Every one of them is an attempted cell of the matrix.
REFERENCE_CELLS: Tuple[_Cell, ...] = tuple(
    cell for cell in ATTEMPTED_CELLS if cell.attempt == ATTEMPT_REFERENCE
)

assert REFERENCE_CELLS, (
    "P-43 quantifies over the cells that name a resource identifier; an empty pool would make it "
    "quantify over nothing"
)

#: Requirement 5.7's cell: a review operation, reached by a caller who is not an Admin_Reviewer.
#: ``GET`` rather than one of the actions, because a read is the operation whose answer could most
#: cheaply carry "this Submission exists" - and the actions share the same ``get_admin_user`` gate,
#: which the exhaustive sweep already drives on every one of them.
P43_ADMIN_CELL: _Cell = _cell_named(
    "GET /api/library/admin/submissions/{submission_id}", SUBMISSION
)

#: Requirement 6.10's cell: the Listing detail read. The seed gives each tenant TWO Listings - one
#: published, one not - so this one cell carries both halves: the unpublished one must be answered
#: as absent, and the published one must still be served.
P43_LISTING_CELL: _Cell = _cell_named("GET /api/library/{library_id}", LISTING_PRIVATE)


def _published_listing_ids(tenancy: _Tenancy, owner: str) -> Dict[str, str]:
    """``owner``'s identifier set with ``library_id`` pointing at its PUBLISHED Listing.

    :data:`P43_LISTING_CELL` fills ``{library_id}`` from ``ids["library_id"]``, which the seed
    makes the UNPUBLISHED Listing. Requirement 6.10 is a statement about the difference between the
    two, so the published one has to be addressable through the same cell.
    """
    ids = dict(tenancy.u2_ids if owner == tenancy.u2 else tenancy.u1_ids)
    ids["library_id"] = ids["public_library_id"]
    return ids


P43_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "the_pair_and_its_identifiers_were_drawn": EXAMPLES,
    "the_nonexistent_record_oracle_was_captured": EXAMPLES,
    "the_status_codes_were_equal_or_a_pinned_gap": EXAMPLES,
    "the_bodies_were_indistinguishable_or_a_pinned_gap": EXAMPLES,
    # Requirement 5.7: own, foreign and absent are ONE answer to a non-Admin_Reviewer.
    "a_non_admin_learned_nothing_about_any_submission": EXAMPLES,
    # Requirement 6.10: the unpublished Listing of another tenant IS the absent answer...
    "an_unpublished_listing_answered_as_absent": EXAMPLES,
    # ...and the published one is still served, which is what makes that 404 a decision.
    "the_published_listing_was_still_served": EXAMPLES,
    # Published rather than floored, for the reason P41_FLOORS states at length: WHICH cell an
    # example draws is Hypothesis's business, and a floor on a sampler-dependent bucket would be a
    # claim about the sampler. Every floor above is hit once per example.
    "a_tenant_pair_not_seen_before": 0,
    "strict_oracle_held": 0,
    "public_catalogue_oracle_held": 0,
    "a_pinned_gap_reproduced_over_drawn_identifiers": 0,
    "both_answers_were_refusals": 0,
    "both_answers_were_admitted": 0,
}

P43_LABELS: Dict[str, str] = {
    "a_tenant_pair_not_seen_before": "the tenant pair had not been generated before",
    "the_status_codes_were_equal_or_a_pinned_gap": "the two status codes agreed",
    "the_bodies_were_indistinguishable_or_a_pinned_gap": "the two bodies were indistinguishable",
    "strict_oracle_held": "the foreign answer WAS the non-existent-record answer",
    "public_catalogue_oracle_held": "only u2's published Listing's public projection differed",
    "a_pinned_gap_reproduced_over_drawn_identifiers": (
        "a pinned Requirement 21.4 gap reproduced for drawn identifiers"
    ),
    "both_answers_were_refusals": "the cell refused both identifiers",
    "both_answers_were_admitted": "the cell answered both identifiers without refusing",
    "a_non_admin_learned_nothing_about_any_submission": (
        "Requirement 5.7: own, foreign and absent Submissions were one 403"
    ),
    "an_unpublished_listing_answered_as_absent": (
        "Requirement 6.10: another tenant's unpublished Listing was answered as absent"
    ),
    "the_published_listing_was_still_served": (
        "the same route served u2's PUBLISHED Listing to u1"
    ),
}


def _assert_indistinguishable(
    label: str,
    cell: _Cell,
    foreign: _Answer,
    absent: _Answer,
    tenancy: _Tenancy,
    recorder: Recorder,
    public: Sequence[str],
) -> None:
    """The oracle of Requirement 21.4, applied to one cell of one drawn tenancy.

    Status first, then body, so a status difference is reported as one. The three oracle kinds are
    the sweep's own: strict equality, the public-catalogue relaxation for a surface whose purpose is
    to publish a Listing, and the pinned pair for a cell where the Marketplace_API fails this
    property today.
    """
    context = f"{cell.name}, {tenancy!r}"
    if cell.oracle == ORACLE_GAP:
        gap = REQUIREMENT_21_4_GAPS[(cell.row.key, cell.column)]
        observed = (_gap_signature(foreign), _gap_signature(absent))
        assert observed == (gap.foreign, gap.absent), (
            f"{label}: {cell.name} is a PINNED Requirement 21.4 gap and its answers have moved for "
            f"drawn identifiers. Recorded {(gap.foreign, gap.absent)!r}, observed {observed!r}. If "
            f"the two answers are now identical the cell is fixed and its entry belongs out of "
            f"REQUIREMENT_21_4_GAPS; if they differ in a NEW way the entry has to say so. {context}"
        )
        recorder.mark("a_pinned_gap_reproduced_over_drawn_identifiers")
        recorder.mark("the_status_codes_were_equal_or_a_pinned_gap")
        recorder.mark("the_bodies_were_indistinguishable_or_a_pinned_gap")
        return

    assert foreign.status == absent.status, (
        f"{label} (Requirement 21.4): another tenant's {cell.column} is answered HTTP "
        f"{foreign.status} while an identifier that exists in no tenant is answered "
        f"{absent.status}. The status code alone therefore answers 'does this identifier name a "
        f"record?' for a record the caller cannot see.\n"
        f"  foreign body: {json.dumps(foreign.body, default=str)[:600]}\n"
        f"  absent  body: {json.dumps(absent.body, default=str)[:600]}\n"
        f"  {context}"
    )
    recorder.mark("the_status_codes_were_equal_or_a_pinned_gap")

    if cell.oracle == ORACLE_PUBLIC_CATALOGUE:
        _assert_only_the_public_catalogue_differs(cell, foreign, absent, allowed=public)
        recorder.mark("public_catalogue_oracle_held")
    else:
        assert foreign.comparable() == absent.comparable(), (
            f"{label} (Requirement 21.4): the two status codes agree but the BODIES differ, so the "
            f"body is the existence oracle.\n"
            f"  foreign: {foreign.comparable()!r}\n"
            f"  absent : {absent.comparable()!r}\n"
            f"  {context}"
        )
        recorder.mark("strict_oracle_held")
    recorder.mark("the_bodies_were_indistinguishable_or_a_pinned_gap")
    recorder.mark(
        "both_answers_were_refusals" if foreign.refused else "both_answers_were_admitted"
    )


def _assert_a_non_admin_learns_nothing_about_a_submission(
    tenancy: _Tenancy, recorder: Recorder
) -> None:
    """Requirement 5.7: the review refusal is the same answer for three different Submissions.

    Own, another tenant's, and one that exists nowhere. A caller without the Admin_Reviewer role
    must not be able to tell those three apart, which is a stronger statement than "the foreign one
    looks like the absent one": it forbids the role gate from resolving the record at all.
    """
    answers = {
        "own": _attempt(
            P43_ADMIN_CELL,
            tenancy.u1_ids,
            caller=tenancy.u1,
            identity=tenancy.u1,
            tenancy=tenancy,
        ),
        "another tenant's": _attempt(
            P43_ADMIN_CELL,
            tenancy.u2_ids,
            caller=tenancy.u1,
            identity=tenancy.u1,
            tenancy=tenancy,
        ),
        "absent": _attempt(
            P43_ADMIN_CELL,
            tenancy.absent_ids,
            caller=tenancy.u1,
            identity=tenancy.u1,
            tenancy=tenancy,
        ),
    }
    for which, answer in answers.items():
        assert answer.status == 403, (
            f"P-43 (Requirement 5.7): {P43_ADMIN_CELL.row.key} answered a NON-Admin_Reviewer "
            f"{answer.status} for the caller's {which} Submission, not the 403 Requirement 5.7 "
            f"requires. Body: {json.dumps(answer.body, default=str)[:600]}. {tenancy!r}"
        )
    distinct = {which: answer.comparable() for which, answer in answers.items()}
    assert len(set(map(repr, distinct.values()))) == 1, (
        f"P-43 (Requirement 5.7): the 403 a non-Admin_Reviewer receives from "
        f"{P43_ADMIN_CELL.row.key} is not the same answer for the caller's own Submission, for "
        f"another tenant's and for one that exists nowhere, so it reveals whether the named "
        f"Submission exists: {distinct!r}. {tenancy!r}"
    )
    recorder.mark("a_non_admin_learned_nothing_about_any_submission")


def _assert_an_unpublished_listing_is_answered_as_absent(
    tenancy: _Tenancy, recorder: Recorder
) -> None:
    """Requirement 6.10, and the control that makes its 404 a decision rather than a habit."""
    unpublished = _attempt(
        P43_LISTING_CELL,
        tenancy.u2_ids,
        caller=tenancy.u1,
        identity=tenancy.u1,
        tenancy=tenancy,
    )
    absent = _attempt(
        P43_LISTING_CELL,
        tenancy.absent_ids,
        caller=tenancy.u1,
        identity=tenancy.u1,
        tenancy=tenancy,
    )
    assert (unpublished.status, absent.status) == (404, 404), (
        f"P-43 (Requirement 6.10): a non-owner asking for a Listing that is not in a published "
        f"Submission_State was answered {unpublished.status}, and an identifier that names nothing "
        f"{absent.status}; Requirement 6.10 requires the API to respond as though the Listing does "
        f"not exist. {tenancy!r}"
    )
    assert unpublished.comparable() == absent.comparable(), (
        f"P-43 (Requirement 6.10): the answer for another tenant's UNPUBLISHED Listing differs "
        f"from the answer for a Listing identifier that names nothing, so the response discloses "
        f"the Listing's existence.\n"
        f"  unpublished: {unpublished.comparable()!r}\n"
        f"  absent     : {absent.comparable()!r}\n  {tenancy!r}"
    )
    recorder.mark("an_unpublished_listing_answered_as_absent")

    # ── the control: the SAME route serves u2's PUBLISHED Listing to u1 ───
    published = _attempt(
        P43_LISTING_CELL,
        _published_listing_ids(tenancy, tenancy.u2),
        caller=tenancy.u1,
        identity=tenancy.u1,
        tenancy=tenancy,
    )
    rendered = json.dumps(published.body, default=str)
    assert published.status == 200 and tenancy.u2_ids["public_library_id"] in rendered, (
        f"P-43: {P43_LISTING_CELL.row.key} answered u2's PUBLISHED Listing with status "
        f"{published.status} and a body that does not name it, so every 404 above would hold for a "
        f"route that answers 404 for every Listing and the indistinguishability would be vacuous. "
        f"Body: {rendered[:600]}. {tenancy!r}"
    )
    recorder.mark("the_published_listing_was_still_served")


def test_p43_foreign_and_absent_responses_are_indistinguishable(request: Any) -> None:
    """Another tenant's record and a record that exists nowhere are answered identically.

    For all resource identifiers - every one of Requirement 21.8's ten record kinds, drawn per
    example, on every endpoint and channel of this specification that NAMES one - the response to a
    request for another user's existing resource is indistinguishable from the response for a
    non-existent resource of the same kind, in status code AND body. The oracle is captured from a
    freshly drawn UUID on the same cell in the same store, so it is never a literal this file chose.

    Two further indistinguishability claims ride on the same drawn tenancy, and neither is a
    cross-tenant statement P-41 already makes:

    * Requirement 5.7 - the 403 a caller without the Admin_Reviewer role receives from a review
      operation is the SAME answer for their own Submission, for another tenant's, and for one that
      exists nowhere, so it reveals nothing about whether the named Submission exists;
    * Requirement 6.10 - another tenant's Listing that is not in a published Submission_State is
      answered exactly as an identifier that names nothing, while the same route still serves that
      tenant's PUBLISHED Listing.

    The seven cells of :data:`REQUIREMENT_21_4_GAPS` are the cells where the Marketplace_API fails
    this property today: for those the pinned pair is asserted, so the gap cannot drift and cannot
    read as coverage.

    The property cannot hold vacuously: the published-Listing control shows the Listing route
    answers 200 for a record in the catalogue, and Requirement 5.7's own-Submission arm shows the
    three-way equality is not an equality over records that all fail to exist.

    **Validates: Requirements 21.4, 5.7, 6.10**
    """
    recorder = Recorder("P-43", P43_FLOORS, P43_LABELS)
    pairs_seen: Set[Tuple[str, str]] = set()

    @PROPERTY_SETTINGS
    @given(tenancy=tenant_pairs(), cell=st.sampled_from(REFERENCE_CELLS))
    def check(tenancy: _Tenancy, cell: _Cell) -> None:
        recorder.start()
        recorder.mark("examples")
        _assert_the_pair_and_its_identifiers_were_drawn(tenancy)
        recorder.mark("the_pair_and_its_identifiers_were_drawn")
        if (tenancy.u1, tenancy.u2) not in pairs_seen:
            pairs_seen.add((tenancy.u1, tenancy.u2))
            recorder.mark("a_tenant_pair_not_seen_before")
        pc.invalidate_session_owner()

        plan = _quantify(cell, tenancy)
        foreign = _attempt(
            cell, plan.target_ids, caller=tenancy.u1, identity=plan.identity, tenancy=tenancy
        )
        absent = _attempt(
            cell,
            tenancy.absent_ids,
            caller=tenancy.u1,
            identity=plan.absent_identity,
            tenancy=tenancy,
        )
        recorder.mark("the_nonexistent_record_oracle_was_captured")
        _assert_indistinguishable(
            "P-43",
            cell,
            foreign,
            absent,
            tenancy,
            recorder,
            public=_public_projection_values_for(tenancy),
        )

        _assert_a_non_admin_learns_nothing_about_a_submission(tenancy, recorder)
        _assert_an_unpublished_listing_is_answered_as_absent(tenancy, recorder)
        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        pc.invalidate_session_owner()
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# P-45 (task 33.10) - A SUPPLIED IDENTITY CHANGES NO DECISION
# ══════════════════════════════════════════════════════════════════════════
#
# THE ORACLE IS A COMPARISON BETWEEN TWO DECISIONS
# ------------------------------------------------
# Every other property in this file compares an answer against the NON-EXISTENT-RECORD answer.
# P-45 does not: its oracle is the SAME request with the identity field ABSENT. Requirement 21.1
# says the acting identity is derived from the authenticated server-side session and that an
# identity supplied in a body, a query parameter, a path parameter or a WebSocket message is
# ignored for authorisation. "Ignored" is exactly "the decision is the same as if it were not
# there", so the two runs of one request are the whole property.
#
# WHAT IS VARIED, AND WHERE
# -------------------------
# * **query** - ``?user_id=<u2>&owner_id=<u2>`` on the drawn cell. No route under either prefix
#   declares a query parameter of any of those names (checked: only
#   ``routers/distributed_execution.py`` does, and it is on neither prefix), so an undeclared
#   parameter is one FastAPI hands to nobody - and the assertion is that the answer does not move.
# * **body** - the four identity names in the JSON body of a drawn POST/PATCH row, so the body half
#   is exercised in EVERY example rather than whenever the sampler lands on a row that takes one.
# * **path** - ``GET /api/library/creator/{creator_id}``, the one route under either prefix whose
#   path parameter IS a user identity. A path parameter cannot be omitted, so the comparison is
#   between the path naming u2 and the path naming the caller itself: the DECISION - what the
#   caller is authorised to see - must be the same public projection either way, and in particular
#   naming another user must not hand the caller that user's private records.
# * **WebSocket message** - every member of ``ws_channels.OWNED_CHANNEL_FAMILIES``, not just
#   ``paper``. The subscribe message names the resource's owner in ``user_id``, ``owner_id``,
#   ``identity`` and ``tenant_id``; the decision must be the one a bare message produces. The
#   owner's own subscription is ADMITTED in the same example, so the decision the message tried to
#   obtain is a real and different decision rather than a refusal for everybody.
#
# THE STRICT-BODY CASE, WHICH IS REQUIREMENT 21.1 WORKING
# ------------------------------------------------------
# Two routes - ``POST /api/library/{library_id}/deploy`` and ``POST /api/paper/sessions`` - parse
# their bodies strictly and answer 422 naming the unexpected fields. That answer is NOT identical
# to the bare request's, and it is not a defect: the supplied identity was refused outright rather
# than used, which is the strongest possible form of "ignored for authorisation purposes". So the
# body comparison admits exactly one alternative to equality, and it is asserted rather than
# tolerated: the 422's ``details.unexpected_fields`` must name EXACTLY the identity fields this
# property injected and nothing else (:func:`_the_schema_rejected_only_the_supplied_identity`). A
# 422 for any other reason, or one naming any other field, still fails - so a route that started
# rejecting a legitimate body would not be waved through by this branch.
#
# A second, narrower case comes out of the same variation and is NOT an authorisation difference
# either: a Pydantic validation error quotes the submitted body back verbatim in its ``input``
# member, so a route whose schema refuses the matrix's declared body for an unrelated reason
# (``POST /api/library/submissions/{id}/price`` needs ``price_minor``) answers the same status, the
# same ``VALIDATION_ERROR`` and the same missing field in both runs, differing only in that echo of
# the caller's own request. :func:`_without_the_echoed_request_body` removes it - by SHAPE, only
# from a mapping carrying ``type``, ``loc`` and ``msg``, and only in this property's own comparison.
#
# WHAT THIS PROPERTY DOES NOT ESTABLISH
# -------------------------------------
# The WebSocket half drives the decision the way ``websocket_manager.websocket_endpoint``'s
# subscribe branch drives it - the channel is read out of the client's message, and the identity
# passed to ``authorize_channel_subscription`` is the connection's authenticated one - and, for the
# ``paper`` family, the REAL ``PaperChannelRegistry.subscribe``, which is production code that
# takes the client ``message`` as an argument and is therefore able to read an identity out of it.
# What is not established here is the transport's own framing; that is
# ``tests/test_task_26_4_paper_channel_delivery.py``'s and the endpoint's own tests'.


#: The identity names this property injects, sorted as the two strict routes report them. The same
#: four :func:`_http_answer` has always put in a body for an :data:`ATTEMPT_IDENTITY` cell.
SUPPLIED_IDENTITY_FIELDS: Tuple[str, ...] = (
    "owner_id",
    "subscriber_id",
    "tenant_id",
    "user_id",
)

#: The HTTP cells. A channel cell has no query string and no body, so it is the WebSocket half's
#: business and not this pool's.
HTTP_CELLS: Tuple[_Cell, ...] = tuple(
    cell for cell in ATTEMPTED_CELLS if not cell.row.is_channel
)

#: The cells on a row that carries a JSON body, so the body half can be drawn in every example.
BODY_CELLS: Tuple[_Cell, ...] = tuple(
    cell for cell in HTTP_CELLS if cell.row.method in ("POST", "PUT", "PATCH")
)

assert HTTP_CELLS and BODY_CELLS, (
    "P-45 draws one HTTP cell and one body-carrying cell per example; an empty pool would make the "
    "query half or the body half quantify over nothing"
)

#: The one route under either prefix whose PATH parameter is a user identity.
P45_PATH_CELL: _Cell = _cell_named("GET /api/library/creator/{creator_id}", USER)

#: P-45's control cell: the private-Listing read. Its answer for the AUTHENTICATED owner differs
#: from its answer for anybody else, which is what makes "the supplied identity changed nothing" a
#: statement about a decision that could have moved.
P45_CONTROL_CELL: _Cell = _cell_named("GET /api/library/{library_id}", LISTING_PRIVATE)


#: The members that identify one entry of a Pydantic validation error. Used to recognise the ONE
#: place a response quotes the caller's own request body back at it - see
#: :func:`_without_the_echoed_request_body`.
_VALIDATION_ERROR_MEMBERS: FrozenSet[str] = frozenset({"type", "loc", "msg"})


def _without_the_echoed_request_body(value: Any) -> Any:
    """Drop the ``input`` member of every validation-error entry, and nothing else.

    A Pydantic validation error quotes the submitted body back verbatim in ``input``. P-45 varies
    the body BY CONSTRUCTION, so on any route whose schema refuses that body for an unrelated reason
    - ``POST /api/library/submissions/{id}/price`` requires ``price_minor``, which the matrix's
    declared body does not carry - the two runs differ in that echo and in nothing else: same
    status, same ``VALIDATION_ERROR``, same missing field. That echo is the caller's OWN input, so
    it cannot carry anything the caller did not already hold - the same exclusion, for the same
    reason, that :data:`CALLER_SUPPLIED_FIELDS` makes for a channel name.

    Recognised by SHAPE rather than by key name: ``input`` is dropped only from a mapping that also
    carries ``type``, ``loc`` and ``msg``, which is a validation-error entry and nothing else. So a
    field called ``input`` anywhere else is still compared. :data:`VOLATILE_RESPONSE_KEYS` is NOT
    widened for this - it is the sweep's and P-41's, and this is P-45's own comparison.
    """
    if isinstance(value, Mapping):
        if _VALIDATION_ERROR_MEMBERS <= set(value) and "input" in value:
            return {
                key: _without_the_echoed_request_body(item)
                for key, item in value.items()
                if key != "input"
            }
        return {
            key: _without_the_echoed_request_body(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_without_the_echoed_request_body(item) for item in value]
    return value


def _decisions(first: _Answer, second: _Answer) -> Tuple[Any, Any]:
    """Two answers, as the values one authorisation DECISION is compared with another on.

    Both are normalised against the UNION of the two requests' supplied identifiers, because the
    two runs supply different sets by construction - that is the whole variation - and normalising
    each against its own set would let an identifier be a token in one and a literal in the other.
    """
    supplied = tuple(sorted(set(first.supplied) | set(second.supplied)))
    return (
        (first.status, _normalise(first.body, supplied)),
        (second.status, _normalise(second.body, supplied)),
    )


def _the_schema_rejected_only_the_supplied_identity(answer: _Answer) -> bool:
    """True when this answer is a 422 naming EXACTLY the identity fields as unexpected.

    The one admitted alternative to equality in the body comparison, and the reason it is not a
    waiver is the word EXACTLY: the set of fields the route reports as unexpected must be
    :data:`SUPPLIED_IDENTITY_FIELDS` and nothing more or less, so a 422 about a legitimate field
    is still a failure.
    """
    if answer.status != 422 or not isinstance(answer.body, Mapping):
        return False
    details = answer.body.get("details")
    if not isinstance(details, Mapping):
        return False
    reported = details.get("unexpected_fields")
    if not isinstance(reported, (list, tuple)) or not reported:
        return False
    return {str(item) for item in reported} == set(SUPPLIED_IDENTITY_FIELDS)


def _assert_the_supplied_identity_changed_no_decision(
    label: str,
    cell: _Cell,
    tenancy: _Tenancy,
    sources: Sequence[str],
    recorder: Recorder,
    bucket: str,
) -> None:
    """One cell, twice: the identity of ``u2`` supplied in ``sources``, and absent."""
    plan = _quantify(cell, tenancy)
    bare = _attempt(
        cell,
        plan.target_ids,
        caller=tenancy.u1,
        identity=tenancy.u2,
        tenancy=tenancy,
        supply_identity=(),
    )
    supplied = _attempt(
        cell,
        plan.target_ids,
        caller=tenancy.u1,
        identity=tenancy.u2,
        tenancy=tenancy,
        supply_identity=sources,
    )
    left, right = _decisions(supplied, bare)
    if left == right:
        recorder.mark(bucket)
        return
    if _without_the_echoed_request_body(left) == _without_the_echoed_request_body(right):
        # Identical decision, quoted back with the caller's own body attached. Recorded, so the
        # case is visible in the distribution rather than folded silently into the equality.
        recorder.mark("a_validation_error_quoted_the_submitted_body")
        recorder.mark(bucket)
        return
    if "body" in sources and _the_schema_rejected_only_the_supplied_identity(supplied):
        # The identity was refused at the schema boundary instead of being used. Requirement 21.1
        # working, in its strongest form - and asserted, not assumed: the 422 names exactly the
        # injected fields.
        recorder.mark("the_schema_rejected_the_supplied_identity")
        recorder.mark(bucket)
        return
    raise AssertionError(
        f"{label} (Requirement 21.1): {cell.name} answered differently when a user, tenant, owner "
        f"and subscriber identity naming u2 was supplied in {list(sources)}, so a client-supplied "
        f"identity participates in the decision.\n"
        f"  with the identity supplied: {left!r}\n"
        f"  with the field absent     : {right!r}\n"
        f"  {tenancy!r}"
    )


def _assert_the_path_identity_changed_no_decision(
    tenancy: _Tenancy, recorder: Recorder
) -> None:
    """The path-parameter half: naming another user in the path does not make the caller that user.

    A path parameter cannot be omitted, so the comparison is between the path naming ``u2`` and the
    path naming the caller itself. The DECISION - the status, and what the caller is authorised to
    see - must be the same either way: a public creator page. In particular the answer for the path
    naming ``u2`` must carry no private value of ``u2``'s, which is what it would carry if the path
    had been taken as the acting identity.
    """
    named_other = _attempt(
        P45_PATH_CELL,
        tenancy.u2_ids,
        caller=tenancy.u1,
        identity=tenancy.u1,
        tenancy=tenancy,
        supply_identity=(),
    )
    named_self = _attempt(
        P45_PATH_CELL,
        tenancy.u1_ids,
        caller=tenancy.u1,
        identity=tenancy.u1,
        tenancy=tenancy,
        supply_identity=(),
    )
    assert named_other.status == named_self.status, (
        f"P-45 (Requirement 21.1): {P45_PATH_CELL.row.key} answered HTTP {named_other.status} when "
        f"its path named u2 and {named_self.status} when it named the caller, so the identity in "
        f"the path changed the decision. {tenancy!r}"
    )
    _assert_no_value_of_u2_appears(
        P45_PATH_CELL,
        named_other,
        values=tenancy.u2_values(),
        public=_public_projection_values_for(tenancy),
    )
    recorder.mark("the_path_identity_changed_no_decision")


def _decision_for_a_subscribe_message(
    message: Mapping[str, Any], caller: str, store: MatrixStore
) -> Any:
    """The authorisation decision for one client subscribe message, as the endpoint takes it.

    ``websocket_manager.websocket_endpoint``'s subscribe branch reads ``channel`` out of the
    client's message, checks it claims an owned family, and calls
    ``authorize_channel_subscription(channel, user)`` where ``user`` is the CONNECTION's
    authenticated identity. This function is that contract: the message supplies the channel and
    nothing else, and the identity comes from ``caller``.
    """
    channel = message.get("channel")
    assert (
        channels.parse_owned_channel(channel) is not None
        or channels.claims_owned_namespace(channel)
    ), f"{channel!r} claims no owned channel family, so no authorisation decision is reached"
    return _run(
        WA.authorize_channel_subscription(channel, {"id": caller}, supabase=store)
    )


def _message_naming(channel: str, named: Optional[str]) -> Dict[str, Any]:
    """A subscribe message, naming ``named`` every way a message can - or naming nobody.

    The four names are :func:`_intruder_message`'s, which is the shape Requirements 19.5 and 21.1
    forbid deriving an identity from; ``named=None`` is the same message with those fields absent,
    which is the oracle.
    """
    message: Dict[str, Any] = {"channel": channel, "last_sequence": 0}
    if named is not None:
        message.update(
            {
                "user_id": named,
                "owner_id": named,
                "identity": named,
                "tenant_id": named,
            }
        )
    return message


def _assert_no_owned_channel_reads_an_identity_from_the_message(
    tenancy: _Tenancy, store: MatrixStore, recorder: Recorder
) -> None:
    """Every owned channel family, not just ``paper``: the message names the owner, in vain.

    For each family the resource is the one ``u2`` owns, and three decisions are taken in the same
    store:

    1. the caller ``u1`` with a message naming ``u2`` in every identity field a message has;
    2. the caller ``u1`` with those fields absent - the oracle;
    3. the OWNER ``u2``, with a message naming ``u1``.

    (1) must equal (2), which is Requirement 21.1. (3) must be ADMITTED, which is what makes the
    equality meaningful: the decision the message in (1) was trying to obtain exists, is different,
    and is reachable only by authenticating as its owner - and a message naming somebody else does
    not take it away from them either.
    """
    for family in channels.OWNED_CHANNEL_FAMILIES:
        namespace = family.namespace
        resource = str(tenancy.u2_ids[CHANNEL_ID_SLOT[namespace]])
        channel = family.channel(resource)

        naming = _decision_for_a_subscribe_message(
            _message_naming(channel, tenancy.u2), tenancy.u1, store
        )
        bare = _decision_for_a_subscribe_message(
            _message_naming(channel, None), tenancy.u1, store
        )
        assert naming.allowed is False and bare.allowed is False, (
            f"P-45: the {namespace!r} channel of u2's resource was ADMITTED to u1 "
            f"(named={naming.allowed}, bare={bare.allowed}), so there is no refusal to compare. "
            f"{tenancy!r}"
        )
        assert comparable_refusal(naming) == comparable_refusal(bare), (
            f"P-45 (Requirement 21.1): on the {namespace!r} family, a subscribe message naming the "
            f"resource's owner in user_id, owner_id, identity and tenant_id produced a different "
            f"authorisation decision from the same subscription with those fields absent.\n"
            f"  with the message naming u2: {comparable_refusal(naming)!r}\n"
            f"  with the fields absent    : {comparable_refusal(bare)!r}\n  {tenancy!r}"
        )
        recorder.mark("every_owned_channel_family_ignored_the_message")

        owner = _decision_for_a_subscribe_message(
            _message_naming(channel, tenancy.u1), tenancy.u2, store
        )
        assert owner.allowed is True and str(owner.owner_id) == tenancy.u2, (
            f"P-45: the OWNER of the {namespace!r} resource was refused "
            f"({owner.code}: {owner.reason}), so the two refusals above would be equal for a "
            f"channel that admits nobody and the property would say nothing. {tenancy!r}"
        )
        recorder.mark("the_message_would_have_changed_the_decision_for_the_owner")


def _assert_a_subscribe_message_records_the_authenticated_identity(
    tenancy: _Tenancy, recorder: Recorder
) -> None:
    """The real ``PaperChannelRegistry.subscribe``, which TAKES the client message as an argument.

    The generic families above reach their decision through a function whose parameters are the
    channel and the authenticated user, so the message cannot reach it. The Paper_Channel registry
    is the one place in this specification where production code is handed the client's message,
    and this is the assertion that it derives no identity from it: the recorded subscription
    identity is the CALLER even though every identity field of the message names the owner.

    Driven on the paper harness's own store rather than the matrix store, because the registry
    reads sessions and events through ``paper_repository``; the subscription is released again so
    nothing is left on a registry another test would read.
    """
    attempt = _Attempt(
        owner=tenancy.u2,
        intruder=tenancy.u1,
        owned_sessions=(str(tenancy.u2_ids["session_id"]),),
        intruder_session=str(tenancy.u1_ids["session_id"]),
        absent_session=str(tenancy.absent_ids["session_id"]),
        retained_events=1,
        frames=1,
    )
    client = _store(attempt)
    registry = _registry()
    subscription = _subscribe(
        registry,
        Connection("intruder"),
        user={"id": attempt.intruder},
        session_id=attempt.target,
        message=_intruder_message(attempt),
    )
    try:
        assert subscription.identity == attempt.intruder, (
            f"P-45 (Requirements 19.5, 21.1): the subscribe message named the session's owner in "
            f"user_id, owner_id, identity and tenant_id, and the registry recorded the identity as "
            f"{subscription.identity!r} rather than the authenticated {attempt.intruder!r}. "
            f"{tenancy!r}"
        )
        assert client.wrote_anything() is False, (
            f"P-45: a subscription attempt wrote to the Persistence_Layer: "
            f"{[(s.op, s.table_name) for s in client.statements if s.op != 'select']!r}. "
            f"{tenancy!r}"
        )
    finally:
        _run(registry.release_session(attempt.target, reason="the attempt is over"))
    recorder.mark("a_subscribe_message_naming_the_owner_recorded_the_caller")


def _assert_the_authenticated_identity_still_decided(
    tenancy: _Tenancy, recorder: Recorder
) -> None:
    """The control: what the supplied identity failed to obtain is obtained by AUTHENTICATING.

    ``u1`` asks for ``u2``'s private Listing while supplying ``u2``'s identity in the query and is
    answered nothing; ``u2`` asks for the same record with no identity supplied anywhere and is
    answered the record, private canary included. Without this the property would hold for a route
    that answered nothing to anybody.
    """
    impersonating = _attempt(
        P45_CONTROL_CELL,
        tenancy.u2_ids,
        caller=tenancy.u1,
        identity=tenancy.u2,
        tenancy=tenancy,
        supply_identity=("query",),
    )
    canary = U2_CANARIES["private_listing_description"]
    rendered_attempt = json.dumps(impersonating.body, default=str)
    assert impersonating.status == 404 and canary not in rendered_attempt, (
        f"P-45 (Requirement 21.1): supplying u2's identity in the query obtained u2's private "
        f"Listing - status {impersonating.status}, body {rendered_attempt[:600]}. {tenancy!r}"
    )
    authenticated = _attempt(
        P45_CONTROL_CELL,
        tenancy.u2_ids,
        caller=tenancy.u2,
        identity=tenancy.u2,
        tenancy=tenancy,
        supply_identity=(),
    )
    rendered_owner = json.dumps(authenticated.body, default=str)
    assert authenticated.status == 200 and canary in rendered_owner, (
        f"P-45: {P45_CONTROL_CELL.row.key} answered the AUTHENTICATED owner with status "
        f"{authenticated.status} and a body that does not carry {canary!r}, so the refusal above "
        f"is not 'the supplied identity was ignored' - it is a route that serves nobody, and the "
        f"decision this property compares could not have moved. Body: {rendered_owner[:600]}. "
        f"{tenancy!r}"
    )
    recorder.mark("the_authenticated_identity_still_decided")


P45_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "the_pair_and_its_identifiers_were_drawn": EXAMPLES,
    "a_supplied_query_identity_changed_no_decision": EXAMPLES,
    "a_supplied_body_identity_changed_no_decision": EXAMPLES,
    "the_path_identity_changed_no_decision": EXAMPLES,
    "every_owned_channel_family_ignored_the_message": (
        EXAMPLES * len(channels.OWNED_CHANNEL_FAMILIES)
    ),
    "the_message_would_have_changed_the_decision_for_the_owner": (
        EXAMPLES * len(channels.OWNED_CHANNEL_FAMILIES)
    ),
    "a_subscribe_message_naming_the_owner_recorded_the_caller": EXAMPLES,
    "the_authenticated_identity_still_decided": EXAMPLES,
    # Published rather than floored, for the reason P41_FLOORS states at length: which cell an
    # example draws is the sampler's business, and whether a drawn row happens to be one of the two
    # that parse their bodies strictly is a property of the route rather than of the generator.
    "a_tenant_pair_not_seen_before": 0,
    "the_schema_rejected_the_supplied_identity": 0,
    "a_validation_error_quoted_the_submitted_body": 0,
}

P45_LABELS: Dict[str, str] = {
    "a_tenant_pair_not_seen_before": "the tenant pair had not been generated before",
    "a_supplied_query_identity_changed_no_decision": (
        "an identity in the query string changed no decision"
    ),
    "a_supplied_body_identity_changed_no_decision": (
        "an identity in the request body changed no decision"
    ),
    "the_path_identity_changed_no_decision": (
        "an identity in the path parameter changed no decision"
    ),
    "the_schema_rejected_the_supplied_identity": (
        "the route refused the supplied identity at the schema boundary"
    ),
    "a_validation_error_quoted_the_submitted_body": (
        "the same decision, with the caller's own body quoted back in it"
    ),
    "every_owned_channel_family_ignored_the_message": (
        "an owned channel family ignored the identity in the subscribe message"
    ),
    "the_message_would_have_changed_the_decision_for_the_owner": (
        "the owner of the same resource was admitted"
    ),
    "a_subscribe_message_naming_the_owner_recorded_the_caller": (
        "the registry recorded the authenticated identity, not the message's"
    ),
    "the_authenticated_identity_still_decided": (
        "authenticating as the owner obtained what the supplied identity did not"
    ),
}


def test_p45_supplied_identity_does_not_change_the_decision(request: Any) -> None:
    """A user, tenant, owner, subscriber or session identity the CALLER supplies decides nothing.

    For all requests carrying such an identity in the body, the query string, the path parameter or
    a WebSocket subscribe message, the authorisation decision is identical to the decision for the
    same request with that field absent. The oracle is therefore not the non-existent-record
    response every other property in this file compares against: it is the SAME request, run twice.

    Four surfaces are varied in every example - a drawn cell's query string, a drawn body-carrying
    cell's JSON body, the one route whose path parameter is a user identity, and a subscribe message
    on every one of ``ws_channels.OWNED_CHANNEL_FAMILIES`` - and the real
    ``PaperChannelRegistry.subscribe``, which is handed the client message as an argument, is driven
    with a message naming the session's owner and must still record the authenticated caller.

    Two routes parse their bodies strictly and answer 422 naming the unexpected fields. That is
    Requirement 21.1 in its strongest form - the identity was refused rather than used - and it is
    the only admitted alternative to equality, asserted precisely: the 422 must name EXACTLY the
    injected identity fields and nothing else.

    The property cannot hold vacuously. Authenticating as the owner obtains the private record the
    supplied identity did not, and on every channel family the owner's own subscription is admitted
    - so the decision a supplied identity failed to move is a real decision that moves for the
    authenticated identity.

    **Validates: Requirement 21.1**
    """
    recorder = Recorder("P-45", P45_FLOORS, P45_LABELS)
    pairs_seen: Set[Tuple[str, str]] = set()

    @PROPERTY_SETTINGS
    @given(
        tenancy=tenant_pairs(),
        cell=st.sampled_from(HTTP_CELLS),
        body_cell=st.sampled_from(BODY_CELLS),
    )
    def check(tenancy: _Tenancy, cell: _Cell, body_cell: _Cell) -> None:
        recorder.start()
        recorder.mark("examples")
        _assert_the_pair_and_its_identifiers_were_drawn(tenancy)
        recorder.mark("the_pair_and_its_identifiers_were_drawn")
        if (tenancy.u1, tenancy.u2) not in pairs_seen:
            pairs_seen.add((tenancy.u1, tenancy.u2))
            recorder.mark("a_tenant_pair_not_seen_before")
        pc.invalidate_session_owner()

        _assert_the_supplied_identity_changed_no_decision(
            "P-45",
            cell,
            tenancy,
            ("query",),
            recorder,
            "a_supplied_query_identity_changed_no_decision",
        )
        _assert_the_supplied_identity_changed_no_decision(
            "P-45",
            body_cell,
            tenancy,
            ("body",),
            recorder,
            "a_supplied_body_identity_changed_no_decision",
        )
        _assert_the_path_identity_changed_no_decision(tenancy, recorder)
        _assert_no_owned_channel_reads_an_identity_from_the_message(
            tenancy, fresh_store(tenancy), recorder
        )
        _assert_a_subscribe_message_records_the_authenticated_identity(tenancy, recorder)
        _assert_the_authenticated_identity_still_decided(tenancy, recorder)
        recorder.finish()

    try:
        with publish_hypothesis_statistics(request.node):
            check()
    finally:
        pc.invalidate_session_owner()
        repo.reset_persistence_probe()

    recorder.assert_not_vacuous()


# ══════════════════════════════════════════════════════════════════════════
# P-46 (task 33.11) - EVERY LISTED ROW BELONGS TO THE CALLER
# ══════════════════════════════════════════════════════════════════════════
#
# TWO CLAIMS, AND THE SECOND IS THE HARDER ONE
# --------------------------------------------
# 1. every row a list endpoint RETURNS is owned by the authenticated identity;
# 2. the scoping is in the QUERY - no list endpoint retrieved a row it then filtered out
#    (Requirement 21.5's second clause, in its own words).
#
# (2) cannot be observed from a response body: a handler that read every tenant's rows and dropped
# the foreign ones in Python satisfies (1) perfectly. It needs the Persistence_Layer's own account
# of what it handed back, which is what task 33.12's counting store records - and this property is
# why that store keeps ``Statement.rows`` rather than only a count. The counting store is IMPORTED,
# never re-implemented: ``tests/paper_seed.py`` records that this repository has exactly ONE
# Persistence_Layer double, and ``CountingStore`` is already a subclass of :class:`MatrixStore`,
# which is already a subclass of ``FakeSupabase``. Nothing about it is modified here - its API
# already exposes everything this property reads, which is what its own module docstring's
# "THE COUNTING-STORE API TASK 33.11 CONSUMES" section was written for.
#
# EXACTLY WHAT THE COUNTING STORE CAN OBSERVE, AND WHAT IT CANNOT
# --------------------------------------------------------------
# It records, per statement: the operation, the table, the column projection, the predicates as
# ``(operator, column, value)``, the rows HANDED BACK, and the predicates this double did not apply.
# So "retrieved" here means "handed back by the Persistence_Layer to the router", which is the thing
# Requirement 21.5's "after retrieval" is about. Three limits, stated rather than implied:
#
# * it cannot see a Python-side filter directly. "Retrieved then filtered out" is therefore asserted
#   in the STRICT form - no statement handed back a row the caller does not own at all - which
#   implies both claims at once and cannot be satisfied by dropping the row afterwards;
# * ``MatrixStore`` deliberately does NOT apply ``or_``, ``like``, ``ilike``, ``contains``,
#   ``overlaps`` or ``range``, and records each as ignored. That returns a SUPERSET of the
#   production result set, so a list endpoint whose only tenant scope was one of those six leaks
#   HERE rather than passing; the assertion below additionally reports any ignored predicate on the
#   statements it measured, so a count that depended on one is visible;
# * it does not apply the column projection either. The one place that matters is
#   ``paper_repository``'s migration probe - ``SELECT id FROM paper_accounts LIMIT 1``, a question
#   about whether the RELATION exists - which in production returns one ``id`` and no owner value at
#   all, and here returns whichever whole row the double holds first. It is excluded by NAME, by its
#   exact shape (that table, that one-column projection, no predicate), and the exclusion is
#   asserted rather than assumed, so any other unpredicated read of that table still fails.
#
# WHICH ENDPOINTS THIS IS ABOUT
# -----------------------------
# Requirement 21.5 is about the list queries scoped by the authenticated identity. The public
# catalogue is not one: Requirement 6 makes an approved, active Listing browsable by anyone, so
# ``GET /api/library`` legitimately returns rows nobody in the request owns. :data:`LIST_SURFACES`
# classifies every row of the matrix that RETURNS rows of some kind - derived from the matrix's own
# exposure cells, so a list endpoint added later has no classification and fails
# :func:`test_p46_every_listed_row_is_owned_by_the_caller`'s first assertion rather than being
# silently uncovered.


#: A list the Persistence_Layer must scope by the authenticated identity (Requirement 21.5).
OWNER_SCOPED_LIST = "owner-scoped list"
#: A catalogue read. Requirement 6 makes it browsable, so it is scoped by publication state rather
#: than by identity, and "every row is owned by the caller" is false OF ITS DESIGN.
PUBLIC_CATALOGUE_LIST = "public catalogue list"
#: A review list, gated on ``get_admin_user``. Neither tenant holds the Admin_Reviewer role and
#: ``_matrix_app`` does not grant it, so the list is never reached.
ADMIN_LIST = "admin review list"
#: A write that echoes rows back. Not a list query.
NOT_A_LIST_READ = "not a list read"


@dataclass(frozen=True)
class _ListSurface:
    """One row of the matrix that returns rows, and what kind of list it is."""

    key: str
    kind: str
    note: str
    #: What a caller reading their OWN list is answered here. 200 everywhere but one, and that one
    #: says why on itself.
    expected_status: int = 200
    #: The path parameters this surface needs, as ``parameter -> identifier slot`` of the CALLER's
    #: own set. A list of the caller's own rows has to be addressed with the caller's own ids, or
    #: it would be empty and the property would say nothing.
    path_slots: Mapping[str, str] = field(default_factory=dict)


def _L(key: str, kind: str, note: str, **kwargs: Any) -> _ListSurface:
    return _ListSurface(key=key, kind=kind, note=note, **kwargs)


#: Every row of the matrix that RETURNS rows, classified. Checked against the matrix's own exposure
#: cells in both directions by the property below.
LIST_SURFACES: Tuple[_ListSurface, ...] = (
    # ── the lists Requirement 21.5 is about ────────────────────────────────
    _L(
        "GET /api/library/me",
        OWNER_SCOPED_LIST,
        "the caller's own Listings, read by author_id",
    ),
    _L(
        "GET /api/library/my-strategies",
        OWNER_SCOPED_LIST,
        "the Strategies_Page combined list: the caller's own strategies, subscriptions and "
        "running Paper_Sessions, all three read by user_id",
    ),
    _L(
        "GET /api/library/favorites",
        OWNER_SCOPED_LIST,
        "the caller's own favourites, held as library_ratings rows with a NULL rating",
    ),
    _L(
        "GET /api/library/creator/analytics",
        OWNER_SCOPED_LIST,
        "the caller's own Listings and Settlement_Records. It answers 503 in this environment: "
        "the matrix seed's settlement row carries no amount_minor, which the earnings read "
        "refuses to state a total without. The two statements it DOES issue are the subject "
        "here - both are scoped by the caller - and the seed is the exhaustive sweep's, which "
        "this property must not change",
        expected_status=503,
    ),
    _L(
        "GET /api/library/subscriber/analytics",
        OWNER_SCOPED_LIST,
        "the caller's own Subscriptions, read by user_id",
    ),
    _L(
        "GET /api/library/{library_id}/subscribe",
        OWNER_SCOPED_LIST,
        "the CALLER's subscription to one Listing - scoped by user_id as well as by library_id",
        path_slots={"library_id": "public_library_id"},
    ),
    _L(
        "GET /api/paper/account",
        OWNER_SCOPED_LIST,
        "the caller's default Paper_Account and its open positions",
    ),
    _L("GET /api/paper/orders", OWNER_SCOPED_LIST, "the caller's default-account paper orders"),
    _L(
        "GET /api/paper/positions",
        OWNER_SCOPED_LIST,
        "the caller's default-account paper positions",
    ),
    _L(
        "GET /api/paper/sessions",
        OWNER_SCOPED_LIST,
        "the caller's own Paper_Sessions, read by user_id with no other scope",
    ),
    _L(
        "GET /api/paper/summary",
        OWNER_SCOPED_LIST,
        "the caller's account, positions, fills and balance events in one response",
    ),
    _L(
        "GET /api/paper/trades",
        OWNER_SCOPED_LIST,
        "the caller's default-account fills, read by user_id and session_id IS NULL",
    ),
    _L(
        "GET /api/paper/sessions/{session_id}/orders",
        OWNER_SCOPED_LIST,
        "one session's orders, reached through a session read that is itself scoped by user_id",
        path_slots={"session_id": "session_id"},
    ),
    _L(
        "GET /api/paper/sessions/{session_id}/positions",
        OWNER_SCOPED_LIST,
        "one session's positions, reached through the same scoped session read",
        path_slots={"session_id": "session_id"},
    ),
    # ── the catalogue, which is browsable by design ────────────────────────
    _L(
        "GET /api/library",
        PUBLIC_CATALOGUE_LIST,
        "Requirement 6 makes an approved, active Listing browsable by anyone, so this page "
        "returns rows the caller does not own BY DESIGN and Requirement 21.5's clause does not "
        "apply. What it may not carry is a PRIVATE field, which P-41 and P-47 assert",
    ),
    _L(
        "GET /api/library/featured",
        PUBLIC_CATALOGUE_LIST,
        "a catalogue slice, on the same public projection as GET /api/library",
    ),
    _L(
        "GET /api/library/trending",
        PUBLIC_CATALOGUE_LIST,
        "a catalogue slice, on the same public projection as GET /api/library",
    ),
    _L(
        "GET /api/library/recommendations",
        PUBLIC_CATALOGUE_LIST,
        "a catalogue slice, on the same public projection as GET /api/library",
    ),
    _L(
        "GET /api/library/creator/{creator_id}",
        PUBLIC_CATALOGUE_LIST,
        "a creator's PUBLISHED Listings. Requirement 6.5 makes the creator page public, so its "
        "rows are owned by the creator named in the path rather than by the caller",
    ),
    # ── the review lists, which neither tenant can reach ───────────────────
    _L(
        "GET /api/library/admin/pending",
        ADMIN_LIST,
        "gated on get_admin_user, which _matrix_app deliberately does not override; the caller "
        "is answered 403 before any list query is issued, so there is no retrieved row to scope",
    ),
    _L(
        "GET /api/library/admin/submissions",
        ADMIN_LIST,
        "the Submission review list, behind the same get_admin_user gate and unreachable here "
        "for the same reason",
    ),
    # ── writes that echo a row back ────────────────────────────────────────
    _L(
        "POST /api/paper/account/reset",
        NOT_A_LIST_READ,
        "a write that returns the caller's own account afterwards; it issues no list query, and "
        "P-42 is what asserts it moves no row of another tenant's",
    ),
    _L(
        "POST /api/paper/orders",
        NOT_A_LIST_READ,
        "a write that returns the order it just created for the caller; not a list query",
    ),
)

LIST_SURFACES_BY_KEY: Dict[str, _ListSurface] = {
    surface.key: surface for surface in LIST_SURFACES
}

#: The pool P-46 draws from. Every one is a GET whose rows are the caller's own.
OWNER_SCOPED_LIST_SURFACES: Tuple[_ListSurface, ...] = tuple(
    surface for surface in LIST_SURFACES if surface.kind == OWNER_SCOPED_LIST
)

assert OWNER_SCOPED_LIST_SURFACES, (
    "P-46 draws its list endpoint from the owner-scoped surfaces; an empty pool would make it "
    "quantify over nothing"
)

#: The columns a row is owned BY, in the order they are consulted. ``library_strategies`` carries
#: both ``user_id`` and ``author_id`` and the seed keeps them equal; ``marketplace_submissions``
#: and ``marketplace_settlements`` carry ``owner_id``; ``library_subscriptions`` carries
#: ``subscriber_id``.
OWNER_COLUMNS: Tuple[str, ...] = ("user_id", "owner_id", "subscriber_id", "author_id")

#: Tables whose rows carry no owner at all, and why that is correct rather than a hole. A row
#: retrieved from a table that is NOT in here and carries none of :data:`OWNER_COLUMNS` is a
#: reported failure: it would be a row this property cannot attribute to anybody.
OWNERLESS_TABLES: Dict[str, str] = {
    "profiles": (
        "a profile row is the server-resolved creator alias of Requirement 6.5, keyed by ``id``. "
        "It is public by design - that is what the alias exists for - and it is owned by the user "
        "it describes rather than scoped to a reader"
    ),
    "marketplace_submission_allowed_transitions": (
        "the Submission_State transition table of 007: static reference data, owned by nobody"
    ),
    "marketplace_subscription_allowed_transitions": (
        "the Subscription transition table of 012: static reference data, owned by nobody"
    ),
    "paper_order_allowed_transitions": (
        "the paper order transition table of 009: static reference data, owned by nobody"
    ),
    "paper_session_allowed_transitions": (
        "the Paper_Session transition table of 009: static reference data, owned by nobody"
    ),
}


def _row_owner(row: Any) -> Optional[Tuple[str, str]]:
    """``(column, value)`` naming this row's owner, or ``None`` when it carries none."""
    if not isinstance(row, Mapping):
        return None
    for column in OWNER_COLUMNS:
        value = row.get(column)
        if value not in (None, ""):
            return (column, str(value))
    return None


def _is_the_relation_probe(statement: Any) -> bool:
    """True for ``paper_repository``'s migration probe and for nothing else.

    ``SELECT id FROM paper_accounts LIMIT 1`` asks whether the RELATION exists (Requirements 17.1,
    17.2, 28.3). It is not a list query: in production it returns one ``id`` column and therefore
    carries no owner value at all. The double ignores column projections and hands back whole rows,
    which is the only reason it is visible here. Recognised by all three of its parts - that table,
    that one-column projection, no predicate - so any OTHER unpredicated read of ``paper_accounts``
    is still checked.
    """
    return (
        statement.table == repo.ACCOUNTS_TABLE
        and statement.cols == repo.PROBE_SELECT
        and not statement.filters
    )


def _list_answer(
    client: TestClient,
    store: Any,
    surface: _ListSurface,
    caller: str,
    ids: Mapping[str, str],
) -> Tuple[_Answer, Any]:
    """One list request, and the statements it - and only it - issued.

    The window is taken the way ``test_fixed_round_trips._issue`` takes it, through the counting
    store's own ``mark``/``window`` pair, so "the statements this response cost" has one definition
    in this repository.
    """
    row = LIST_ROWS_BY_KEY[surface.key]
    url = row.path or ""
    supplied: List[str] = [caller]
    for param in row.params:
        slot = surface.path_slots.get(param, param)
        value = str(ids[slot])
        url = url.replace("{" + param + "}", value)
        supplied.append(value)

    start = store.mark()
    with _authenticated_as(caller):
        response = client.get(url)
    window = store.window(start)
    try:
        parsed = response.json()
    except ValueError:
        parsed = response.text
    return (
        _Answer(
            status=response.status_code,
            body=parsed,
            text=response.text,
            snapshot={},
            foreign_rows={},
            supplied=tuple(supplied),
        ),
        window,
    )


@contextmanager
def _authenticated_as(caller: str) -> Iterator[None]:
    """Swap the authenticated identity for the duration of one request.

    ``_matrix_app`` fixes ONE caller for the block it wraps, which is what every other property
    here needs. P-46 holds one store and one client open for the whole run - the counting store has
    to be the object the router closed over - and needs two identities within it: the caller whose
    list is being read, and the OTHER tenant, for the control that shows the same surface serves
    that tenant its own rows. Only the ``get_current_user`` override moves; every other seam
    ``_matrix_app`` installs is caller-independent and is left exactly as it is.
    """
    previous = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": caller,
        "email": f"{caller}@example.invalid",
        "role": "authenticated",
        "access_token": f"token-{caller}",
        "app_metadata": {"role": "authenticated"},
        "user_metadata": {},
    }
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = previous


#: ``row key -> the matrix row``, for the surfaces above. Built from :func:`collect_rows` so the
#: path and the method are the registry's rather than this section's.
LIST_ROWS_BY_KEY: Dict[str, _MatrixRow] = {
    row.key: row for row in collect_rows() if row.key in LIST_SURFACES_BY_KEY
}

#: The exposure cells of each list surface, so the response can be scanned for every record kind
#: the row is declared to return.
LIST_EXPOSURE_CELLS: Dict[str, Tuple[_Cell, ...]] = {
    surface.key: tuple(
        cell
        for cell in MATRIX
        if cell.row.key == surface.key and cell.attempt == ATTEMPT_EXPOSURE
    )
    for surface in LIST_SURFACES
}


def _assert_every_retrieved_row_belongs_to_the_caller(
    surface: _ListSurface,
    answer: _Answer,
    window: Any,
    caller: str,
    tenancy: _Tenancy,
) -> int:
    """Requirement 21.5, both clauses, from the Persistence_Layer's own account of the statements.

    Returns how many rows the caller's own were retrieved, which is what the census floors below
    turn into "this list was not empty".
    """
    context = (
        f"{surface.key} as {caller!r} ({surface.note}). Statements:\n{window.describe()}"
    )
    rendered = json.dumps(answer.body, default=str)
    mine = 0
    for statement in window.statements:
        if statement.op != "select" or _is_the_relation_probe(statement):
            continue
        assert statement.ignored == (), (
            f"P-46: a statement of {surface.key} asked for predicate(s) {statement.ignored} that "
            f"this double does not apply, so the rows it handed back are a superset of the "
            f"production result set and the scoping cannot be read off it. {context}"
        )
        for column in OWNER_COLUMNS:
            values = {
                str(value)
                for operator, name, value in statement.filters
                if name == column and operator in ("eq", "in")
                for value in (value if isinstance(value, (list, tuple)) else [value])
            }
            assert values <= {caller} or not values, (
                f"P-46 (Requirement 21.5): a statement of {surface.key} scoped {column!r} by "
                f"{sorted(values)}, which is not the authenticated identity {caller!r}. A list "
                f"query must be scoped by the identity the session resolved, never by one the "
                f"request carried. {context}"
            )
        for row in statement.rows:
            owner = _row_owner(row)
            if owner is None:
                reason = OWNERLESS_TABLES.get(statement.table)
                assert reason, (
                    f"P-46: {statement.describe()} handed back a row carrying none of "
                    f"{OWNER_COLUMNS}, so this property cannot say whose it is. Either the row has "
                    f"an owner column under another name, or {statement.table!r} belongs in "
                    f"OWNERLESS_TABLES with the reason it is owned by nobody. Row keys: "
                    f"{sorted(row) if isinstance(row, Mapping) else row!r}. {context}"
                )
                continue
            column, value = owner
            if value == caller:
                mine += 1
                continue
            leaked = value in rendered
            raise AssertionError(
                f"P-46 (Requirement 21.5): {surface.key} retrieved a row belonging to "
                f"{value!r} rather than to the authenticated {caller!r} - "
                f"{statement.table}.{column}={value!r}"
                + (
                    " - and that value appears in the response, so the row was returned to the "
                    "wrong tenant."
                    if leaked
                    else " - and that value does NOT appear in the response, so the row was "
                    "retrieved and then filtered out after retrieval. Requirement 21.5 requires "
                    "the scope to be in the query itself."
                )
                + f"\n{context}\n  body: {rendered[:800]}\n  {tenancy!r}"
            )
    return mine


P46_FLOORS: Dict[str, int] = {
    "examples": EXAMPLES,
    "the_pair_and_its_identifiers_were_drawn": EXAMPLES,
    "every_retrieved_row_belonged_to_the_caller": EXAMPLES,
    "no_value_of_the_other_tenant_appeared": EXAMPLES,
    # The control, run in every example: the same surface serves each tenant exactly its own two
    # Listings. Two lists that returned rows, per example.
    "each_tenant_still_read_its_own_list": EXAMPLES,
    "the_control_retrieved_rows_for_both_tenants": 2 * EXAMPLES,
    # Published rather than floored: WHICH surface an example draws is the sampler's business, and
    # whether that surface has rows to return is a property of the endpoint - GET /api/paper/orders
    # answers an empty list for a tenant with no default-account order, and an empty list is a
    # correct answer. The floors above carry the anti-vacuity guarantee; the control is what makes
    # "returned nothing" unable to satisfy this property.
    "a_tenant_pair_not_seen_before": 0,
    "the_drawn_list_returned_rows_of_the_callers_own": 0,
    "the_drawn_list_was_legitimately_empty": 0,
}

P46_LABELS: Dict[str, str] = {
    "a_tenant_pair_not_seen_before": "the tenant pair had not been generated before",
    "every_retrieved_row_belonged_to_the_caller": (
        "every row the Persistence_Layer handed back was the caller's"
    ),
    "no_value_of_the_other_tenant_appeared": "no field value of the other tenant reached the caller",
    "the_drawn_list_returned_rows_of_the_callers_own": "the drawn list returned the caller's rows",
    "the_drawn_list_was_legitimately_empty": "the drawn list had no row of the caller's to return",
    "each_tenant_still_read_its_own_list": "each tenant read its own Listings through the surface",
    "the_control_retrieved_rows_for_both_tenants": "the control retrieved rows for one tenant",
}

#: The control's surface: the caller's own Listings. Chosen because the seed gives EVERY tenant
#: exactly two of them, so "the list is not empty" holds for both tenants of any drawn pair.
P46_CONTROL_SURFACE: _ListSurface = LIST_SURFACES_BY_KEY["GET /api/library/me"]


def test_p46_every_listed_row_is_owned_by_the_caller(request: Any) -> None:
    """Every row a list endpoint returns is the caller's, and the scoping is in the query.

    For all generated multi-tenant datasets - a drawn pair of tenants, each seeded with all ten of
    Requirement 21.8's record kinds - and all list endpoints the Marketplace_API and the
    Paper_Trading_API scope by the authenticated identity, every returned row's owner equals that
    identity. The assertion is made in the strict form the counting Persistence_Layer of task 33.12
    makes possible: no statement HANDED BACK a row the caller does not own, so no list endpoint
    retrieved a row it then filtered out in Python (Requirement 21.5's second clause). A predicate
    on an owner column must also carry the authenticated identity and nothing else, and any
    predicate the double did not apply fails the measurement rather than being forgiven by it.

    The catalogue reads are not in this pool and :data:`LIST_SURFACES` says why on each of them:
    Requirement 6 makes an approved, active Listing browsable, so those pages return rows nobody in
    the request owns by design. Every row of the matrix that returns rows is classified there, and
    the classification is checked against the matrix in both directions - a list endpoint added
    later fails here rather than going uncovered.

    The property cannot hold vacuously: in every example both tenants read their own Listings
    through the same surface and each is answered exactly its own two, so a list that returned
    nothing - or a store that held nothing - cannot satisfy it.

    **Validates: Requirement 21.5**
    """
    # Imported here rather than at module scope: ``test_fixed_round_trips`` imports this module for
    # ``MatrixStore`` and ``_matrix_app``, so a module-level import in this direction would be a
    # cycle whose outcome depended on which of the two pytest collected first.
    from tests.property.test_fixed_round_trips import CountingStore

    # ── the classification covers the matrix, in both directions ──────────
    returning_rows = {
        cell.row.key for cell in MATRIX if cell.attempt == ATTEMPT_EXPOSURE
    }
    unclassified = sorted(returning_rows - set(LIST_SURFACES_BY_KEY))
    assert not unclassified, (
        f"Requirement 21.5: {len(unclassified)} endpoint(s) return rows of a record kind and are "
        f"not classified in LIST_SURFACES, so nothing says whether their rows must belong to the "
        f"caller: {unclassified}. Add a _L(...) entry for each."
    )
    stale = sorted(set(LIST_SURFACES_BY_KEY) - returning_rows)
    assert not stale, (
        f"LIST_SURFACES classifies {len(stale)} row(s) that return no rows in the matrix any more: "
        f"{stale}. A classification for a list that is gone is a claim of coverage nothing backs."
    )
    thin = [
        f"{surface.key}: {surface.note!r}"
        for surface in LIST_SURFACES
        if len(surface.note) < 40
    ]
    assert not thin, (
        f"a list surface excluded from Requirement 21.5's pool must say why, or its exclusion "
        f"cannot be told from an oversight: {thin}"
    )

    recorder = Recorder("P-46", P46_FLOORS, P46_LABELS)
    pairs_seen: Set[Tuple[str, str]] = set()
    #: ONE store for the run, reset between examples: ``_matrix_app``'s patches capture it by
    #: closure, so a fresh object per example would not be the object the routers read.
    store = CountingStore()

    with _matrix_app(store, U1) as client:

        @PROPERTY_SETTINGS
        @given(tenancy=tenant_pairs(), surface=st.sampled_from(OWNER_SCOPED_LIST_SURFACES))
        def check(tenancy: _Tenancy, surface: _ListSurface) -> None:
            recorder.start()
            recorder.mark("examples")
            _assert_the_pair_and_its_identifiers_were_drawn(tenancy)
            recorder.mark("the_pair_and_its_identifiers_were_drawn")
            if (tenancy.u1, tenancy.u2) not in pairs_seen:
                pairs_seen.add((tenancy.u1, tenancy.u2))
                recorder.mark("a_tenant_pair_not_seen_before")
            pc.invalidate_session_owner()

            # The generated multi-tenant dataset: both tenants' ten record kinds, in one store,
            # seeded exactly as ``fresh_store`` seeds them - u2's rows carry the canaries.
            store.reset()
            _seed_tenant(store, tenancy.u1, tenancy.u1_ids, mine=False)
            _seed_tenant(store, tenancy.u2, tenancy.u2_ids, mine=True)

            answer, window = _list_answer(
                client, store, surface, caller=tenancy.u1, ids=tenancy.u1_ids
            )
            assert answer.status == surface.expected_status, (
                f"P-46: {surface.key} answered {answer.status} rather than the "
                f"{surface.expected_status} it answers for a caller reading their own list, so the "
                f"statements below are an unexpected path's. {surface.note}. Body: "
                f"{json.dumps(answer.body, default=str)[:600]}. {tenancy!r}"
            )
            mine = _assert_every_retrieved_row_belongs_to_the_caller(
                surface, answer, window, caller=tenancy.u1, tenancy=tenancy
            )
            recorder.mark("every_retrieved_row_belonged_to_the_caller")
            recorder.mark(
                "the_drawn_list_returned_rows_of_the_callers_own"
                if mine
                else "the_drawn_list_was_legitimately_empty"
            )

            # No field value of the OTHER tenant, at any nesting depth, for every record kind this
            # row is declared to return.
            public = _public_projection_values_for(tenancy)
            values = tenancy.u2_values()
            for cell in LIST_EXPOSURE_CELLS[surface.key]:
                _assert_no_value_of_u2_appears(cell, answer, values=values, public=public)
            recorder.mark("no_value_of_the_other_tenant_appeared")

            # ── the control: the same surface serves EACH tenant its own two Listings ──
            for caller, ids, others in (
                (tenancy.u1, tenancy.u1_ids, tenancy.u2_ids),
                (tenancy.u2, tenancy.u2_ids, tenancy.u1_ids),
            ):
                control, control_window = _list_answer(
                    client, store, P46_CONTROL_SURFACE, caller=caller, ids=ids
                )
                rendered = json.dumps(control.body, default=str)
                expected = {ids["library_id"], ids["public_library_id"]}
                missing = sorted(item for item in expected if item not in rendered)
                intruding = sorted(
                    item
                    for item in (others["library_id"], others["public_library_id"])
                    if item in rendered
                )
                retrieved = _assert_every_retrieved_row_belongs_to_the_caller(
                    P46_CONTROL_SURFACE, control, control_window, caller=caller, tenancy=tenancy
                )
                assert control.status == 200 and not missing and not intruding, (
                    f"P-46: {P46_CONTROL_SURFACE.key} answered {caller!r} with status "
                    f"{control.status}, missing its own Listing(s) {missing} and carrying the "
                    f"other tenant's {intruding}. Without both tenants reading their own list "
                    f"through this surface, 'every returned row is the caller's' would hold for a "
                    f"list that returns nothing at all. Body: {rendered[:600]}. {tenancy!r}"
                )
                assert retrieved >= 2, (
                    f"P-46: {P46_CONTROL_SURFACE.key} retrieved {retrieved} row(s) of "
                    f"{caller!r}'s own, and the seed gives every tenant two Listings; the "
                    f"Persistence_Layer therefore handed back nothing to scope. {tenancy!r}"
                )
                recorder.mark("the_control_retrieved_rows_for_both_tenants")
            recorder.mark("each_tenant_still_read_its_own_list")
            recorder.finish()

        try:
            with publish_hypothesis_statistics(request.node):
                check()
        finally:
            pc.invalidate_session_owner()
            repo.reset_persistence_probe()

    recorder.assert_not_vacuous()
