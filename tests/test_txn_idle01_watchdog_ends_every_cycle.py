"""TXN-IDLE-01: the order watchdog ends its transaction every cycle.

Found 2026-11-19: one shared session, reads never committed -> a single transaction "idle in
transaction" for 2 h 27 m in production, blocking ALTER TABLE execution_records (migration
079 queued, and every later query on the table queued behind it) and pinning a snapshot.
"""

from __future__ import annotations

import inspect

from backend_app.backend.order_watchdog import OrderWatchdog


class _DB:
    def __init__(self, fail=False):
        self.commits = self.rollbacks = 0
        self.fail = fail

    def commit(self):
        if self.fail:
            raise RuntimeError("connection lost")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _wd(db):
    wd = OrderWatchdog.__new__(OrderWatchdog)
    wd.db = db
    return wd


def test_a_cycle_end_commits():
    db = _DB()
    _wd(db)._end_cycle_transaction()
    assert db.commits == 1


def test_a_failed_commit_rolls_back_like_a_failed_cycle():
    db = _DB(fail=True)
    _wd(db)._end_cycle_transaction()
    assert db.rollbacks == 1


def test_both_loops_end_their_cycle_before_sleeping():
    for loop in (OrderWatchdog._monitoring_loop, OrderWatchdog._reconciliation_loop):
        src = inspect.getsource(loop)
        assert "self._end_cycle_transaction()" in src, loop.__name__
