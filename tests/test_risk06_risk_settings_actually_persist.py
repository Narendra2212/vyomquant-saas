"""RISK-06 / RISK-07 — a risk setting a trader is told was saved must be stored.

WHAT WAS BROKEN
---------------
``PUT /api/risk/settings`` had never persisted anything in production. The handler
assembles one record and upserts it whole, and one of its seven keys --
``circuit_breaker_armed`` -- was declared by no migration and existed in no database.
PostgREST rejects the whole statement on an undefined column, so the upsert failed with
42703 on every request. Because the upsert is atomic, ``max_daily_loss``,
``max_positions``, ``max_leverage`` and ``kill_switches`` never landed either.

The failure was invisible from inside the application for two reasons, and either alone
would have hidden it:

  1. ``except Exception as e: logger.warning("Database persist ... skipped")`` -- a
     WARNING, not an error, on a financial control.
  2. The handler then returned ``{"status": "ok", "message": "Risk settings saved
     successfully"}`` unconditionally, and had already written the record to the
     process-local ``_user_risk_settings`` dict BEFORE attempting the upsert. So the
     worker that served the request read back exactly what the trader submitted. Another
     worker, or the same worker after a restart, read the old value.

Measured against production on 2026-11-18 before the fix::

    insert into public.risk_settings (..., circuit_breaker_armed, ...)
      -> 42703  column "circuit_breaker_armed" of relation "risk_settings" does not exist

``PUT /strategy-limits`` had the same shape with none of the ambiguity: it wrote ONLY to
``_user_strategy_limits`` and never touched a table at all, while ``GET /strategy-limits``
reads ``public.strategy_limits``.

WHAT THESE TESTS PIN
--------------------
That a persistence failure REFUSES (503) instead of reporting success, that the process
store is not mutated when the database write failed, and that every column the handler
names is declared by a migration -- the last being the check whose absence let this ship.

WHY A NEW DRIFT CHECK HERE
--------------------------
``tests/test_schema_table_reference_drift.py`` exists to catch exactly this class of
defect and its ``KNOWN_UNDECLARED_COLUMN_DEFECTS`` register is empty, yet this defect was
live. It scans for literal ``.table("X").insert({"col": ...})`` dict literals; this
handler builds ``updated_record`` as a variable first and upserts the variable, so the
association between the table and the column names is invisible to a literal scan. The
test below closes that gap for this call site by resolving the variable through the AST.
"""
from __future__ import annotations

import ast
import pathlib
import re
import textwrap

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock, patch

from backend_app.core.dependencies import get_current_user
from backend_app.main import app
from backend_app.routers import risk as risk_router

REPO = pathlib.Path(__file__).resolve().parents[1]
RISK_ROUTER = REPO / "backend_app" / "routers" / "risk.py"

client = TestClient(app)

PAYLOAD = {
    "max_daily_loss": 2000,
    "max_positions": 20,
    "max_leverage": 3,
    "circuit_breaker_armed": False,
}


def _user_with_token():
    return {"id": "risk06_user", "access_token": "valid_token"}


def _user_without_token():
    return {"id": "risk06_user_no_token"}


def _sb_that_raises(exc: Exception):
    sb = Mock()
    sb.table.return_value.upsert.return_value.execute.side_effect = exc
    return AsyncMock(return_value=sb)


def _sb_that_succeeds():
    sb = Mock()
    sb.table.return_value.upsert.return_value.execute.return_value = Mock(data=[{"ok": True}])
    return AsyncMock(return_value=sb), sb


@pytest.fixture
def configured_store(monkeypatch):
    """A deployment that HAS a configuration store attached.

    The suite runs with `SUPABASE_URL` / `SUPABASE_ANON_KEY` unset, which the handler
    correctly reads as "deliberately unconfigured" and answers in-memory -- the DEV_MODE
    contract that `core/dependencies.get_supabase` establishes. Every test about REFUSING
    has to opt into a configured deployment first, or it is asserting against the dev
    path and proves nothing about production.
    """
    monkeypatch.setenv("SUPABASE_URL", "https://stub.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "stub-anon-key")
    yield


# ─────────────────────────────────────────────────────────────────────────────────
# The refusal
# ─────────────────────────────────────────────────────────────────────────────────


class TestSaveRefusesRatherThanLying:
    @patch("backend_app.routers.risk._sb")
    def test_upsert_failure_returns_503_not_a_success_message(self, mock_sb, configured_store):
        """The exact production failure: 42703 on an undeclared column."""
        mock_sb.side_effect = _sb_that_raises(
            Exception('column "circuit_breaker_armed" of relation "risk_settings" '
                      'does not exist')
        )
        app.dependency_overrides[get_current_user] = _user_with_token
        try:
            response = client.put("/api/risk/settings", json=PAYLOAD)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 503, (
            "a failed risk-settings write must refuse. Before RISK-06 this returned 200 "
            f"with 'Risk settings saved successfully'. Got {response.status_code}."
        )
        detail = response.json()["detail"]
        assert detail["error"] == "RISK_SETTINGS_PERSIST_FAILED"
        body = response.text.lower()
        assert "saved successfully" not in body

    @patch("backend_app.routers.risk._sb")
    def test_process_store_keeps_the_old_value_when_the_write_failed(self, mock_sb, configured_store):
        """The divergence that made the defect invisible: this worker must not serve a
        value the database does not hold.

        Asserted on the VALUE, not on the key's presence: ``get_user_risk_settings_store``
        seeds a defaults row on first read and the handler reads it before saving, so the
        key always exists by the time the upsert runs. What must not happen is the
        SUBMITTED figure replacing it.
        """
        uid = "risk06_user"
        risk_router._user_risk_settings.pop(uid, None)
        before = dict(risk_router.get_user_risk_settings_store(uid))
        mock_sb.side_effect = _sb_that_raises(Exception("connection refused"))

        app.dependency_overrides[get_current_user] = _user_with_token
        try:
            response = client.put("/api/risk/settings", json=PAYLOAD)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 503
        after = risk_router._user_risk_settings[uid]
        assert after["max_daily_loss"] == before["max_daily_loss"], (
            "the in-memory store took the submitted max_daily_loss despite the database "
            f"write failing ({before['max_daily_loss']} -> {after['max_daily_loss']}), so "
            "this worker would read back a limit that was never saved"
        )
        assert after["max_daily_loss"] != PAYLOAD["max_daily_loss"]
        assert after["max_positions"] == before["max_positions"]

    @patch("backend_app.routers.risk._sb")
    def test_token_present_but_no_client_is_a_broken_dependency_not_an_unconfigured_one(self, mock_sb, configured_store):
        mock_sb.side_effect = AsyncMock(return_value=None)
        app.dependency_overrides[get_current_user] = _user_with_token
        try:
            response = client.put("/api/risk/settings", json=PAYLOAD)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 503
        assert response.json()["detail"]["error"] == (
            "RISK_SETTINGS_PERSISTENCE_UNAVAILABLE"
        )

    @patch("backend_app.routers.risk._sb")
    def test_successful_save_reports_persisted_true(self, mock_sb, configured_store):
        factory, sb = _sb_that_succeeds()
        mock_sb.side_effect = factory
        app.dependency_overrides[get_current_user] = _user_with_token
        try:
            response = client.put("/api/risk/settings", json=PAYLOAD)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["persisted"] is True
        sb.table.assert_any_call("risk_settings")

    def test_tokenless_save_does_not_claim_durability(self):
        """The in-memory store is the deliberate behaviour here. It must still not print
        'saved successfully'."""
        app.dependency_overrides[get_current_user] = _user_without_token
        try:
            response = client.put("/api/risk/settings", json=PAYLOAD)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is False
        assert "will not survive a restart" in body["message"]

    def test_unconfigured_deployment_is_not_treated_as_a_broken_one(self):
        """DEV_MODE with no SUPABASE_URL/ANON_KEY is the documented unconfigured state.

        The first version of this fix keyed the refusal on token presence alone and
        answered 503 here, which broke three existing lifecycle tests. Keeping that case
        pinned, because the distinction -- deliberately unconfigured vs. broken -- is the
        whole design of the refusal.
        """
        assert not risk_router._persistence_configured(), (
            "this test asserts the unconfigured path; the suite is not supposed to have "
            "SUPABASE_URL/SUPABASE_ANON_KEY set"
        )
        app.dependency_overrides[get_current_user] = _user_with_token
        try:
            response = client.put("/api/risk/settings", json=PAYLOAD)
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["persisted"] is False
        assert "saved successfully" not in body["message"]


# ─────────────────────────────────────────────────────────────────────────────────
# RISK-07 — per-strategy limits reach the table the GET reads
# ─────────────────────────────────────────────────────────────────────────────────


class TestStrategyLimitsPersist:
    """These patch `get_service_supabase_async`, NOT `_sb`.

    `public.strategy_limits` grants `authenticated` SELECT only, so the write has to go
    through the service-role client or fail with 42501 in production -- which is how CI's
    `sec22_authenticated_write_inventory` analyzer caught the first version of this fix.
    Patching `_sb` here would test a client the handler no longer uses.
    """

    @patch("backend_app.routers.risk.get_service_supabase_async")
    def test_batch_update_upserts_on_the_tables_unique_key(self, mock_sb, configured_store):
        factory, sb = _sb_that_succeeds()
        mock_sb.side_effect = factory
        app.dependency_overrides[get_current_user] = _user_with_token
        try:
            response = client.put(
                "/api/risk/strategy-limits",
                json={"limits": [{"strategy_id": "s1", "max_position_size": 250}]},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.json()["persisted"] is True
        sb.table.assert_any_call("strategy_limits")
        kwargs = sb.table.return_value.upsert.call_args.kwargs
        assert kwargs.get("on_conflict") == "user_id,strategy_id", (
            "the upsert must conflict on uq_strategy_limits (user_id, strategy_id), "
            f"got {kwargs.get('on_conflict')!r}"
        )

    @patch("backend_app.routers.risk.get_service_supabase_async")
    def test_single_update_refuses_when_the_write_fails(self, mock_sb, configured_store):
        mock_sb.side_effect = _sb_that_raises(Exception("connection refused"))
        app.dependency_overrides[get_current_user] = _user_with_token
        try:
            response = client.put(
                "/api/risk/strategy-limits/s1", json={"max_position_size": 250}
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 503
        assert response.json()["detail"]["error"] == "STRATEGY_LIMITS_PERSIST_FAILED"

    @patch("backend_app.routers.risk.get_service_supabase_async")
    def test_no_service_role_client_refuses_rather_than_falling_back_to_the_caller(
        self, mock_sb, configured_store
    ):
        """A missing SUPABASE_SERVICE_ROLE_KEY must not silently degrade to the caller's
        client -- that client cannot write this table, so the save would fail anyway, and
        falling back would re-grow the browser-role write surface."""
        mock_sb.side_effect = AsyncMock(return_value=None)
        app.dependency_overrides[get_current_user] = _user_with_token
        try:
            response = client.put(
                "/api/risk/strategy-limits/s1", json={"max_position_size": 250}
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 503
        assert response.json()["detail"]["error"] == (
            "STRATEGY_LIMITS_PERSISTENCE_UNAVAILABLE"
        )

    def test_row_builder_omits_keys_the_caller_did_not_supply(self):
        """A partial update must not overwrite unmentioned columns with model defaults."""
        row = risk_router._strategy_limit_row("u", "s1", {"max_position_size": 250})
        assert row["user_id"] == "u"
        assert row["strategy_id"] == "s1"
        assert row["max_position_size"] == 250
        for absent in ("max_daily_trades", "allowed_symbols", "max_drawdown_pct", "enabled"):
            assert absent not in row, (
                f"{absent} was not supplied by the caller but would be written anyway, "
                "resetting whatever is stored"
            )

    def test_row_builder_never_writes_columns_this_endpoint_has_no_field_for(self):
        row = risk_router._strategy_limit_row(
            "u", "s1",
            {"max_position_size": 1, "max_daily_trades": 2, "allowed_symbols": ["BTC"],
             "max_drawdown_pct": 0.1, "enabled": True,
             # these exist in the table with defaults and must not be touched
             "max_allocation_usd": 999, "max_leverage": 50, "is_active": False},
        )
        for forbidden in ("max_allocation_usd", "max_leverage", "is_active"):
            assert forbidden not in row


# ─────────────────────────────────────────────────────────────────────────────────
# The drift check that was missing
# ─────────────────────────────────────────────────────────────────────────────────


def _declared_columns_for(table: str) -> set[str]:
    """Every column name any migration or the provisioning snapshot associates with
    ``table``.

    Deliberately permissive -- it unions CREATE TABLE bodies, ALTER TABLE ... ADD COLUMN
    and the recorded snapshot, and does not try to model ordering. A column named by the
    application and found in NONE of those is the defect this catches.
    """
    found: set[str] = set()
    roots = [REPO / "migrations", REPO / "backend_app" / "migrations"]
    for root in roots:
        if not root.exists():
            continue
        for sql in root.rglob("*.sql"):
            text = sql.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(
                rf"ALTER\s+TABLE\s+(?:public\.)?{table}\b(.*?);", text,
                re.IGNORECASE | re.DOTALL,
            ):
                for col in re.finditer(
                    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)",
                    m.group(1), re.IGNORECASE,
                ):
                    found.add(col.group(1).lower())
            for m in re.finditer(
                rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?{table}\s*\((.*?)\n\s*\);",
                text, re.IGNORECASE | re.DOTALL,
            ):
                for line in m.group(1).splitlines():
                    line = line.strip()
                    col = re.match(r"([a-z_][a-z0-9_]*)\s+[a-z]", line, re.IGNORECASE)
                    if col and col.group(1).upper() not in (
                        "CONSTRAINT", "UNIQUE", "PRIMARY", "FOREIGN", "CHECK", "EXCLUDE"
                    ):
                        found.add(col.group(1).lower())

    snapshot = REPO / "staging" / "provisioning_snapshot.json"
    if snapshot.exists():
        import json

        blob = json.loads(snapshot.read_text(encoding="utf-8", errors="replace"))

        def walk(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == table and isinstance(value, (list, dict)):
                        if isinstance(value, dict):
                            found.update(str(k).lower() for k in value)
                        else:
                            for entry in value:
                                if isinstance(entry, str):
                                    found.add(entry.lower())
                                elif isinstance(entry, dict):
                                    for candidate in ("column_name", "name", "column"):
                                        if candidate in entry:
                                            found.add(str(entry[candidate]).lower())
                    walk(value)
            elif isinstance(node, list):
                for entry in node:
                    walk(entry)

        walk(blob)
    return found


def _upsert_record_keys(variable: str) -> set[str]:
    """Keys of a dict literal assigned to ``variable`` in routers/risk.py."""
    tree = ast.parse(RISK_ROUTER.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == variable:
                    for key in node.value.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            keys.add(key.value.lower())
    return keys


class TestEveryColumnTheHandlerNamesIsDeclared:
    """The check whose absence let a phantom column ship.

    ``test_schema_table_reference_drift.py`` resolves literal ``.insert({...})`` dicts.
    This handler upserts a VARIABLE, so that scan sees no columns at all for this table.
    """

    def test_risk_settings_upsert_record_has_no_undeclared_column(self):
        sent = _upsert_record_keys("updated_record")
        assert sent, (
            "could not find the `updated_record` dict literal in routers/risk.py -- if "
            "the handler was restructured, point this test at the new variable rather "
            "than deleting it"
        )
        declared = _declared_columns_for("risk_settings")
        assert declared, "found no declaration of risk_settings in any migration"

        undeclared = sorted(sent - declared)
        assert not undeclared, (
            "routers/risk.py upserts column(s) public.risk_settings does not declare: "
            f"{undeclared}. The upsert is ATOMIC, so any one of these makes the WHOLE "
            "save fail with 42703 -- which is how circuit_breaker_armed stopped every "
            "risk setting from persisting while the endpoint reported success. Declare "
            "the column in a migration (see 075) or stop sending it."
        )

    def test_circuit_breaker_armed_is_declared_by_migration_075(self):
        path = (REPO / "backend_app" / "migrations"
                / "075_risk06_risk_settings_circuit_breaker_armed.sql")
        assert path.exists(), "migration 075 is missing"
        sql = path.read_text(encoding="utf-8")
        assert "circuit_breaker_armed boolean NOT NULL DEFAULT true" in sql, (
            "the column must be NOT NULL DEFAULT true: true = armed, so a pre-existing "
            "row reads as protected and 'unset' never becomes a third state"
        )
        assert "ADD COLUMN IF NOT EXISTS" in sql, "must be idempotent"
        rollback = (REPO / "backend_app" / "migrations" / "rollback"
                    / "075_risk06_rollback.sql")
        assert rollback.exists(), "every migration needs a rollback"

    def test_075_postcondition_checks_the_whole_handler_record(self):
        """Asserting only the one column would leave the next phantom to be found in
        production again."""
        sql = (REPO / "backend_app" / "migrations"
               / "075_risk06_risk_settings_circuit_breaker_armed.sql"
               ).read_text(encoding="utf-8")
        for column in _upsert_record_keys("updated_record"):
            assert column in sql, (
                f"075's postcondition does not mention {column!r}, so a future removal "
                "of that column would pass the migration and fail the save"
            )


def _strip_comments(source: str) -> str:
    """Source with ``#`` comments removed, line structure preserved.

    Both assertions below search for a code shape. The fix's own comments QUOTE the
    defective code they replaced -- deliberately, because that is what makes the diff
    legible later -- so a naive search matches the explanation and fails. Same approach as
    the frontend's colour-literal guard, which strips comments before counting for exactly
    this reason: prose about a defect must not read as the defect.
    """
    out = []
    for line in source.splitlines():
        in_single = in_double = False
        cut = None
        for i, ch in enumerate(line):
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "#" and not in_single and not in_double:
                cut = i
                break
        out.append(line if cut is None else line[:cut])
    return "\n".join(out)


class TestTheSwallowIsGone:
    def test_no_warning_level_swallow_remains_on_the_risk_settings_write(self):
        code = _strip_comments(RISK_ROUTER.read_text(encoding="utf-8"))
        assert "Database persist for risk_settings skipped" not in code, (
            "the swallowed WARNING is back. A failed write to risk configuration must "
            "refuse, not log at WARNING and return success."
        )

    def test_the_handler_persists_before_touching_the_process_store(self):
        """Order matters: writing memory first is what made the failure invisible."""
        source = RISK_ROUTER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
            and n.name == "update_risk_settings"
        )
        body = _strip_comments(textwrap.dedent(ast.get_source_segment(source, func) or ""))
        upsert_at = body.find('table("risk_settings").upsert')
        store_at = body.find("_user_risk_settings[uid] = updated_record")
        assert upsert_at != -1, "the risk_settings upsert is gone from the handler"
        assert store_at != -1, "the process-store write is gone from the handler"
        assert upsert_at < store_at, (
            "the process-local store is written before the database upsert, so a failed "
            "save still changes what this worker reports"
        )
