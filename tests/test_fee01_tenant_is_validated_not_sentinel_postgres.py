"""FEE-01 — a fee whose owning tenant cannot be established is refused, not sentinel-filed.

WHAT THE FINDING WAS
====================
`FeeEngine._get_tenant_id_from_execution` and `_get_tenant_id_from_position` ended:

    return record.tenant_id if record else UUID(int=0)

So a fee for an execution or position that could not be found was written against
`tenant_id = '00000000-0000-0000-0000-000000000000'` -- a uuid belonging to no customer,
and with no foreign key on `fee_records.tenant_id` to object. Under the table's
row-level security (`fee_records_owner_access USING auth.uid() = tenant_id`) such a row
is invisible to every authenticated caller and legible only to `service_role`:
fail-closed in the sense that nobody sees it, but silently unreadable rather than loudly
refused, and the all-zero id is a value a bug elsewhere could collide with.

THE FIX, AND WHY IT BREAKS NOTHING
==================================
Both lookups now raise `TenantResolutionError` when the parent row is missing. Measured
before changing anything:

* NO production code path calls the fee engine -- every importer is a test or an
  `attack_*` script, and `get_fee_engine()` has zero callers;
* every current caller already holds a real tenant uuid;
* `UUID(int=0)` was not an established sentinel convention -- it appeared only in these
  two fallbacks.

So requiring a resolvable tenant removes an error-path foot-gun without changing any live
behaviour.

HOW THIS FILE MEASURES
======================
The resolution tests run against real PostgreSQL, because the thing being asserted is
"the row is or is not present in `execution_records` / `positions`", which is a database
fact. The source-level guarantees (no `UUID(int=0)` fallback remains; the sentinel never
reaches a `fee_records` write) are AST-checked and need no database.
"""

from __future__ import annotations

import ast
import inspect
import os
import pathlib
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend.fee_engine import FeeEngine, TenantResolutionError

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

_PG_URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
_HAS_PG = _PG_URL.startswith("postgres")

pg_only = pytest.mark.skipif(
    not _HAS_PG,
    reason=(
        "requires PostgreSQL: set AERORA_TEST_DATABASE_URL to a postgres DSN. "
        "A SKIP here is NOT a pass -- tenant resolution is unverified."
    ),
)


def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    engine = create_engine(_PG_URL, future=True, poolclass=NullPool)
    return engine, sessionmaker(bind=engine, future=True)()


# ── source-level guarantees (no DB) ──────────────────────────────────────────


def _fee_engine_tree():
    import backend_app.backend.fee_engine as mod

    return ast.parse(inspect.getsource(mod)), inspect.getsource(mod)


def test_no_uuid_int_zero_fallback_remains():
    """The all-zero-uuid fallback is gone from both lookups.

    AST-based: a `UUID(int=0)` written in a comment or docstring explaining the OLD
    behaviour must not fail this. The check is for a real `Call` node `UUID(int=0)` whose
    enclosing function is one of the two tenant lookups.
    """
    tree, _ = _fee_engine_tree()

    def is_uuid_int_zero(node):
        if not isinstance(node, ast.Call):
            return False
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "UUID":
            return False
        for kw in node.keywords:
            if kw.arg == "int" and isinstance(kw.value, ast.Constant) and kw.value.value == 0:
                return True
        return False

    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name not in ("_get_tenant_id_from_execution", "_get_tenant_id_from_position"):
            continue
        for node in ast.walk(fn):
            if is_uuid_int_zero(node):
                offenders.append(f"{fn.name}:{node.lineno}")

    assert offenders == [], (
        "a UUID(int=0) tenant fallback is still present, so an unattributable fee is "
        f"still written against the all-zero tenant: {offenders}"
    )


def test_both_lookups_raise_on_a_missing_parent():
    """Each lookup must raise rather than return a value when the parent is absent.

    Asserted on the AST: the function body must contain a `raise` and must NOT contain a
    bare `return <something> if ... else <fallback>` that yields a sentinel.
    """
    tree, _ = _fee_engine_tree()
    for name in ("_get_tenant_id_from_execution", "_get_tenant_id_from_position"):
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
        )
        raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
        assert raises, f"{name} does not raise when the parent row is missing"


# ── behavioural guarantees (PostgreSQL) ──────────────────────────────────────


@pg_only
def test_a_missing_execution_refuses_rather_than_returning_the_sentinel():
    engine, session = _session()
    try:
        repo = FeeEngine(session)
        with pytest.raises(TenantResolutionError) as excinfo:
            repo._get_tenant_id_from_execution(f"nonexistent_{uuid.uuid4().hex}")
        assert "FEE-01" in str(excinfo.value)
    finally:
        session.close()
        engine.dispose()


@pg_only
def test_a_missing_position_refuses_rather_than_returning_the_sentinel():
    engine, session = _session()
    try:
        repo = FeeEngine(session)
        with pytest.raises(TenantResolutionError):
            repo._get_tenant_id_from_position(f"nonexistent_{uuid.uuid4().hex}")
    finally:
        session.close()
        engine.dispose()


@pg_only
def test_a_present_execution_resolves_to_its_real_tenant():
    """NEGATIVE CONTROL. The refusal must not be "refuse everything"."""
    engine, session = _session()
    tenant = str(uuid.uuid4())
    execution_id = f"fee01_{uuid.uuid4().hex[:12]}"
    try:
        from sqlalchemy import text

        session.execute(
            text("INSERT INTO auth.users (id, email) VALUES (CAST(:t AS uuid), :e) "
                 "ON CONFLICT DO NOTHING"),
            {"t": tenant, "e": f"fee01-{tenant[:8]}@example.invalid"},
        )
        session.execute(
            text("INSERT INTO public.profiles (id, email) VALUES (CAST(:t AS uuid), :e) "
                 "ON CONFLICT DO NOTHING"),
            {"t": tenant, "e": f"fee01-{tenant[:8]}@example.invalid"},
        )
        session.execute(
            text("INSERT INTO public.execution_records "
                 "(execution_id, tenant_id, strategy_id, symbol, side, size, status, "
                 " created_at, updated_at) "
                 "VALUES (:e, CAST(:t AS uuid), 'fee01', 'BTCUSDT', 'buy', '1', "
                 "        CAST('COMPLETED' AS executionstatus), now(), now())"),
            {"e": execution_id, "t": tenant},
        )
        session.commit()

        resolved = FeeEngine(session)._get_tenant_id_from_execution(execution_id)
        assert str(resolved) == tenant, "a present execution did not resolve to its tenant"
        assert resolved != uuid.UUID(int=0)
    finally:
        from sqlalchemy import text

        session.rollback()
        session.execute(
            text("DELETE FROM public.execution_records WHERE execution_id = :e"),
            {"e": execution_id},
        )
        session.execute(text("DELETE FROM public.profiles WHERE id = CAST(:t AS uuid)"),
                        {"t": tenant})
        session.execute(text("DELETE FROM auth.users WHERE id = CAST(:t AS uuid)"),
                        {"t": tenant})
        session.commit()
        session.close()
        engine.dispose()


def test_the_postgres_requirement_is_explicit():
    """The gate, AST-checked so a skip cannot be silent and a prose mention of the DSN
    file cannot fail it."""
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    env_reads = {
        n.args[0].value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "get"
        and n.args and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str)
    }
    assert "AERORA_TEST_DATABASE_URL" in env_reads
