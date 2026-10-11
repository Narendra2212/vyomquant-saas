"""Does ExecutionRecordRepository.claim_execution's PostgreSQL fast path ever run?

It compares `status` to the lowercase literal 'pending'. `execution_records.status` is the
enum `executionstatus`, whose labels are UPPERCASE only (PENDING, EXECUTING, COMPLETED,
FAILED, UNKNOWN), so PostgreSQL cannot parse that literal as a value of the type. If that
is right, the documented atomic `UPDATE ... RETURNING` path raises on every call and the
code silently falls through to the ORM path.

Measured by running the exact statement, and by watching for the fallback's warning.
"""
from __future__ import annotations

import logging
import os
import pathlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

ROOT = pathlib.Path(__file__).resolve().parents[1]
DSN = (os.environ.get("AERORA_TEST_DATABASE_URL") or "").strip() or (
    (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()
)
print(f"DSN={DSN}")

records: list[str] = []


class Catcher(logging.Handler):
    def emit(self, record):
        records.append(record.getMessage())


logging.getLogger("ExecutionRecord").addHandler(Catcher())
logging.getLogger("ExecutionRecord").setLevel(logging.DEBUG)

from backend_app.core.models.execution_record import ExecutionRecordRepository  # noqa: E402

engine = create_engine(DSN, poolclass=NullPool, future=True)
session = sessionmaker(bind=engine, future=True)()

tenant = str(uuid.uuid4())
execution_id = f"exec_probe_{uuid.uuid4().hex[:10]}"

session.execute(
    text("INSERT INTO auth.users (id, email) VALUES (CAST(:i AS uuid), :e)"),
    {"i": tenant, "e": f"probe-{tenant[:8]}@example.invalid"},
)
session.execute(
    text("INSERT INTO public.profiles (id) VALUES (CAST(:i AS uuid))"), {"i": tenant}
)
now = datetime.now(timezone.utc)
session.execute(
    text(
        "INSERT INTO public.execution_records "
        "(execution_id, tenant_id, strategy_id, symbol, side, size, price, status, "
        " created_at, updated_at) "
        "VALUES (:e, CAST(:t AS uuid), 's', 'BTCUSDT', 'buy', '1', '100', "
        "        CAST('PENDING' AS executionstatus), :n, :n)"
    ),
    {"e": execution_id, "t": tenant, "n": now},
)
session.commit()

# 1. The raw statement the fast path issues.
try:
    session.execute(
        text(
            "UPDATE execution_records SET status='EXECUTING' "
            " WHERE execution_id = :e AND (status = 'PENDING' OR status = 'pending') "
            "RETURNING execution_id"
        ),
        {"e": execution_id},
    ).fetchone()
    session.rollback()
    print("RAW_LOWERCASE_PREDICATE=ACCEPTED")
except Exception as exc:
    session.rollback()
    print(f"RAW_LOWERCASE_PREDICATE=REJECTED {exc.__class__.__name__}: {str(exc)[:160]}")

# 2. The upper(status::text) form proposed as the fix.
try:
    session.execute(
        text(
            "UPDATE execution_records SET status='EXECUTING' "
            " WHERE execution_id = :e AND upper(status::text) = 'PENDING' "
            "RETURNING execution_id"
        ),
        {"e": execution_id},
    ).fetchone()
    session.rollback()
    print("RAW_UPPER_CAST_PREDICATE=ACCEPTED")
except Exception as exc:
    session.rollback()
    print(f"RAW_UPPER_CAST_PREDICATE=REJECTED {exc.__class__.__name__}: {str(exc)[:160]}")

# 3. claim_execution itself: does it succeed, and via which branch?
records.clear()
repo = ExecutionRecordRepository(session)
claimed, record = repo.claim_execution(execution_id, uuid.UUID(tenant))
print(f"CLAIM_EXECUTION_RESULT claimed={claimed} record={'yes' if record else 'no'}")
fell_back = [m for m in records if "falling back" in m]
print(f"FALLBACK_WARNINGS={len(fell_back)}")
for m in fell_back:
    print(f"  {m[:200]}")

session.execute(
    text("DELETE FROM public.execution_records WHERE tenant_id = CAST(:t AS uuid)"),
    {"t": tenant},
)
session.execute(text("DELETE FROM public.profiles WHERE id = CAST(:t AS uuid)"), {"t": tenant})
session.execute(text("DELETE FROM auth.users WHERE id = CAST(:t AS uuid)"), {"t": tenant})
session.commit()
session.close()
engine.dispose()
