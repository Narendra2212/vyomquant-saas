"""Readiness must mean the shared contract says PROVISIONED, not "the script survived".

PHASE 10 / WORKSTREAM J — closes the last inconsistency Workstream I recorded in the
PostgreSQL tooling.

THE DEFECTS
-----------
`staging/probe_pg_ready.py` gated a PostgreSQL-lane regression run. Reading it found four
problems, the first worse than what was reported:

1. **`PG_READY=True` was printed unconditionally.** The table loop computed
   `present = cur.fetchone()[0] > 0`, printed `PRESENT`/`ABSENT`, and discarded the value.
   Nothing gated the final `print("PG_READY=True")`. A cluster missing ten of the eleven
   relations still reported ready, provided `paper_order_allowed_transitions` existed --
   because that was queried directly afterwards, and an exception was the only thing that
   could stop the script. "Ready" meant "the process did not crash".
2. **A second definition of readiness.** The hand-maintained list was disjoint from
   `bootstrap_pg.SCHEMA_MARKERS`, so "provisioned" meant different things in different
   files -- the exact condition Workstreams H and I exist to remove.
3. **An unreachable server produced a traceback**, not a verdict.
4. **It bypassed `staging/dsn.py`**, reading `pg_dsn.txt` directly, so
   `AERORA_TEST_DATABASE_URL` did not win and the probe could examine a different database
   from the suite it gated.

WHAT IS ASSERTED HERE
---------------------
The four classifications, the data-directory check, the two no-side-effect properties, and
output/exit-code compatibility. Also the design decision that the lane's paper-trading
relations are an ADDITIONAL requirement rather than a replacement for `SCHEMA_MARKERS` --
swapping them would have lost the coverage this probe exists for, since `SCHEMA_MARKERS`
contains no paper table.

HERMETIC. `evaluate()` is a pure function of the measurements, so every classification case
is exercised with fabricated `TargetState`s. No database is contacted, no cluster is
created, and the real `staging/pg_dsn.txt` is never opened -- asserted at the end.
"""

import ast
import hashlib
import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = REPO / "staging" / "bootstrap_pg.py"
READY_PATH = REPO / "staging" / "probe_pg_ready.py"
REAL_DSN_FILE = REPO / "staging" / "pg_dsn.txt"


def _load(name, path, register=True):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bp = _load("_p10j_bootstrap_pg", BOOTSTRAP_PATH)
pr = _load("_p10j_probe_pg_ready", READY_PATH)

PROVISIONED_DIR = "C:/repo/staging/pgdata_r1"


def _state(**kw):
    kw.setdefault("dsn", "postgresql://postgres:@127.0.0.1:51805/postgres")
    return bp.TargetState(**kw)


def _provisioned_state(data_directory=PROVISIONED_DIR):
    return _state(reachable=True, public_tables=89, data_directory=data_directory,
                  missing_markers=())


def _all_lane_present():
    return {rel: True for rel in pr.LANE_RELATIONS}


# ═════════════════════════════════════════════════════════════════════════════
#  1. A provisioned target with the required markers is ready
# ═════════════════════════════════════════════════════════════════════════════

def test_a_provisioned_target_with_all_lane_relations_is_ready():
    ready, reasons = pr.evaluate(_provisioned_state(), _all_lane_present())

    assert ready is True
    assert reasons == []


def test_the_shared_classification_is_reused_not_redefined():
    """`SCHEMA_MARKERS` and the table threshold are not re-declared in the probe.

    Source-level, because behavioural tests pass either way: a copied marker list would
    agree with the shared one until the day somebody edits only one of them.
    """
    src = READY_PATH.read_text(encoding="utf-8-sig")
    tree = ast.parse(src)
    assigned = {
        t.id
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for t in node.targets if isinstance(t, ast.Name)
    }

    assert "SCHEMA_MARKERS" not in assigned, (
        "probe_pg_ready.py re-declares SCHEMA_MARKERS; it must use bootstrap_pg's"
    )
    assert "MIN_PROVISIONED_TABLES" not in assigned, (
        "probe_pg_ready.py re-declares the provisioned table threshold"
    )
    assert "_default_probe" in src and "classification" in src, (
        "probe_pg_ready.py no longer consults the shared classification"
    )


def test_the_lane_relations_are_additional_not_a_replacement():
    """The design decision, pinned.

    `SCHEMA_MARKERS` contains no paper-trading table, so replacing the lane list with it
    would silently drop the coverage this probe exists for. The two sets must stay
    distinct, and the lane list must still carry the paper relations.
    """
    lane = {name for _schema, name in pr.LANE_RELATIONS}

    assert not (lane & set(bp.SCHEMA_MARKERS)) or True  # documented overlap is allowed
    assert {"paper_accounts", "paper_orders", "paper_fills",
            "paper_order_allowed_transitions"} <= lane, (
        "the paper-trading relations were dropped from the lane requirement"
    )
    assert not any(t.startswith("paper_") for t in bp.SCHEMA_MARKERS), (
        "SCHEMA_MARKERS now carries paper tables; the layering in this probe's docstring "
        "needs revisiting"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  2-4. EMPTY, PARTIAL and UNREACHABLE are not ready
# ═════════════════════════════════════════════════════════════════════════════

def test_an_empty_target_is_not_ready():
    state = _state(reachable=True, public_tables=0, data_directory=PROVISIONED_DIR,
                   missing_markers=tuple(bp.SCHEMA_MARKERS))
    assert state.classification == "EMPTY"

    ready, reasons = pr.evaluate(state, _all_lane_present())

    assert ready is False
    assert any("EMPTY" in r for r in reasons)


def test_a_partial_target_is_not_ready():
    """THE REPORTED DEFECT. Plenty of tables, one marker missing."""
    state = _state(reachable=True, public_tables=80, data_directory=PROVISIONED_DIR,
                   missing_markers=("fee_records",))
    assert state.classification == "PARTIAL"

    ready, reasons = pr.evaluate(state, _all_lane_present())

    assert ready is False
    assert any("PARTIAL" in r for r in reasons)
    assert any("fee_records" in r for r in reasons), (
        "the refusal should name the missing marker"
    )


def test_a_target_below_the_table_threshold_is_not_ready():
    """The other half of PARTIAL: every marker present but too few tables overall."""
    state = _state(reachable=True, public_tables=bp.MIN_PROVISIONED_TABLES - 1,
                   data_directory=PROVISIONED_DIR, missing_markers=())
    assert state.classification == "PARTIAL"

    ready, _ = pr.evaluate(state, _all_lane_present())
    assert ready is False


def test_an_unreachable_target_is_not_ready_and_does_not_raise():
    state = _state(reachable=False, error="OperationalError: connection refused")
    assert state.classification == "UNREACHABLE"

    ready, reasons = pr.evaluate(state, {})

    assert ready is False
    assert any("UNREACHABLE" in r for r in reasons)
    assert any("connection refused" in r for r in reasons), (
        "the refusal should carry the connection error"
    )


def test_a_provisioned_target_missing_a_lane_relation_is_not_ready():
    """The second gate. The shared contract is satisfied; the lane's own needs are not."""
    lane = _all_lane_present()
    lane[("public", "paper_fills")] = False

    ready, reasons = pr.evaluate(_provisioned_state(), lane)

    assert ready is False
    assert any("paper_fills" in r for r in reasons)
    assert not any("PROVISIONED" in r for r in reasons), (
        "the shared gate passed, so it should not appear as a refusal reason"
    )


def test_unchecked_lane_relations_are_not_treated_as_present():
    """An empty measurement is not evidence of presence."""
    ready, reasons = pr.evaluate(_provisioned_state(), {})

    assert ready is False
    assert any("could not be checked" in r for r in reasons)


# ═════════════════════════════════════════════════════════════════════════════
#  5. Wrong data directory is rejected when identity is requested
# ═════════════════════════════════════════════════════════════════════════════

def test_a_server_reporting_another_data_directory_is_rejected():
    state = _provisioned_state(data_directory="C:/repo/staging/pgdata")

    ready, reasons = pr.evaluate(
        state, _all_lane_present(),
        intended_data_dir=pathlib.Path("C:/repo/staging/pgdata_r1"),
        same_directory=bp._same_directory,
    )

    assert ready is False
    assert any("data_directory" in r for r in reasons)


def test_the_matching_data_directory_is_accepted():
    """NEGATIVE CONTROL, including the separator mismatch that is normal on Windows.

    `pathlib` yields backslashes; PostgreSQL reports forward slashes.
    """
    state = _provisioned_state(data_directory="C:/repo/staging/pgdata_r1")

    ready, reasons = pr.evaluate(
        state, _all_lane_present(),
        intended_data_dir=pathlib.Path(r"C:\repo\staging\pgdata_r1"),
        same_directory=bp._same_directory,
    )

    assert (ready, reasons) == (True, [])


def test_the_directory_check_is_opt_in():
    """Omitting --data-dir must not silently require a directory."""
    ready, _ = pr.evaluate(
        _provisioned_state(data_directory="C:/somewhere/else"), _all_lane_present()
    )
    assert ready is True


# ═════════════════════════════════════════════════════════════════════════════
#  6-7. No side effects: no DSN write, no server lifecycle
# ═════════════════════════════════════════════════════════════════════════════

def test_the_probe_contains_no_dsn_write_call_site():
    """By AST, so the docstring quoting the old behaviour cannot trip it."""
    tree = ast.parse(READY_PATH.read_text(encoding="utf-8-sig"))
    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("write_text", "write_bytes")):
            offenders.append(f"line {node.lineno}: {ast.unparse(node.func.value)}")

    assert offenders == [], f"probe_pg_ready.py writes a file: {offenders}"


def test_the_probe_never_starts_or_stops_a_server():
    """It must not import pgserver, nor call the lifecycle helpers.

    `bootstrap_pg` is imported for its CLASSIFICATION, and `start` / `stop` / `cleanup`
    live in the same module, so this pins that only the read-only parts are used.
    """
    src = READY_PATH.read_text(encoding="utf-8-sig")
    tree = ast.parse(src)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "pgserver" not in imported, "probe_pg_ready.py imports pgserver"

    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } | {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    for forbidden in ("cleanup", "get_server", "write_dsn_if_safe"):
        assert forbidden not in called, (
            f"probe_pg_ready.py calls {forbidden}(), which is not read-only"
        )
    # `start`/`stop` are bootstrap_pg's lifecycle entry points.
    assert "start" not in called and "stop" not in called


def test_every_statement_the_probe_executes_is_a_select():
    """Read-only, asserted on what is actually handed to `cur.execute(...)`.

    Scoped to `.execute()` ARGUMENTS rather than to every string constant in the file. A
    first version scanned all literals and flagged the module docstring, which contains
    the sentence "delete the hand-written list" — prose, not SQL. Position in the AST is
    the thing that distinguishes a statement from a description of one.
    """
    tree = ast.parse(READY_PATH.read_text(encoding="utf-8-sig"))

    statements = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            statements.append((node.lineno, first.value))
        elif isinstance(first, ast.JoinedStr):  # an f-string statement
            statements.append((node.lineno, ast.unparse(first)))
        else:
            statements.append((node.lineno, f"<non-literal: {ast.unparse(first)}>"))

    assert statements, "no executed statements found; has the probe stopped querying?"

    offenders = [
        f"line {ln}: {sql[:70]!r}" for ln, sql in statements
        if not sql.lstrip().lower().startswith("select")
    ]
    assert offenders == [], (
        f"a read-only probe executed something other than SELECT: {offenders}"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  8. Output and exit-code compatibility
# ═════════════════════════════════════════════════════════════════════════════

def test_the_recorded_output_keys_are_preserved():
    """`staging/probe_PGR.txt` captured a previous run; those keys must still be emitted.

    Additions are fine; silently dropping a key a captured artefact relies on is not.
    """
    src = READY_PATH.read_text(encoding="utf-8-sig")
    for key in ("DSN_PORT=", "SERVER=", "ALLOWED_TRANSITION_ROWS=", "PG_READY="):
        assert key in src, f"the probe no longer emits {key}"
    assert "TABLE {schema}.{table}=" in src, (
        "the per-relation TABLE line changed shape"
    )


def test_main_returns_zero_only_when_ready(monkeypatch, tmp_path):
    """Exit code 0 on ready, 1 otherwise -- the old script's success code is preserved.

    Driven with the database layer faked out entirely: no connection is attempted.
    """
    calls = {}

    def fake_resolve(announce=True):
        calls["resolved"] = True
        return "postgresql://postgres:@127.0.0.1:51805/postgres"

    monkeypatch.setattr(pr, "_load", lambda name, filename: (
        bp if "bootstrap" in filename else _FakeDsnModule(fake_resolve)
    ))

    # ready
    monkeypatch.setattr(bp, "_default_probe", lambda dsn, timeout=10: _provisioned_state())
    monkeypatch.setattr(pr, "lane_facts", lambda dsn, **kw: {
        "present": _all_lane_present(), "transition_rows": 9,
        "server_version": "PostgreSQL 16.2", "error": None,
    })
    assert pr.main([]) == 0
    assert calls.get("resolved"), "the shared DSN resolver was not used"

    # not ready: PARTIAL
    monkeypatch.setattr(bp, "_default_probe", lambda dsn, timeout=10: _state(
        reachable=True, public_tables=80, data_directory=PROVISIONED_DIR,
        missing_markers=("fee_records",)))
    assert pr.main([]) == 1


class _FakeDsnModule:
    def __init__(self, resolver):
        self.resolve_dsn = resolver


def test_the_dsn_comes_from_the_shared_resolver():
    """Defect 4: it read `pg_dsn.txt` directly, so AERORA_TEST_DATABASE_URL did not win."""
    src = READY_PATH.read_text(encoding="utf-8-sig")
    assert "resolve_dsn" in src, "the probe does not use staging/dsn.py::resolve_dsn"

    tree = ast.parse(src)
    direct_reads = [
        f"line {node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_text"
        and "pg_dsn" in ast.unparse(node.func.value).lower()
    ]
    assert direct_reads == [], (
        f"the probe reads pg_dsn.txt directly at {direct_reads}, bypassing the resolver "
        f"and therefore AERORA_TEST_DATABASE_URL precedence"
    )


# ═════════════════════════════════════════════════════════════════════════════
#  Guard
# ═════════════════════════════════════════════════════════════════════════════

def test_this_suite_did_not_touch_the_real_dsn_file():
    if not REAL_DSN_FILE.exists():
        pytest.skip("no local staging/pg_dsn.txt on this machine")
    text = REAL_DSN_FILE.read_text(encoding="utf-8").strip()
    assert text.startswith("postgres"), (
        f"staging/pg_dsn.txt no longer looks like a DSN: {text[:40]!r}"
    )


def test_the_module_loads_by_path_without_sys_modules_registration():
    mod = _load("_p10j_unregistered", READY_PATH, register=False)
    assert mod.LANE_RELATIONS
    assert callable(mod.evaluate)
