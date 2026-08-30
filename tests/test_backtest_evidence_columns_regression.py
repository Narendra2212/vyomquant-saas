"""
tests/test_backtest_evidence_columns_regression.py

Spec: marketplace-subscriptions-paper-trading task 12.2.
Requirements 3.4 and 3.8. ``design.md`` -> "6. ``strategy_backtests`` has two divergent
definitions and records no bar count (newly found)".

THIS FILE IS THE REVERT-DETECTOR FOR TASK 12.1
----------------------------------------------
Task 12.1 has landed: ``BacktestRuntime.run_backtest`` passes
``executed_bar_count=len(ohlcv_data)`` into ``BacktestService.update_backtest_results``,
which adds it to the UPDATE payload. Before it landed, a completed backtest persisted no
bar count at all - ``backtesting_engine.py`` line 189 tested ``len(price_data) < 50`` and
threw the number away, and the runtime knew ``len(ohlcv_data)`` and did not write it - so
no ``strategy_backtests`` row could satisfy Requirement 3.8's "non-null executed bar count
of at least 50" and every marketplace submission would have failed its bar-count criterion
on evidence that existed but was never written down.

``TestExecutedBarCountIsPersisted`` therefore asserts the *present* behaviour, and its
value is that it fails the moment the fix is reverted: drop the ``executed_bar_count=``
argument at the runtime call site, or drop the column from ``update_data``, and
``test_a_completed_backtest_persists_the_bar_count_it_ran_over`` fails. It is written as a
guard, not as a red test - the code it guards is already correct.

WHY THE SECOND HALF SKIPS TODAY
-------------------------------
``TestEvidenceColumnsAreWritableAndReadable`` asserts Requirement 3.4's obligation that
``version_id``, ``final_capital``, ``engine_version``, ``schema_version``, ``dag_hash``,
``dataset_checksum``, ``completed_at`` and ``error_message`` are all writable and readable
once ``backend_app/migrations/006_backtest_evidence_columns.sql`` reconciles the two
divergent ``strategy_backtests`` definitions:

  * definition A - ``backend_app/migrations/001_strategy_architecture.sql`` - has
    ``version_id``, ``final_capital``, ``completed_at`` and ``error_message`` but **none**
    of the four reproducibility columns ``backtest_service.create_backtest`` writes.
  * definition B - ``migrations/006_reconcile_production_database.sql`` - has the four
    reproducibility columns but **no** ``version_id``, ``final_capital`` or
    ``error_message``.

Both are ``CREATE TABLE IF NOT EXISTS``, so whichever ran first decides the shape. That
migration is task 11.1 and has not landed, so those tests **skip** with a message naming
the file, in the same way ``tests/test_sb06_exchange_agnostic_save.py`` skips on an absent
checkout artefact. They pass, without modification, once 11.1 lands.

``test_the_divergence_is_real_without_006`` does not skip: it exercises the gap that 006
closes, under each base definition, and is what makes the skip above a statement about a
missing migration rather than about a missing problem.

NO POSTGRESQL, AND NOTHING MOCKED THAT IS UNDER TEST
----------------------------------------------------
CI runs no PostgreSQL, so the double here is the PostgREST client - the single thing this
environment has no instance of - exactly as ``tests/test_task_8_2_deployment_binding.py``
does it. The real ``BacktestRuntime``, the real ``BacktestService`` and the real
``RiskEngine`` are used, and the fake client's ``strategy_backtests`` column set is
**parsed out of the migration SQL** rather than asserted from memory, so the writable /
readable claim is a claim about the committed schema.

The fake polices only the nine evidence columns. ``winning_trades``, ``losing_trades``,
``monthly_returns``, ``daily_returns``, ``execution_time_seconds`` and ``trades`` are
absent from one definition or the other too, and neither 006 nor task 12.x claims to
reconcile them; failing this file on them would be reporting somebody else's bug.
"""

import os
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import backtest_service as bs
from backend_app.backend.backtest_runtime import BacktestRuntime
from backend_app.backend.backtest_service import BacktestService

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Definition A - the ``strategy_backtests`` of 001, line 118.
DEFINITION_A_SQL = REPO_ROOT / "backend_app" / "migrations" / "001_strategy_architecture.sql"
#: Definition B - the ``strategy_backtests`` of the production reconciliation, line 445.
DEFINITION_B_SQL = REPO_ROOT / "migrations" / "006_reconcile_production_database.sql"
#: The reconciliation this file is the contract for (task 11.1).
MIGRATION_006 = REPO_ROOT / "backend_app" / "migrations" / "006_backtest_evidence_columns.sql"

TABLE = "strategy_backtests"

#: The nine additive columns of 006, in the order design.md lists them.
EVIDENCE_COLUMNS = (
    "version_id",
    "final_capital",
    "executed_bar_count",
    "engine_version",
    "schema_version",
    "dag_hash",
    "dataset_checksum",
    "completed_at",
    "error_message",
)

#: The eight task 12.2 names in its second bullet - the nine minus the bar count, which the
#: first half of this file owns.
REQUIREMENT_3_4_COLUMNS = tuple(c for c in EVIDENCE_COLUMNS if c != "executed_bar_count")

USER_ID = "11111111-1111-4111-8111-111111111111"
STRATEGY_ID = "22222222-2222-4222-8222-222222222222"
VERSION_ID = "33333333-3333-4333-8333-333333333333"
USER = {"id": USER_ID, "access_token": "token-for-the-rls-scoped-client"}

#: Deliberately not 50, not 20 and not 10_000: an assertion on this number cannot pass by
#: coincidence against a threshold or a fetch limit.
BAR_COUNT = 137


# ---------------------------------------------------------------------------
# Migration parsing - the schemas the fake client stands in for
# ---------------------------------------------------------------------------

def _create_table_columns(path: Path, table: str) -> frozenset:
    """Column names declared by ``CREATE TABLE ... <table> ( ... )`` in ``path``."""
    assert path.is_file(), f"missing migration: {path}"
    sql = path.read_text(encoding="utf-8", errors="replace").lower()
    found = set()
    pattern = (
        r"create\s+table[^;]*?(?:public\.)?" + re.escape(table) + r"\s*\((.*?)\n\s*\)\s*;"
    )
    for match in re.finditer(pattern, sql, re.S):
        for line in match.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("--"):
                continue
            name = re.match(r"([a-z0-9_]+)\s+[a-z]", line)
            if name and name.group(1) not in ("primary", "unique", "check", "foreign"):
                found.add(name.group(1))
    assert found, f"no CREATE TABLE {table} parsed out of {path.name}"
    return frozenset(found)


def _added_columns(path: Path, table: str) -> frozenset:
    """Column names added by ``ALTER TABLE ... <table> ADD COLUMN ...`` in ``path``."""
    sql = path.read_text(encoding="utf-8", errors="replace").lower()
    found = set()
    pattern = r"alter\s+table\s+(?:public\.)?" + re.escape(table) + r"(.*?);"
    for match in re.finditer(pattern, sql, re.S):
        for column in re.finditer(
            r"add\s+column\s+(?:if\s+not\s+exists\s+)?([a-z0-9_]+)", match.group(1)
        ):
            found.add(column.group(1))
    return frozenset(found)


def _definition(name: str) -> frozenset:
    if name == "A":
        return _create_table_columns(DEFINITION_A_SQL, TABLE)
    return _create_table_columns(DEFINITION_B_SQL, TABLE)


def _migration_006_columns() -> frozenset:
    """The columns 006 adds, or a skip naming the file when task 11.1 has not landed."""
    if not MIGRATION_006.is_file():
        pytest.skip(
            f"{MIGRATION_006.relative_to(REPO_ROOT)} is not in this checkout "
            "(marketplace-subscriptions-paper-trading task 11.1). The Requirement 3.4 "
            "writable/readable contract is a claim about that migration's columns, so it "
            "cannot be asserted before the migration exists."
        )
    return _added_columns(MIGRATION_006, TABLE)


#: The reconciled schema this file asserts task 12.1's write against. Hand-named rather
#: than parsed, because the first half of this file is about the Python code's behaviour
#: when the column is there - not about which migration put it there, which is 11.1's job
#: and is asserted separately below.
RECONCILED_FOR_BAR_COUNT = _definition("A") | frozenset(EVIDENCE_COLUMNS)


# ---------------------------------------------------------------------------
# The PostgREST double
# ---------------------------------------------------------------------------

def _requested(columns) -> list:
    if not isinstance(columns, str):
        return []
    return [c.strip() for c in columns.split(",") if c.strip() and c.strip() != "*"]


class _Result:
    def __init__(self, data):
        self.data = data
        self.error = None


class _Query:
    """A chainable stand-in for the PostgREST builder that honours ``.eq`` filters."""

    def __init__(self, parent, table):
        self._parent = parent
        self._table = table
        self._filters = {}
        self._mode = "select"
        self._payload = None

    # builder ------------------------------------------------------------
    def select(self, columns="*", *a, **kw):
        self._mode = "select"
        for column in _requested(columns):
            if self._parent.is_absent(self._table, column):
                raise RuntimeError(
                    f'column {self._table}.{column} does not exist '
                    f'(42703 undefined_column)'
                )
        return self

    def insert(self, payload, *a, **kw):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload, *a, **kw):
        self._mode = "update"
        self._payload = payload
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def limit(self, *a, **kw):
        return self

    def order(self, *a, **kw):
        return self

    # terminal -----------------------------------------------------------
    def execute(self):
        if self._mode in ("insert", "update"):
            offending = [
                c for c in (self._payload or {}) if self._parent.is_absent(self._table, c)
            ]
            if offending:
                raise RuntimeError(
                    f"Could not find the '{sorted(offending)[0]}' column of "
                    f"'{self._table}' in the schema cache (PGRST204)"
                )
        rows = self._parent.rows.setdefault(self._table, [])
        if self._mode == "insert":
            row = dict(self._payload)
            self._parent.inserts.append((self._table, dict(row)))
            rows.append(row)
            return _Result([row])
        matched = [r for r in rows if self._matches(r)]
        if self._mode == "update":
            self._parent.updates.append(
                (self._table, dict(self._payload), dict(self._filters))
            )
            for row in matched:
                row.update(self._payload)
        return _Result(matched)

    def _matches(self, row) -> bool:
        return all(str(row.get(k)) == str(v) for k, v in self._filters.items())


class _Supabase:
    """A PostgREST client whose ``strategy_backtests`` carries exactly ``columns``.

    Only :data:`EVIDENCE_COLUMNS` are policed. A payload key outside that set passes
    through untouched, because migration 006 does not claim to reconcile it and a failure
    on one would be this file reporting a defect it was not written to hold.
    """

    def __init__(self, columns, rows=None):
        self.columns = frozenset(columns)
        self.rows = {k: [dict(r) for r in v] for k, v in (rows or {}).items()}
        self.inserts = []
        self.updates = []

    def is_absent(self, table: str, column: str) -> bool:
        if table != TABLE or column not in EVIDENCE_COLUMNS:
            return False
        return column not in self.columns

    def table(self, name):
        return _Query(self, name)

    # convenience --------------------------------------------------------
    def row(self, table=TABLE):
        (row,) = self.rows[table]
        return row

    def updated(self, table=TABLE):
        return [payload for name, payload, _f in self.updates if name == table]


def _service(sb) -> BacktestService:
    """The real service, wired to the fake client at its one seam."""
    service = BacktestService()
    service._get_supabase = lambda user: sb  # noqa: SLF001 - the seam under test
    return service


def _running_row(**overrides):
    row = {
        "id": "44444444-4444-4444-8444-444444444444",
        "user_id": USER_ID,
        "strategy_id": STRATEGY_ID,
        "version": "v1",
        "status": "running",
    }
    row.update(overrides)
    return row


def _results(**overrides):
    results = {
        "total_return": 1234.5,
        "total_return_pct": 12.345,
        "win_rate": 0.55,
        "total_trades": 41,
        "final_capital": 11234.5,
        "execution_time_seconds": 3.5,
    }
    results.update(overrides)
    return results


@pytest.fixture(autouse=True)
def _clean_module_state():
    bs.reset_executed_bar_count_support()
    yield
    bs.reset_executed_bar_count_support()


# ---------------------------------------------------------------------------
# Doubles for the runtime's collaborators
# ---------------------------------------------------------------------------

def _bars(count=BAR_COUNT):
    """``count`` OHLCV bars in the shape ``fetch_historical_ohlcv`` returns."""
    return [
        [1_700_000_000_000 + i * 60_000, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0]
        for i in range(count)
    ]


class _StubDataEngine:
    def __init__(self, bars):
        self._bars = bars

    async def fetch_historical_ohlcv(self, **_kwargs):
        return self._bars


class _StubDagEngine:
    def execute(self, nodes, edges, market_data):
        return {"signals": pd.Series(1, index=market_data.index)}


class _StubVectorbtEngine:
    initial_capital = 10_000.0
    fees = 0.001
    slippage = 0.0005

    async def run_backtest_async(self, **_kwargs):
        return {"Total Trades": 41, "Final Equity": 11_234.5}, []


class _StubExecutionGraph:
    nodes = {}
    edges = []

    def to_dict(self):
        return {"schema_version": "2.0", "metadata": {"dag_hash": "cafebabe12345678"}}


class _StubStrategyPackage:
    execution_graph = _StubExecutionGraph()
    metadata = {"symbols": ["BTC/USDT"], "timeframe": "15m"}


def _runtime(sb) -> BacktestRuntime:
    """The real runtime, with only its data feed, DAG engine and simulator stubbed.

    ``run_backtest``'s own body - including the 50-bar guard and the ``len(ohlcv_data)``
    the fix passes on - is the code under test and is untouched.
    """
    runtime = BacktestRuntime(initial_capital=10_000.0)
    runtime.set_data_engine = lambda *_a, **_k: None
    runtime.data_engine = _StubDataEngine(_bars())
    runtime.dag_engine = _StubDagEngine()
    runtime.vectorbt_engine = _StubVectorbtEngine()
    runtime._calculate_performance_metrics = lambda *a, **k: {"win_rate": 0.55}
    runtime._generate_charts = lambda *a, **k: {}
    runtime.backtest_service = _service(sb)
    return runtime


# ---------------------------------------------------------------------------
# Task 12.1, held in place
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestExecutedBarCountIsPersisted:
    """The revert-detector. Each test fails if task 12.1's change is undone."""

    async def test_a_completed_backtest_persists_the_bar_count_it_ran_over(self):
        """End to end: the runtime's ``len(ohlcv_data)`` reaches the row.

        Revert either half of task 12.1 - the ``executed_bar_count=len(ohlcv_data)``
        argument in ``backtest_runtime.run_backtest`` or the ``update_data`` entry in
        ``backtest_service.update_backtest_results`` - and this fails.
        """
        sb = _Supabase(RECONCILED_FOR_BAR_COUNT)
        runtime = _runtime(sb)

        outcome = await runtime.run_backtest(
            strategy_package=_StubStrategyPackage(),
            user=USER,
            strategy_id=STRATEGY_ID,
            version_id=VERSION_ID,
            version="v1",
            start_date="2024-01-01",
            end_date="2024-06-30",
            exchange_instance=object(),
        )

        assert outcome["status"] == "completed"
        row = sb.row()
        # ``.get`` rather than ``[...]`` so a revert reports this sentence instead of a
        # bare KeyError.
        assert row.get("executed_bar_count") == BAR_COUNT, (
            "a completed backtest persisted no executed bar count. Requirement 3.8 "
            "requires a non-null count of at least 50 on the row, and the runtime "
            "already knows len(ohlcv_data) - it must pass it as executed_bar_count "
            "into update_backtest_results (task 12.1)."
        )
        (payload,) = sb.updated()
        assert payload["status"] == "completed"
        assert payload.get("executed_bar_count") == BAR_COUNT

    async def test_the_count_is_recorded_next_to_the_metrics_in_one_update(self):
        """One UPDATE carries the metrics and the count, and reads back as one row."""
        sb = _Supabase(RECONCILED_FOR_BAR_COUNT, {TABLE: [_running_row()]})
        service = _service(sb)

        written = await service.update_backtest_results(
            user=USER,
            backtest_id=_running_row()["id"],
            results=_results(),
            executed_bar_count=BAR_COUNT,
        )

        assert len(sb.updates) == 1, "the count must not cost a second round trip"
        assert written["executed_bar_count"] == BAR_COUNT
        read_back = await service.get_backtest(USER, _running_row()["id"])
        assert read_back["executed_bar_count"] == BAR_COUNT
        assert read_back["status"] == "completed"
        assert read_back["completed_at"] is not None

    async def test_a_caller_that_supplies_no_count_writes_no_count(self):
        """The pre-12.1 call shape, which the HTTP results handler still uses.

        ``executed_bar_count`` defaults to ``None`` and is then absent from the payload
        rather than written as an explicit ``NULL``, so a partial update cannot erase a
        count an earlier write recorded. This is the negative control for the test above:
        the column appears because the runtime supplies it, not because the service
        invents one.
        """
        sb = _Supabase(RECONCILED_FOR_BAR_COUNT, {TABLE: [_running_row()]})
        service = _service(sb)

        written = await service.update_backtest_results(
            user=USER,
            backtest_id=_running_row()["id"],
            results=_results(),
        )

        assert "executed_bar_count" not in written
        (payload,) = sb.updated()
        assert "executed_bar_count" not in payload

    async def test_an_unapplied_006_costs_the_count_and_never_the_metrics(self, caplog):
        """006 is hand-applied, so this code can reach production before the DDL does.

        Naming an absent column in the UPDATE would turn "the bar count was not recorded"
        into "the finished run's every metric was lost".
        """
        sb = _Supabase(_definition("A"), {TABLE: [_running_row()]})
        service = _service(sb)

        with caplog.at_level("WARNING"):
            written = await service.update_backtest_results(
                user=USER,
                backtest_id=_running_row()["id"],
                results=_results(),
                executed_bar_count=BAR_COUNT,
            )

        assert "executed_bar_count" not in written
        assert written["total_return"] == 1234.5
        assert written["final_capital"] == 11234.5
        assert written["status"] == "completed"
        assert bs.BACKTEST_EVIDENCE_MIGRATION in caplog.text, (
            "the degradation warning must name the migration an operator has to apply"
        )


# ---------------------------------------------------------------------------
# Requirement 3.4, once migration 006 reconciles the two definitions
# ---------------------------------------------------------------------------

class TestTheDivergenceIsReal:
    """What 006 is for. These do not skip - the gap exists in this checkout."""

    def test_the_two_definitions_disagree_about_the_evidence_columns(self):
        a, b = _definition("A"), _definition("B")
        assert {"engine_version", "schema_version", "dag_hash", "dataset_checksum"} & a == set(), (
            "definition A is expected to carry none of the reproducibility columns"
        )
        assert {"version_id", "final_capital", "error_message"} & b == set(), (
            "definition B is expected to carry neither version_id, final_capital nor "
            "error_message"
        )
        assert "executed_bar_count" not in (a | b), (
            "neither definition records a bar count - that is Requirement 3.8's gap"
        )

    @pytest.mark.asyncio
    async def test_definition_a_alone_cannot_store_the_reproducibility_columns(self):
        sb = _Supabase(_definition("A"))
        with pytest.raises(RuntimeError) as exc:
            await _service(sb).create_backtest(
                user=USER,
                strategy_id=STRATEGY_ID,
                version="v1",
                blueprint={"metadata": {"dag_hash": "cafebabe12345678"}},
                dataset="BTC/USDT",
                start_date="2024-01-01",
                end_date="2024-06-30",
                initial_capital=10_000.0,
                commission=0.001,
                slippage=0.0005,
                version_id=VERSION_ID,
            )
        assert "dag_hash" in str(exc.value) or "engine_version" in str(exc.value)

    @pytest.mark.asyncio
    async def test_definition_b_alone_cannot_store_the_version_reference(self):
        sb = _Supabase(_definition("B"))
        with pytest.raises(RuntimeError) as exc:
            await _service(sb).create_backtest(
                user=USER,
                strategy_id=STRATEGY_ID,
                version="v1",
                blueprint={"metadata": {"dag_hash": "cafebabe12345678"}},
                dataset="BTC/USDT",
                start_date="2024-01-01",
                end_date="2024-06-30",
                initial_capital=10_000.0,
                commission=0.001,
                slippage=0.0005,
                version_id=VERSION_ID,
            )
        assert "version_id" in str(exc.value)


class TestEvidenceColumnsAreWritableAndReadable:
    """Requirement 3.4, against migration 006. Skips until task 11.1 lands."""

    @pytest.mark.parametrize("column", EVIDENCE_COLUMNS)
    def test_migration_006_adds_the_column(self, column):
        added = _migration_006_columns()
        assert column in added, (
            f"006_backtest_evidence_columns.sql does not add {TABLE}.{column}. "
            f"Requirements 3.4 and 3.8 need all nine on the row regardless of which "
            f"CREATE TABLE IF NOT EXISTS won the race. Currently added: {sorted(added)}"
        )

    @pytest.mark.parametrize("base", ("A", "B"))
    def test_the_reconciled_schema_covers_every_evidence_column(self, base):
        """Additive over *either* starting shape - that is what reconciliation means."""
        reconciled = _definition(base) | _migration_006_columns()
        missing = [c for c in EVIDENCE_COLUMNS if c not in reconciled]
        assert not missing, (
            f"definition {base} plus 006 still lacks {missing}; a deployment that ran "
            f"that CREATE TABLE first would keep failing Requirement 3.4"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("base", ("A", "B"))
    async def test_every_evidence_column_is_writable_and_readable(self, base):
        """Write through the real service, read back through the real service.

        ``create_backtest`` writes ``version_id`` and the four reproducibility columns;
        ``update_backtest_results`` writes ``final_capital``, ``completed_at`` and the bar
        count; ``error_message`` has no writer in this service - the failure path sends
        ``results={"status": "failed", "error": ...}`` - so it is written at the client and
        asserted readable, which is the whole of Requirement 3.4's obligation for it here.
        """
        reconciled = _definition(base) | _migration_006_columns()
        sb = _Supabase(reconciled)
        service = _service(sb)

        created = await service.create_backtest(
            user=USER,
            strategy_id=STRATEGY_ID,
            version="v1",
            blueprint={"schema_version": "2.0", "metadata": {"dag_hash": "cafebabe12345678"}},
            dataset="BTC/USDT",
            start_date="2024-01-01",
            end_date="2024-06-30",
            initial_capital=10_000.0,
            commission=0.001,
            slippage=0.0005,
            version_id=VERSION_ID,
        )
        backtest_id = created["id"]

        await service.update_backtest_results(
            user=USER,
            backtest_id=backtest_id,
            results=_results(),
            executed_bar_count=BAR_COUNT,
        )
        sb.table(TABLE).update({"error_message": ""}).eq("id", backtest_id).eq(
            "user_id", USER_ID
        ).execute()

        row = await service.get_backtest(USER, backtest_id)
        assert row is not None
        for column in EVIDENCE_COLUMNS:
            assert column in row, (
                f"{TABLE}.{column} did not survive a write-then-read under definition "
                f"{base} plus 006; Requirement 3.4 requires it to be persisted and "
                f"readable"
            )
        assert row["version_id"] == VERSION_ID
        assert row["engine_version"] == "1.0.0"
        assert row["schema_version"] == "2.0"
        assert row["dag_hash"] == "cafebabe12345678"
        assert row["dataset_checksum"]
        assert row["final_capital"] == 11234.5
        assert row["executed_bar_count"] == BAR_COUNT
        assert row["completed_at"] is not None
        assert row["error_message"] == ""

    def test_006_guards_the_bar_count_and_the_column_shapes(self):
        """The two safeguards task 11.1 owes this file's premise.

        ``ADD COLUMN IF NOT EXISTS`` is silent about a pre-existing column of a different
        type, so a shape assertion is what makes "the column is there" mean "the column is
        usable"; and ``chk_sb_executed_bar_count`` is what keeps a stored count a count.
        """
        _migration_006_columns()
        sql = MIGRATION_006.read_text(encoding="utf-8", errors="replace").lower()
        assert "chk_sb_executed_bar_count" in sql
        assert "information_schema.columns" in sql, (
            "no column-shape assertion found; ADD COLUMN IF NOT EXISTS would leave a "
            "wrongly typed pre-existing column in place, silently"
        )
        assert "drop table" not in sql and "drop column" not in sql, (
            "006 is additive; it drops nothing"
        )
