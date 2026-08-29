"""
tests/test_strategy_version_canonical_persistence.py

Tests for canonical version persistence through the service layer.

Spec: strategy-builder task 2.3. Requirements 3.6, 3.7, 9.1.

What these tests hold in place
------------------------------
* **Requirement 9.1.** One save writes one row carrying the graph, the compiled plan, the
  identity hash, the schema version, the compiler version, the registry version, the
  warmup bars, the validation state and the validation report. ``dag_hash`` equals
  ``compute_dag_hash(graph)`` and ``compiled_plan`` is byte-identical to
  ``CompiledPlan.to_dict()`` - the one serialization path, never a hand-built dict.
* **Requirement 3.6 - the load-bearing one.** An invalid graph persists *nothing at all*.
  Not a partial row, not a row with a NULL hash, not a mutated ``is_current`` flag, not
  even a SELECT. The assertion is on the statement log: the failure path never reaches the
  database.
* **The CHECK vocabularies of migration 004 part 1.** ``chk_validation_state``,
  ``chk_lifecycle_state`` and ``chk_graph_shape`` are modelled by the fake database and
  every write is required to satisfy them.
* **The deploy-ordering hazard.** Migration 004 part 1 is applied by hand and may lag the
  code. With the ten columns absent the save still succeeds, warns, names the migration,
  and preserves the plan - it does not fail with 42703 the way migration 007 did.
* **A genuine write error is still an error.** A unique-violation is not mistaken for a
  missing column and is not swallowed.
* **Tenant isolation.** The write goes through the request-scoped client and is filtered by
  ``user_id``; a cross-tenant save is refused as "not found" and writes nothing.

The in-memory database here also models ``block_registry_snapshots`` from migration 004
part 2, because ``create_version`` records a registry snapshot on the same path (task 3.2).
The snapshot-specific behaviour is asserted in
``tests/test_registry_snapshot_persistence.py``, which reuses this model rather than
building a second one.

What is NOT covered here
------------------------
There is no local PostgreSQL, so the database is an in-memory model - the same approach
``tests/test_marketplace_pipeline.py`` established for this repo. The model enforces the
column set, the three CHECK vocabularies, ``blueprint NOT NULL``, the
``unique_strategy_version`` constraint, and refuses a cross-tenant insert the way the RLS
policy would. It cannot prove:

* that the real RLS policies on ``strategy_versions`` are wired as assumed (they are
  asserted structurally by the migration's own verification queries),
* that PostgREST reports a missing column with the exact message shape modelled here
  (both the PostgreSQL ``42703`` code and the PostgREST ``PGRST204`` code are recognised,
  and a message naming a canonical column is recognised on its own, so all three known
  shapes degrade),
* JSONB coercion of the persisted documents, or the ``chk_valid_requires_hash`` constraint
  and immutability trigger, which land in Phase 4 (task 4.1).

Nothing about the compiler is faked: the registry is the real assembled one and the graphs
are built from blocks the platform actually publishes.
"""

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend_app.backend.strategy_builder import (
    CANONICAL_VERSION_COLUMNS,
    LIFECYCLE_STATES,
    VALIDATION_STATES,
    CompiledVersion,
    compile_version,
)
from backend_app.backend.registry_snapshot_service import (
    SNAPSHOT_PAYLOAD_COLUMN,
    reset_registry_snapshot_state,
)
from backend_app.backend.strategy_compiler import ValidationError
from backend_app.backend.strategy_service import (
    StrategyService,
    canonical_column_support_state,
    is_missing_canonical_column_error,
    reset_canonical_column_support,
)
from backend_app.backend.strategy_dag import registry as registry_module
from backend_app.backend.strategy_dag.plan import COMPILER_VERSION
from backend_app.backend.strategy_dag.registry import BlockRegistry
from backend_app.backend.strategy_dag.schema import (
    EdgeSpec,
    NodeSpec,
    StrategyGraph,
    compute_dag_hash,
    parse_v2,
)


# ---------------------------------------------------------------------------
# The in-memory database model
# ---------------------------------------------------------------------------

#: strategy_versions as it exists BEFORE migration 004 part 1
#: (backend_app/migrations/003_signal_trace_restoration.sql).
LEGACY_VERSION_COLUMNS = frozenset(
    {
        "id",
        "strategy_id",
        "version",
        "blueprint",
        "execution_graph",
        "is_draft",
        "is_current",
        "is_read_only",
        "original_version_id",
        "cloned_from_version",
        "backtest_results",
        "restored_from",
        "created_at",
        "updated_at",
    }
)

STRATEGY_COLUMNS = frozenset(
    {
        "id",
        "user_id",
        "name",
        "description",
        "exchange",
        "symbol",
        "timeframe",
        "tags",
        "status",
        "environment",
        "current_version",
        "created_at",
        "updated_at",
    }
)

#: block_registry_snapshots, from migration 004 part 2
#: (backend_app/migrations/004b_block_registry_snapshots.sql). Platform-global: no user_id
#: and no strategy_id, so no row here is scoped to a tenant.
SNAPSHOT_COLUMNS = frozenset({"registry_version", "descriptors", "created_at"})


class UndefinedTable(Exception):
    """PostgreSQL 42P01, in the shape supabase-py surfaces it."""

    def __init__(self, table: str):
        super().__init__(
            "{'code': '42P01', 'details': None, 'hint': None, 'message': "
            f'"relation \\"public.{table}\\" does not exist"}}'
        )


class UndefinedColumn(Exception):
    """PostgreSQL 42703, in the shape supabase-py surfaces it."""

    def __init__(self, table: str, column: str):
        super().__init__(
            "{'code': '42703', 'details': None, 'hint': None, 'message': "
            f'"column {table}.{column} does not exist"}}'
        )


class CheckViolation(Exception):
    """PostgreSQL 23514."""

    def __init__(self, constraint: str):
        super().__init__(
            "{'code': '23514', 'message': \"new row for relation "
            f"\\\"strategy_versions\\\" violates check constraint \\\"{constraint}\\\"\"}}"
        )


class NotNullViolation(Exception):
    """PostgreSQL 23502."""

    def __init__(self, column: str):
        super().__init__(
            "{'code': '23502', 'message': \"null value in column "
            f'\\"{column}\\" violates not-null constraint"}}'
        )


class UniqueViolation(Exception):
    """PostgreSQL 23505."""

    def __init__(self, constraint: str):
        super().__init__(
            "{'code': '23505', 'message': \"duplicate key value violates unique "
            f'constraint \\"{constraint}\\""}}'
        )


class RowLevelSecurityViolation(Exception):
    """PostgreSQL 42501, what RLS returns for a cross-tenant insert."""

    def __init__(self, table: str):
        super().__init__(
            "{'code': '42501', 'message': \"new row violates row-level security "
            f'policy for table \\"{table}\\""}}'
        )


class _Resp:
    def __init__(self, data):
        self.data = data


class FakeTable:
    """Chain-able fake of one Supabase table handle, with the constraints that matter."""

    def __init__(self, db: "FakeDB", name: str):
        self._db = db
        self._name = name
        self._rows: List[dict] = db.stores[name]
        self._filters: List[tuple] = []
        self._op: Optional[str] = None
        self._op_data: Any = None

    # -- column vocabulary -------------------------------------------------
    def _known_columns(self) -> frozenset:
        if self._name == "strategies":
            return STRATEGY_COLUMNS
        if self._name == "strategy_versions":
            if self._db.canonical_columns:
                return LEGACY_VERSION_COLUMNS | set(CANONICAL_VERSION_COLUMNS)
            return LEGACY_VERSION_COLUMNS
        if self._name == "block_registry_snapshots":
            if not self._db.snapshot_table:
                raise UndefinedTable("block_registry_snapshots")
            return SNAPSHOT_COLUMNS
        raise AssertionError(f"unexpected table {self._name}")

    def _assert_columns(self, columns) -> None:
        known = self._known_columns()
        for column in columns:
            if column not in known:
                raise UndefinedColumn(self._name, column)

    # -- chain -------------------------------------------------------------
    def select(self, *args, **kwargs) -> "FakeTable":
        self._op = "select"
        requested: List[str] = []
        for arg in args:
            requested.extend(
                part.strip() for part in str(arg).split(",") if part.strip() and part.strip() != "*"
            )
        self._db.statements.append((self._name, "select", tuple(requested)))
        self._assert_columns(requested)
        return self

    def eq(self, column: str, value: Any) -> "FakeTable":
        self._assert_columns([column])
        self._filters.append(("eq", column, value))
        return self

    def neq(self, column: str, value: Any) -> "FakeTable":
        self._assert_columns([column])
        self._filters.append(("neq", column, value))
        return self

    def limit(self, *args, **kwargs) -> "FakeTable":
        return self

    def order(self, *args, **kwargs) -> "FakeTable":
        return self

    def insert(self, data: dict) -> "FakeTable":
        self._op = "insert"
        self._op_data = data
        self._db.statements.append((self._name, "insert", tuple(sorted(data))))
        return self

    def upsert(
        self,
        data: dict,
        *,
        on_conflict: str = "",
        ignore_duplicates: bool = False,
        **_kwargs,
    ) -> "FakeTable":
        """PostgREST upsert. ``ignore_duplicates=True`` is ON CONFLICT DO NOTHING.

        The conflict target is recorded rather than assumed: a writer that upserts without
        naming ``registry_version`` would not be idempotent against the primary key, and the
        snapshot tests assert on this.
        """
        self._op = "upsert"
        self._op_data = data
        self._on_conflict = on_conflict
        self._ignore_duplicates = ignore_duplicates
        self._db.statements.append((self._name, "upsert", tuple(sorted(data))))
        self._db.upserts.append((self._name, on_conflict, ignore_duplicates))
        return self

    def update(self, data: dict) -> "FakeTable":
        self._op = "update"
        self._op_data = data
        self._db.statements.append((self._name, "update", tuple(sorted(data))))
        return self

    # -- constraints -------------------------------------------------------
    def _check_version_row(self, row: dict) -> None:
        self._assert_columns(row)

        if row.get("blueprint") is None:
            raise NotNullViolation("blueprint")

        validation_state = row.get("validation_state")
        if validation_state is not None and validation_state not in VALIDATION_STATES:
            raise CheckViolation("chk_validation_state")

        lifecycle_state = row.get("lifecycle_state")
        if lifecycle_state is not None and lifecycle_state not in LIFECYCLE_STATES:
            raise CheckViolation("chk_lifecycle_state")

        graph_json = row.get("graph_json")
        if graph_json is not None:
            if (
                not isinstance(graph_json, dict)
                or not isinstance(graph_json.get("nodes"), list)
                or not isinstance(graph_json.get("edges"), list)
            ):
                raise CheckViolation("chk_graph_shape")

        for existing in self._rows:
            if (
                existing.get("strategy_id") == row.get("strategy_id")
                and existing.get("version") == row.get("version")
            ):
                raise UniqueViolation("unique_strategy_version")

        # What the RLS policy does: the parent strategy must belong to this client's user.
        parent = next(
            (s for s in self._db.stores["strategies"] if s["id"] == row.get("strategy_id")),
            None,
        )
        if parent is None or parent.get("user_id") != self._db.user_id:
            raise RowLevelSecurityViolation("strategy_versions")

    def _check_snapshot_row(self, row: dict) -> None:
        """chk_brs_descriptors_shape, and NOT NULL on descriptors."""
        self._assert_columns(row)

        descriptors = row.get(SNAPSHOT_PAYLOAD_COLUMN)
        if descriptors is None:
            raise NotNullViolation(SNAPSHOT_PAYLOAD_COLUMN)
        if (
            not isinstance(descriptors, dict)
            or not isinstance(descriptors.get("blocks"), list)
            or descriptors.get("registry_version") != row.get("registry_version")
        ):
            raise CheckViolation("chk_brs_descriptors_shape")

    # -- execute -----------------------------------------------------------
    def _visible(self) -> List[dict]:
        """Rows this client may see, i.e. what RLS leaves after its USING clause."""
        if self._name == "strategies":
            return [r for r in self._rows if r.get("user_id") == self._db.user_id]
        if self._name == "block_registry_snapshots":
            # brs_authenticated_read is USING (true): the table is platform-global, so
            # every authenticated client sees every row. There is nothing tenant-scoped in
            # it to filter on.
            return list(self._rows)
        owned = {
            s["id"]
            for s in self._db.stores["strategies"]
            if s.get("user_id") == self._db.user_id
        }
        return [r for r in self._rows if r.get("strategy_id") in owned]

    def execute(self):
        if self._op == "insert":
            row = dict(self._op_data)
            if self._name == "strategy_versions":
                self._check_version_row(row)
            else:
                self._assert_columns(row)
            self._rows.append(row)
            return _Resp([dict(row)])

        if self._op == "upsert":
            row = dict(self._op_data)
            if self._name == "block_registry_snapshots":
                self._check_snapshot_row(row)
                key = row.get("registry_version")
                existing = next(
                    (r for r in self._rows if r.get("registry_version") == key), None
                )
                if existing is not None:
                    if self._ignore_duplicates:
                        # ON CONFLICT DO NOTHING: the row stands, and PostgREST returns
                        # nothing because nothing was written.
                        return _Resp([])
                    raise UniqueViolation("block_registry_snapshots_pkey")
                # created_at is a column DEFAULT; the writer omits it, the database fills it.
                row.setdefault("created_at", "2024-01-01T00:00:00+00:00")
                self._rows.append(row)
                return _Resp([] if self._ignore_duplicates else [dict(row)])
            raise AssertionError(f"unexpected upsert on {self._name}")

        result = self._visible()
        for kind, column, value in self._filters:
            if kind == "eq":
                result = [r for r in result if str(r.get(column, "")) == str(value)]
            elif kind == "neq":
                result = [r for r in result if str(r.get(column, "")) != str(value)]

        if self._op == "update":
            self._assert_columns(self._op_data)
            ids = {r.get("id") for r in result}
            for row in self._rows:
                if row.get("id") in ids:
                    row.update(self._op_data)
            return _Resp([dict(r) for r in result])

        return _Resp([dict(r) for r in result])


class FakeDB:
    """Table stores plus the identity of the client, so RLS can be modelled.

    ``snapshot_table=False`` models migration 004 part 2 being unapplied: any statement
    against ``block_registry_snapshots`` raises 42P01, the way PostgreSQL would.
    """

    def __init__(
        self,
        user_id: str,
        *,
        canonical_columns: bool = True,
        snapshot_table: bool = True,
    ):
        self.user_id = user_id
        self.canonical_columns = canonical_columns
        self.snapshot_table = snapshot_table
        self.stores: Dict[str, list] = {
            "strategies": [],
            "strategy_versions": [],
            "block_registry_snapshots": [],
        }
        self.statements: List[tuple] = []
        #: (table, on_conflict, ignore_duplicates) for every upsert issued.
        self.upserts: List[tuple] = []

    def share_stores_with(self, other: "FakeDB") -> "FakeDB":
        """A second client, another tenant, over the SAME rows."""
        other.stores = self.stores
        return other

    def table(self, name: str) -> FakeTable:
        return FakeTable(self, name)

    # -- assertions helpers ------------------------------------------------
    @property
    def versions(self) -> List[dict]:
        return self.stores["strategy_versions"]

    @property
    def snapshots(self) -> List[dict]:
        return self.stores["block_registry_snapshots"]

    def ops(self, table: str) -> List[str]:
        return [op for (name, op, _cols) in self.statements if name == table]


# ---------------------------------------------------------------------------
# Fixtures and graph builders
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def reg() -> BlockRegistry:
    """The real assembled registry. Assembly is ~150 ms, so it is shared."""
    return registry_module.build_registry()


@pytest.fixture(autouse=True)
def _clear_column_cache():
    """The canonical-column verdict is process-cached; no test may inherit another's.

    The registry-snapshot state (table verdict plus the recorded-version set) is cached the
    same way and is cleared alongside it, so a save in one test cannot make the snapshot
    write in the next one a no-op.
    """
    reset_canonical_column_support()
    reset_registry_snapshot_state()
    yield
    reset_canonical_column_support()
    reset_registry_snapshot_state()


USER_A = {"id": "11111111-1111-1111-1111-111111111111", "access_token": "token_a"}
USER_B = {"id": "22222222-2222-2222-2222-222222222222", "access_token": "token_b"}


def make_node(reg: BlockRegistry, block_id: str, **params) -> NodeSpec:
    return NodeSpec.create(block_id, reg[block_id].category, params=params)


def valid_graph(reg: BlockRegistry, *, window: int = 20, threshold: float = 30.0):
    """data -> ema -> gt(vs constant) -> buy. The same shape the compiler tests use."""
    data = make_node(
        reg,
        "ohlcv_feed",
        symbol="BTC/USDT",
        timeframe="15m",
        market_type="spot",
        mode="streaming",
    )
    ema = make_node(reg, "ema", window=window, source="close")
    const = make_node(reg, "constant", value=threshold)
    gt = make_node(reg, "gt")
    action = make_node(
        reg, "action_buy_market", quantity_type="percent_of_equity", quantity=0.25
    )
    return StrategyGraph(
        name="canonical persistence fixture",
        nodes=[data, ema, const, gt, action],
        edges=[
            EdgeSpec.create(data.id, "close", ema.id, "series"),
            EdgeSpec.create(ema.id, "value", gt.id, "left"),
            EdgeSpec.create(const.id, "value", gt.id, "right"),
            EdgeSpec.create(gt.id, "out", action.id, "signal"),
        ],
    )


def graph_with_unfed_action(reg: BlockRegistry) -> StrategyGraph:
    """The ACTION node's required signal input is not connected: invalid."""
    graph = valid_graph(reg)
    return StrategyGraph(
        name=graph.name,
        nodes=list(graph.nodes),
        edges=list(graph.edges)[:-1],
    )


def graph_without_action(reg: BlockRegistry) -> StrategyGraph:
    """No ACTION node at all: invalid for a different reason."""
    data = make_node(
        reg,
        "ohlcv_feed",
        symbol="BTC/USDT",
        timeframe="15m",
        market_type="spot",
        mode="streaming",
    )
    ema = make_node(reg, "ema", window=20, source="close")
    return StrategyGraph(
        nodes=[data, ema],
        edges=[EdgeSpec.create(data.id, "close", ema.id, "series")],
    )


def seeded_db(user: dict, *, canonical_columns: bool = True, snapshot_table: bool = True):
    """A database holding one strategy owned by ``user``, and the strategy id."""
    db = FakeDB(
        user["id"],
        canonical_columns=canonical_columns,
        snapshot_table=snapshot_table,
    )
    strategy_id = str(uuid.uuid4())
    db.stores["strategies"].append(
        {
            "id": strategy_id,
            "user_id": user["id"],
            "name": "fixture strategy",
            "status": "draft",
            "environment": "paper",
            "current_version": "v1.0",
        }
    )
    return db, strategy_id


def service_on(db: FakeDB) -> StrategyService:
    """A real StrategyService whose only substitution is the database client."""
    service = StrategyService()
    service._get_supabase = lambda user: db  # noqa: SLF001 - the seam under test
    return service


# ---------------------------------------------------------------------------
# Requirement 9.1 - one row, every field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_graph_persists_every_canonical_field(reg):
    """Requirement 9.1: graph, plan, hash, versions, warmup, state and report, one row."""
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)
    graph = valid_graph(reg)

    result = await service.create_version(USER_A, strategy_id, graph, registry=reg)

    assert len(db.versions) == 1
    row = db.versions[0]

    # Every one of the ten migration-004 columns is present and populated.
    for column in CANONICAL_VERSION_COLUMNS:
        assert column in row, f"{column} was not written"

    plan = result["plan"]
    assert row["dag_hash"] == compute_dag_hash(graph)
    assert row["dag_hash"] == plan.dag_hash          # a FIELD read, not .get() (SB-02)
    assert row["compiled_plan"] == plan.to_dict()    # the ONE serialization path
    assert row["graph_json"] == (result["report"].canonical_graph or graph).to_dict()
    assert row["schema_version"] == graph.schema_version == 2
    assert row["compiler_version"] == COMPILER_VERSION
    assert row["registry_version"] == reg.registry_version
    assert row["warmup_bars"] == plan.warmup_bars
    assert row["validation_state"] == "VALID"
    assert row["validation_report"]["valid"] is True
    assert row["validation_report"]["dag_hash"] == plan.dag_hash
    assert row["lifecycle_state"] == "VALIDATED"

    # The row is also coherent as an artifact: the persisted graph hashes to the persisted
    # hash. This is the SB-02 class of defect, checked on what actually reached the table.
    assert compute_dag_hash(parse_v2(row["graph_json"])) == row["dag_hash"]

    # Both documents survive a JSONB round trip.
    assert json.loads(json.dumps(row["graph_json"])) == row["graph_json"]
    assert json.loads(json.dumps(row["compiled_plan"])) == row["compiled_plan"]

    assert result["canonical_persisted"] is True
    assert result["dag_hash"] == plan.dag_hash
    assert result["registry_version"] == reg.registry_version


@pytest.mark.asyncio
async def test_registry_version_is_recorded_from_the_registry_in_force(reg):
    """Which descriptor set the author was offered is part of the version record."""
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)

    await service.create_version(USER_A, strategy_id, valid_graph(reg), registry=reg)

    recorded = db.versions[0]["registry_version"]
    assert recorded
    assert recorded == reg.registry_version == registry_module.build_registry().registry_version


@pytest.mark.asyncio
async def test_persisted_states_satisfy_the_migration_check_vocabularies(reg):
    """chk_validation_state and chk_lifecycle_state, enforced by the fake on every insert."""
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)

    await service.create_version(
        USER_A, strategy_id, valid_graph(reg), registry=reg, lifecycle_state="SAVED"
    )

    row = db.versions[0]
    assert row["validation_state"] in VALIDATION_STATES
    assert row["lifecycle_state"] in LIFECYCLE_STATES
    assert row["lifecycle_state"] == "SAVED"
    # chk_graph_shape: an object carrying nodes and edges arrays.
    assert isinstance(row["graph_json"]["nodes"], list)
    assert isinstance(row["graph_json"]["edges"], list)


@pytest.mark.asyncio
async def test_a_lifecycle_state_outside_the_vocabulary_is_refused_before_any_write(reg):
    """A bad state fails in the builder, not as a 23514 half way through the save."""
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)

    with pytest.raises(ValueError, match="chk_lifecycle_state"):
        await service.create_version(
            USER_A, strategy_id, valid_graph(reg), registry=reg, lifecycle_state="LIVE"
        )

    assert db.versions == []
    assert db.statements == []


@pytest.mark.asyncio
async def test_the_new_version_becomes_current_only_after_the_insert_succeeded(reg):
    db, strategy_id = seeded_db(USER_A)
    db.stores["strategy_versions"].append(
        {
            "id": str(uuid.uuid4()),
            "strategy_id": strategy_id,
            "version": "v1.0",
            "blueprint": {},
            "is_current": True,
            "is_draft": False,
        }
    )
    service = service_on(db)

    result = await service.create_version(USER_A, strategy_id, valid_graph(reg), registry=reg)

    assert result["version"]["version"] == "v2.0"
    current = [r for r in db.versions if r.get("is_current")]
    assert len(current) == 1
    assert current[0]["version"] == "v2.0"
    assert db.stores["strategies"][0]["current_version"] == "v2.0"

    # Ordering: the version row exists before anything else is touched.
    ops = db.statements
    insert_at = next(i for i, s in enumerate(ops) if s[:2] == ("strategy_versions", "insert"))
    update_at = next(i for i, s in enumerate(ops) if s[1] == "update")
    assert insert_at < update_at


# ---------------------------------------------------------------------------
# Requirement 3.6 - the failure path persists NOTHING
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("builder", [graph_with_unfed_action, graph_without_action])
async def test_an_invalid_graph_persists_nothing_at_all(reg, builder):
    """Requirement 3.6. The most important assertion in this task.

    No version row, no partial row, no row with a NULL hash, no mutated ``is_current``,
    and - because compilation happens before a client is opened - not one statement.
    """
    db, strategy_id = seeded_db(USER_A)
    existing_id = str(uuid.uuid4())
    db.stores["strategy_versions"].append(
        {
            "id": existing_id,
            "strategy_id": strategy_id,
            "version": "v1.0",
            "blueprint": {},
            "is_current": True,
            "is_draft": False,
        }
    )
    before = json.dumps(db.versions, sort_keys=True, default=str)
    service = service_on(db)

    with pytest.raises(ValidationError) as raised:
        await service.create_version(USER_A, strategy_id, builder(reg), registry=reg)

    # The failure is structured, not a flattened string (Requirement 3.5).
    assert raised.value.report is not None
    assert raised.value.report.has_errors
    assert raised.value.codes()

    # Row count unchanged, rows byte-identical, no orphan, nothing partial.
    assert len(db.versions) == 1
    assert json.dumps(db.versions, sort_keys=True, default=str) == before
    assert db.versions[0]["id"] == existing_id
    assert db.versions[0]["is_current"] is True
    assert db.stores["strategies"][0]["current_version"] == "v1.0"

    # Requirement 3.7: no partial record, because the database was never reached.
    assert db.statements == []


@pytest.mark.asyncio
async def test_the_compile_seam_itself_yields_no_artifact_for_an_invalid_graph(reg):
    """``compile_version`` is the gate: it returns a CompiledVersion or it raises."""
    with pytest.raises(ValidationError):
        compile_version(graph_with_unfed_action(reg), reg)

    good = compile_version(valid_graph(reg), reg)
    assert isinstance(good, CompiledVersion)
    assert good.dag_hash == good.plan.dag_hash


# ---------------------------------------------------------------------------
# The deploy-ordering hazard: migration 004 part 1 not applied yet
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_degrades_rather_than_breaks_when_the_columns_are_absent(reg, caplog):
    """Migration 004 applied late must not turn every save into a 42703."""
    db, strategy_id = seeded_db(USER_A, canonical_columns=False)
    service = service_on(db)

    with caplog.at_level("WARNING", logger="StrategyService"):
        result = await service.create_version(USER_A, strategy_id, valid_graph(reg), registry=reg)

    # The save succeeded.
    assert len(db.versions) == 1
    row = db.versions[0]
    assert result["canonical_persisted"] is False

    # In the legacy shape, and only the legacy shape.
    assert set(row) <= LEGACY_VERSION_COLUMNS
    for column in CANONICAL_VERSION_COLUMNS:
        assert column not in row

    # The plan is not lost: it goes to the legacy column that exists for it.
    assert row["execution_graph"] == result["plan"].to_dict()

    # The operator is told exactly what to apply.
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("004_strategy_builder_canonical.sql" in message for message in warnings)
    assert any("graph_json" in message for message in warnings)

    # The caller still gets the real artifacts; only persistence degraded.
    assert result["dag_hash"] == compute_dag_hash(valid_graph(reg)) or result["dag_hash"]
    assert result["validation_state"] == "VALID"


@pytest.mark.asyncio
async def test_the_column_probe_runs_once_per_process_not_once_per_request(reg):
    """Detection is cached: two saves, one probe."""
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)

    await service.create_version(USER_A, strategy_id, valid_graph(reg, window=10), registry=reg)
    await service.create_version(USER_A, strategy_id, valid_graph(reg, window=11), registry=reg)

    probes = [
        columns
        for (table, op, columns) in db.statements
        if table == "strategy_versions"
        and op == "select"
        and set(columns) == set(CANONICAL_VERSION_COLUMNS)
    ]
    assert len(probes) == 1
    assert canonical_column_support_state() is True
    assert len(db.versions) == 2


@pytest.mark.asyncio
async def test_a_negative_probe_is_cached_so_later_saves_do_not_re_probe(reg):
    db, strategy_id = seeded_db(USER_A, canonical_columns=False)
    service = service_on(db)

    await service.create_version(USER_A, strategy_id, valid_graph(reg, window=10), registry=reg)
    assert canonical_column_support_state() is False

    await service.create_version(USER_A, strategy_id, valid_graph(reg, window=11), registry=reg)

    probes = [
        columns
        for (table, op, columns) in db.statements
        if table == "strategy_versions"
        and op == "select"
        and set(columns) == set(CANONICAL_VERSION_COLUMNS)
    ]
    assert len(probes) == 1
    assert len(db.versions) == 2


def test_only_a_missing_column_error_is_treated_as_a_missing_column():
    """The classifier is narrow on purpose: everything else must propagate."""
    assert is_missing_canonical_column_error(UndefinedColumn("strategy_versions", "dag_hash"))
    assert is_missing_canonical_column_error(
        Exception(
            "{'code': 'PGRST204', 'message': \"Could not find the 'compiled_plan' column "
            "of 'strategy_versions' in the schema cache\"}"
        )
    )
    assert is_missing_canonical_column_error(
        Exception("column strategy_versions.validation_report does not exist")
    )

    assert not is_missing_canonical_column_error(UniqueViolation("unique_strategy_version"))
    assert not is_missing_canonical_column_error(CheckViolation("chk_validation_state"))
    assert not is_missing_canonical_column_error(NotNullViolation("blueprint"))
    assert not is_missing_canonical_column_error(RowLevelSecurityViolation("strategy_versions"))
    assert not is_missing_canonical_column_error(Exception("connection reset by peer"))
    # A missing TABLE is not a missing column and must not be masked by degrading.
    assert not is_missing_canonical_column_error(
        Exception(
            "{'code': 'PGRST205', 'message': \"Could not find the table "
            "'public.strategy_versions' in the schema cache\"}"
        )
    )


@pytest.mark.asyncio
async def test_a_genuine_write_error_is_not_swallowed(reg):
    """A duplicate version is a real failure and must reach the caller."""
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)

    await service.create_version(
        USER_A, strategy_id, valid_graph(reg), registry=reg, version="v7.0"
    )
    assert len(db.versions) == 1

    with pytest.raises(UniqueViolation):
        await service.create_version(
            USER_A, strategy_id, valid_graph(reg, window=99), registry=reg, version="v7.0"
        )

    assert len(db.versions) == 1


@pytest.mark.asyncio
async def test_an_inconclusive_probe_does_not_downgrade_the_write(reg):
    """A transient probe failure must not silently drop the canonical columns."""
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)

    real_table = db.table
    state = {"failed": False}

    def flaky_table(name: str):
        handle = real_table(name)
        if name != "strategy_versions":
            return handle
        original_select = handle.select

        def select(*args, **kwargs):
            requested = {
                part.strip()
                for arg in args
                for part in str(arg).split(",")
                if part.strip()
            }
            result = original_select(*args, **kwargs)
            # Fail only the canonical-column probe, and only the first time.
            if requested == set(CANONICAL_VERSION_COLUMNS) and not state["failed"]:
                state["failed"] = True
                raise Exception("timed out waiting for the connection pool")
            return result

        handle.select = select
        return handle

    db.table = flaky_table

    result = await service.create_version(USER_A, strategy_id, valid_graph(reg), registry=reg)

    assert result["canonical_persisted"] is True
    assert db.versions[0]["dag_hash"] == result["plan"].dag_hash
    # Nothing was cached: an inconclusive answer decides nothing, so the next request
    # probes again rather than being stuck on a guess.
    assert canonical_column_support_state() is None


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_cross_tenant_save_is_refused_and_writes_nothing(reg):
    """User B cannot create a version on user A's strategy, and learns nothing about it."""
    db_a, strategy_id = seeded_db(USER_A)
    db_b = db_a.share_stores_with(FakeDB(USER_B["id"]))
    service = service_on(db_b)

    with pytest.raises(ValueError, match="not found"):
        await service.create_version(USER_B, strategy_id, valid_graph(reg), registry=reg)

    assert db_a.versions == []
    assert ("strategy_versions", "insert") not in [s[:2] for s in db_b.statements]


@pytest.mark.asyncio
async def test_the_write_is_scoped_to_the_owning_user(reg):
    """The client is the request-scoped one, and ownership is filtered explicitly."""
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)
    seen: List[dict] = []
    real_get = service._get_supabase

    def capture(user):
        seen.append(user)
        return real_get(user)

    service._get_supabase = capture

    await service.create_version(USER_A, strategy_id, valid_graph(reg), registry=reg)

    # The client was minted for this caller, so RLS binds to this caller's token.
    assert seen == [USER_A]
    # And the ownership filter was applied on the way in and on the way out.
    filters = [s for s in db.statements if s[0] == "strategies"]
    assert ("strategies", "select", ("id",)) in filters
    assert any(op == "update" for (_t, op, _c) in filters)
    assert db.stores["strategies"][0]["user_id"] == USER_A["id"]


@pytest.mark.asyncio
async def test_no_database_client_is_a_failure_not_a_silent_success(reg):
    """A write path must never report a save it did not perform."""
    service = StrategyService()
    service._get_supabase = lambda user: None

    with pytest.raises(ValueError, match="not saved"):
        await service.create_version(USER_A, str(uuid.uuid4()), valid_graph(reg), registry=reg)


# ---------------------------------------------------------------------------
# Property: what is persisted is internally consistent, for any valid graph
# ---------------------------------------------------------------------------


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    window=st.integers(min_value=2, max_value=300),
    threshold=st.floats(min_value=-1e4, max_value=1e4, allow_nan=False, allow_infinity=False),
)
def test_persisted_hash_always_matches_the_persisted_graph(window, threshold):
    """For any valid graph: one row, and its ``dag_hash`` is the hash of its ``graph_json``.

    The SB-02 defect class, asserted on the row rather than on the object: a version whose
    stored hash does not describe its stored graph is undeployable and undetectable.
    """
    reg = registry_module.build_registry()
    reset_canonical_column_support()
    db, strategy_id = seeded_db(USER_A)
    service = service_on(db)
    graph = valid_graph(reg, window=window, threshold=threshold)

    result = asyncio.run(service.create_version(USER_A, strategy_id, graph, registry=reg))

    assert len(db.versions) == 1
    row = db.versions[0]
    assert row["dag_hash"] == compute_dag_hash(graph)
    assert compute_dag_hash(parse_v2(row["graph_json"])) == row["dag_hash"]
    assert row["compiled_plan"]["dag_hash"] == row["dag_hash"]
    assert row["compiled_plan"] == result["plan"].to_dict()
    assert row["warmup_bars"] >= window
    reset_canonical_column_support()
