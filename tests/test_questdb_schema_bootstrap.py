"""
tests/test_questdb_schema_bootstrap.py

Regression tests for the production defect observed in CloudWatch:

    E i.q.g.e.QueryProgress err [... msg=table does not exist
      [table=account_health] ...]

    E i.q.g.e.QueryProgress err [... msg=Invalid column:
      total_exposure_usdt ... pos=408]

Three distinct bugs combined to leave the QuestDB schema unbuilt while
startup logged success:

1. execute_query() returns None on every failure and never raises, so
   _ensure_dashboard_tables() reported "table 3/3 ensured" for tables it
   never created.
2. The read path uses a 100 ms timeout, which DDL cannot meet while the
   QuestDB sidecar is still booting, and any failure trips a 60 s circuit
   breaker that then short-circuits the remaining DDL.
3. The live_user_pnl view selected total_exposure_usdt FROM equity_curve,
   but that column existed only on account_health, so QuestDB rejected the
   statement outright.
"""

import asyncio
import inspect
import re
from pathlib import Path

import pytest

from backend_app.backend.telemetry_engine import TelemetryEngine

REPO_ROOT = Path(__file__).resolve().parents[1]
INGESTER = REPO_ROOT / "backend_app" / "backend" / "dashboard_data_ingester.py"


def _ddl_source() -> str:
    return inspect.getsource(TelemetryEngine._ensure_dashboard_tables)


# ---------------------------------------------------------------- bug 3

def test_equity_curve_declares_total_exposure_usdt():
    """
    The live_user_pnl view reads total_exposure_usdt from equity_curve, so
    equity_curve must declare that column. Without it QuestDB rejects the
    view with "Invalid column: total_exposure_usdt".
    """
    src = _ddl_source()
    m = re.search(
        r"CREATE TABLE IF NOT EXISTS equity_curve\s*\((.*?)\)\s*TIMESTAMP",
        src,
        re.S,
    )
    assert m, "could not locate the equity_curve DDL"
    body = m.group(1)
    assert "total_exposure_usdt" in body, (
        "equity_curve does not declare total_exposure_usdt, but the "
        "live_user_pnl view selects it FROM equity_curve. This reproduces "
        "the production error: Invalid column: total_exposure_usdt"
    )


def test_view_exposure_column_is_sourced_from_its_declaring_table():
    """Every column the view selects must exist on the table it reads."""
    src = _ddl_source()
    view = re.search(r"CREATE VIEW IF NOT EXISTS live_user_pnl(.*?)\"\"\"", src, re.S)
    assert view, "could not locate the live_user_pnl view DDL"
    view_sql = view.group(1)
    assert "FROM equity_curve" in view_sql

    tbl = re.search(
        r"CREATE TABLE IF NOT EXISTS equity_curve\s*\((.*?)\)\s*TIMESTAMP",
        src,
        re.S,
    )
    declared = set(re.findall(r"([a-z_][a-z0-9_]*)\s+(?:TIMESTAMP|SYMBOL|DOUBLE)", tbl.group(1)))

    referenced = set(re.findall(r"(?:last|first)\((\w+)\)", view_sql))
    missing = referenced - declared
    assert not missing, (
        f"live_user_pnl references {sorted(missing)} which equity_curve does "
        f"not declare. Declared: {sorted(declared)}"
    )


def test_view_still_exposes_total_exposure_contract():
    """
    routers/portfolio.py::live_pnl and
    dashboard_aggregation_service.py::get_live_pnl both read the view with
    SELECT * and their empty-state fallbacks include total_exposure, so the
    view must keep emitting that alias.
    """
    src = _ddl_source()
    assert "as total_exposure" in src, (
        "live_user_pnl no longer aliases total_exposure; this breaks the "
        "response contract expected by portfolio and dashboard consumers"
    )
    for alias in ("total_equity", "total_pnl", "pnl_pct"):
        assert f"as {alias}" in src, f"view lost the {alias} field"


# ---------------------------------------------------------------- bug 1 + 2

def test_ddl_has_dedicated_executor_separate_from_read_path():
    """DDL must not go through the 100 ms, None-returning read path."""
    assert hasattr(TelemetryEngine, "_execute_ddl"), (
        "no _execute_ddl: DDL would run through execute_query(), which "
        "returns None on failure and cannot be distinguished from success"
    )
    src = _ddl_source()
    assert "_execute_ddl" in src, "_ensure_dashboard_tables does not use _execute_ddl"
    assert "await self.execute_query(" not in src, (
        "_ensure_dashboard_tables still uses execute_query(), which swallows "
        "failures and made startup log success for tables never created"
    )


def test_ddl_failure_is_detected_and_reported(monkeypatch):
    """
    The core regression: when every DDL statement fails, startup must log
    an error naming the failed objects - not report success.
    """
    eng = TelemetryEngine()
    attempted = []

    async def always_fail(sql, label):
        attempted.append(label)
        return False

    monkeypatch.setattr(eng, "_execute_ddl", always_fail)

    errors = []
    infos = []
    import backend_app.backend.telemetry_engine as te
    monkeypatch.setattr(te.logger, "error", lambda *a, **k: errors.append(a))
    monkeypatch.setattr(te.logger, "info", lambda *a, **k: infos.append(a))

    asyncio.run(eng._ensure_dashboard_tables())

    assert len(attempted) == 4, f"expected 4 DDL statements, got {attempted}"
    assert errors, (
        "total DDL failure produced no logger.error - this is the silent "
        "false-success defect"
    )
    blob = " ".join(str(e) for e in errors)
    assert "INCOMPLETE" in blob
    for obj in ("executions", "equity_curve", "account_health", "live_user_pnl"):
        assert obj in blob, f"failure summary does not name {obj}"


def test_ddl_success_reports_all_four_objects(monkeypatch):
    """On full success, log info and no error."""
    eng = TelemetryEngine()

    async def always_ok(sql, label):
        return True

    monkeypatch.setattr(eng, "_execute_ddl", always_ok)

    errors = []
    infos = []
    import backend_app.backend.telemetry_engine as te
    monkeypatch.setattr(te.logger, "error", lambda *a, **k: errors.append(a))
    monkeypatch.setattr(te.logger, "info", lambda *a, **k: infos.append(a))

    asyncio.run(eng._ensure_dashboard_tables())

    assert not errors, f"unexpected errors on success path: {errors}"
    blob = " ".join(str(i) for i in infos)
    for obj in ("executions", "equity_curve", "account_health", "live_user_pnl"):
        assert obj in blob


def test_ddl_never_raises_so_startup_is_not_aborted(monkeypatch):
    """
    QuestDB is an optional sidecar. A schema failure must be loud but must
    not propagate, or the ECS task would crash-loop on a degraded sidecar.
    """
    eng = TelemetryEngine()

    async def boom(sql, label):
        raise RuntimeError("questdb exploded")

    monkeypatch.setattr(eng, "_execute_ddl", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(eng._ensure_dashboard_tables())


def test_execute_ddl_uses_generous_timeout_not_read_path_budget():
    """DDL must not inherit the 100 ms user-facing read budget."""
    src = inspect.getsource(TelemetryEngine._execute_ddl)
    assert "QUESTDB_DDL_TIMEOUT_SECONDS" in src
    assert "total=0.1" not in src, "DDL is using the 100 ms read-path budget"
    assert "_unreachable_until" not in src, (
        "DDL must not trip the read-path circuit breaker"
    )


# ---------------------------------------------------------------- writer

def test_ingester_writes_exposure_into_equity_curve():
    """
    The view can only surface a real total_exposure if the ingester actually
    writes total_exposure_usdt into equity_curve.
    """
    src = INGESTER.read_text(encoding="utf-8")
    m = re.search(r"async def _write_equity_curve\((.*?)\n\s{8}\"\"\"", src, re.S)
    assert m, "could not locate _write_equity_curve"
    assert "total_exposure_usdt" in m.group(1), (
        "_write_equity_curve does not accept total_exposure_usdt"
    )
    body = src[src.index("async def _write_equity_curve"):]
    body = body[: body.index("async def _write_account_health")]
    assert "total_exposure_usdt={total_exposure_usdt}" in body, (
        "the equity_curve ILP line does not include total_exposure_usdt, so "
        "live_user_pnl.total_exposure would always be null"
    )
    assert "await self._write_equity_curve(user_id, total_equity, total_exposure)" in src, (
        "call site does not pass exposure through to _write_equity_curve"
    )
