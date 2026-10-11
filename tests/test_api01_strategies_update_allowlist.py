"""API-01 regression: `PUT /api/strategies/{id}` enforces a column allowlist.

THE DEFECT
----------
The handler took a raw caller ``body`` dict and did ``update_payload = dict(body)``
straight into ``.table("strategies").update(...)``
(``backend_app/routers/strategies.py``). There was no column allowlist. The handler's own
source comment said so. The only field-specific guards were ``status`` (blocked from
``running``/``deployed``) and ``ml_model_path`` (containment-checked), so every other
column on ``strategies`` was caller-writable.

The ``.eq("user_id", user["id"])`` filter meant this was never cross-tenant -- a caller
could only corrupt their own row -- which is why it is a mass-assignment / integrity
defect rather than a tenant-isolation one. What it allowed a caller to write included
``user_id``, ``marketplace_listing_id``, ``is_published``, ``is_subscribed``,
``subscription_id``, ``is_read_only``, ``archived_at``, ``environment`` (the paper/live
discriminator), ``id``, ``created_at``, ``cloned_from``, ``current_version``,
``dag_hash``, ``dag_version``, ``dag_schema_version`` and ``last_signal_at``.

THE FIX
-------
``_UPDATABLE_STRATEGY_COLUMNS`` plus ``_assert_update_fields_are_mutable()``, called
before the payload is assembled so a refused request writes nothing. A non-mutable field
produces 422 ``STRATEGY_FIELD_NOT_MUTABLE`` naming the fields and echoing no values.

WHY THESE TESTS ARE STRUCTURAL RATHER THAN HTTP-DRIVEN
------------------------------------------------------
Driving the real endpoint needs an authenticated client plus a Supabase double, and the
existing router suites already cover that wiring. What actually went wrong here was the
*contents of the allowlist* and the *reachability of the guard*, so that is what these
assert: the guard function's behaviour directly, that the handler calls it, that the
allowlist excludes every dangerous column by name, and that it still includes the fields
the Builder legitimately saves. A test that only exercised one happy path over HTTP would
not have caught the original defect either.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.routers.strategies import (  # noqa: E402
    _GRAPH_PAYLOAD_KEYS,
    _NON_COLUMN_BODY_KEYS,
    _UPDATABLE_STRATEGY_COLUMNS,
    _assert_update_fields_are_mutable,
)

#: Every column on `public.strategies`, read off the live catalogue during remediation
#: (staging/rem_inspect2.txt). Hardcoded so this test states the full surface it reasons
#: about rather than depending on a database connection.
ALL_STRATEGY_COLUMNS = (
    "id", "user_id", "name", "symbol", "timeframe", "buy_logic", "sell_logic", "risk",
    "indicators", "ml_model_path", "exchange_id", "status", "created_at", "updated_at",
    "source_library_id", "backtest_result", "current_version", "environment",
    "is_active", "dag_config", "dag_hash", "dag_version", "dag_schema_version",
    "execution_order", "last_signal_at", "archived_at", "is_published",
    "marketplace_listing_id", "is_subscribed", "is_read_only", "subscription_id",
    "cloned_from",
)

#: Columns that must NEVER be caller-writable, each with the reason, so a future reader
#: can tell whether a change to the allowlist is a mistake or a decision.
FORBIDDEN_COLUMNS = {
    "id": "row identity",
    "user_id": "ownership — reassigning the row to another account",
    "created_at": "provenance",
    "updated_at": "server-maintained timestamp",
    "source_library_id": "marketplace provenance",
    "current_version": "version pointer, set by the version service",
    "environment": "THE PAPER/LIVE DISCRIMINATOR on a trading strategy",
    "is_active": "lifecycle, set by deploy/stop",
    "dag_hash": "derived from the compiled plan",
    "dag_version": "derived from the compiled plan",
    "dag_schema_version": "derived from the compiled plan",
    "last_signal_at": "runtime telemetry",
    "archived_at": "archival state — bypasses the archive workflow",
    "is_published": "marketplace publication state",
    "marketplace_listing_id": "marketplace linkage",
    "is_subscribed": "subscription state",
    "is_read_only": "subscriber write-protection flag",
    "subscription_id": "subscription linkage",
    "cloned_from": "clone provenance",
}

#: Fields the Builder legitimately saves. If the allowlist loses one of these, saving a
#: strategy breaks -- so this is the half that proves the fix is scoped, not just strict.
EXPECTED_MUTABLE = (
    "name", "symbol", "timeframe", "buy_logic", "sell_logic", "risk", "indicators",
    "dag_config", "ml_model_path", "exchange_id", "status", "execution_order",
    "backtest_result",
)


# ── 1. the allowlist's contents ──────────────────────────────────────────────


@pytest.mark.parametrize("column", sorted(FORBIDDEN_COLUMNS))
def test_forbidden_column_is_not_in_the_allowlist(column):
    assert column not in _UPDATABLE_STRATEGY_COLUMNS, (
        f"'{column}' is in _UPDATABLE_STRATEGY_COLUMNS but must not be caller-writable "
        f"({FORBIDDEN_COLUMNS[column]}). This is API-01."
    )


@pytest.mark.parametrize("column", EXPECTED_MUTABLE)
def test_legitimately_mutable_column_is_in_the_allowlist(column):
    assert column in _UPDATABLE_STRATEGY_COLUMNS, (
        f"'{column}' is missing from _UPDATABLE_STRATEGY_COLUMNS; the Builder saves it, "
        f"so the allowlist is too narrow and strategy editing is broken."
    )


def test_allowlist_covers_no_column_outside_the_real_schema():
    """A typo in the allowlist would be a column that silently never applies."""
    unknown = sorted(_UPDATABLE_STRATEGY_COLUMNS - set(ALL_STRATEGY_COLUMNS))
    assert unknown == [], (
        f"_UPDATABLE_STRATEGY_COLUMNS names {unknown}, which are not columns on "
        f"public.strategies. Either the allowlist has a typo or the schema changed."
    )


def test_every_schema_column_is_classified():
    """No column may be left unconsidered.

    This is the assertion that makes a FUTURE column a test failure rather than a silent
    grant or a silent denial — the failure mode that produced API-01 in the first place.
    """
    classified = set(_UPDATABLE_STRATEGY_COLUMNS) | set(FORBIDDEN_COLUMNS)
    unclassified = sorted(set(ALL_STRATEGY_COLUMNS) - classified)
    assert unclassified == [], (
        f"these public.strategies columns are in neither the allowlist nor the "
        f"forbidden list: {unclassified}. Decide explicitly whether a caller may write "
        f"each one."
    )


def test_graph_payload_keys_are_accepted_but_are_not_columns():
    """`graph_json`/`nodes`/`edges` are body keys folded into `buy_logic`, not columns."""
    for key in _GRAPH_PAYLOAD_KEYS:
        assert key in _NON_COLUMN_BODY_KEYS
        assert key not in _UPDATABLE_STRATEGY_COLUMNS, (
            f"'{key}' is not a column on strategies; it must not be in the column "
            f"allowlist or it would be sent to the UPDATE."
        )
        assert key not in ALL_STRATEGY_COLUMNS


# ── 2. the guard's behaviour ─────────────────────────────────────────────────


def test_guard_accepts_a_legitimate_edit():
    _assert_update_fields_are_mutable({"name": "renamed", "symbol": "ETH/USDT"})


def test_guard_accepts_a_graph_save():
    _assert_update_fields_are_mutable(
        {"name": "x", "graph_json": {"nodes": [], "edges": []}, "nodes": [], "edges": []}
    )


def test_guard_accepts_an_empty_body():
    """An empty update is a no-op, not an error; the handler already tolerates it."""
    _assert_update_fields_are_mutable({})


@pytest.mark.parametrize("column", sorted(FORBIDDEN_COLUMNS))
def test_guard_refuses_each_forbidden_column(column):
    with pytest.raises(HTTPException) as exc:
        _assert_update_fields_are_mutable({column: "anything"})
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "STRATEGY_FIELD_NOT_MUTABLE"
    assert column in exc.value.detail["fields"]


def test_guard_refuses_an_unknown_column():
    """A field that is not a column at all is refused rather than silently dropped."""
    with pytest.raises(HTTPException) as exc:
        _assert_update_fields_are_mutable({"totally_made_up_field": 1})
    assert exc.value.status_code == 422
    assert "totally_made_up_field" in exc.value.detail["fields"]


def test_guard_refuses_a_mixed_body_and_names_every_offender():
    with pytest.raises(HTTPException) as exc:
        _assert_update_fields_are_mutable(
            {"name": "legit", "user_id": "x", "environment": "live", "archived_at": "y"}
        )
    fields = exc.value.detail["fields"]
    assert set(fields) == {"archived_at", "environment", "user_id"}
    assert "name" not in fields, "a legitimate field must not be reported as an offender"


def test_guard_error_echoes_field_names_but_never_values():
    """Same rule library.py:3043-3046 follows: name the field, never the value."""
    secret = "s3cret-value-that-must-not-appear"
    with pytest.raises(HTTPException) as exc:
        _assert_update_fields_are_mutable({"user_id": secret})
    rendered = repr(exc.value.detail)
    assert "user_id" in rendered
    assert secret not in rendered, (
        "the refusal echoed the supplied value back to the caller"
    )


def test_guard_refuses_a_non_object_body():
    with pytest.raises(HTTPException) as exc:
        _assert_update_fields_are_mutable(["not", "an", "object"])
    assert exc.value.status_code == 422


# ── 3. the guard is actually reachable from the handler ──────────────────────


def test_handler_calls_the_guard():
    """A guard that exists but is never called is not a control.

    This repository's own audit found five security controls implemented and wired to
    nothing, so the call site is asserted rather than assumed.
    """
    import inspect

    from backend_app.routers import strategies as mod

    source = inspect.getsource(mod.update_strategy)
    assert "_assert_update_fields_are_mutable(" in source, (
        "update_strategy does not call _assert_update_fields_are_mutable — the "
        "allowlist is dead code and API-01 is still open."
    )


def test_guard_runs_before_the_payload_is_assembled():
    """A refused request must write nothing, so the guard must precede `dict(body)`.

    Comment lines are stripped before the search. The handler's own explanatory comment
    quotes the literal string ``update_payload = dict(body)`` while describing the defect,
    and a naive ``str.index`` matches that comment rather than the statement -- which is
    exactly what this test did on its first run. The line numbers are compared on
    executable code only.
    """
    import inspect

    from backend_app.routers import strategies as mod

    lines = inspect.getsource(mod.update_strategy).splitlines()
    code_lines = [
        (i, line) for i, line in enumerate(lines) if not line.lstrip().startswith("#")
    ]

    guard_line = next(
        (i for i, line in code_lines if "_assert_update_fields_are_mutable(" in line),
        None,
    )
    assemble_line = next(
        (i for i, line in code_lines if line.strip() == "update_payload = dict(body)"),
        None,
    )

    assert guard_line is not None, (
        "no executable call to _assert_update_fields_are_mutable in update_strategy"
    )
    assert assemble_line is not None, (
        "could not locate the `update_payload = dict(body)` statement; if the handler was "
        "refactored, re-establish that the guard still precedes payload assembly."
    )
    assert guard_line < assemble_line, (
        f"the allowlist guard is on line {guard_line} but the payload is assembled on "
        f"line {assemble_line}; a refused request could still have side effects."
    )


def test_status_running_is_still_blocked_separately():
    """`status` is allowlisted, so its own deploy-bypass guard must still be present."""
    import inspect

    from backend_app.routers import strategies as mod

    source = inspect.getsource(mod.update_strategy)
    assert "DIRECT_STATUS_UPDATE_FORBIDDEN" in source, (
        "the status guard was removed. `status` is in the allowlist, so without it a "
        "caller could set status='running' and bypass the deploy gate."
    )
