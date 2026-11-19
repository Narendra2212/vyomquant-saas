"""EXEC-ENV-01: a venue fill records whether it went to a sandbox, and recording never blocks.

The marketplace counts only ``is_sandbox = false`` fills as live history (testnet fills have an
``exchange_id`` too). The stamp is a separate statement after the submission evidence, so a
database without migration 079 leaves the row NULL -- not-real-money -- and the order proceeds.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from backend_app.core import execution_engine
from backend_app.core.models.execution_record import ExecutionRecordRepository


class _DB:
    def __init__(self, fail=False):
        self.calls, self.fail, self.commits, self.rollbacks = [], fail, 0, 0

    def execute(self, stmt, params):
        if self.fail:
            raise RuntimeError('column "is_sandbox" does not exist')
        self.calls.append((str(stmt), params))
        return SimpleNamespace(fetchone=lambda: None)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_the_stamp_writes_the_flag_for_this_tenants_row():
    db = _DB()
    ExecutionRecordRepository(db)._stamp_environment("exec_1", "t", True)
    sql, params = db.calls[0]
    assert "SET is_sandbox = :sb" in sql and "tenant_id = :tenant_id" in sql
    assert params == {"sb": True, "execution_id": "exec_1", "tenant_id": "t"}


def test_a_missing_column_is_swallowed_and_rolled_back():
    db = _DB(fail=True)
    ExecutionRecordRepository(db)._stamp_environment("exec_1", "t", False)
    assert db.rollbacks == 1


def test_unknown_is_not_written():
    db = _DB()
    ExecutionRecordRepository(db)._stamp_environment("exec_1", "t", None)
    assert db.calls == []


def test_the_engine_passes_requested_or_verified_sandbox():
    src = inspect.getsource(execution_engine)
    assert 'getattr(self.exchange_executor, "sandbox", False)' in src
    assert 'getattr(self.exchange_executor, "sandbox_verified", False)' in src
