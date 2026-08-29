"""Fixtures for the crash-recovery regression suite (Requirement 19.4, tasks 12.1 - 12.5).

Three things every failure mode needs, done once here so no module repeats them:

  1. ``crash_world`` - the durable world (database, Redis, exchange, clock) plus the
     module-level patch that points ``DistributedIdempotencyLayer``'s Redis handle at the
     recording double. The layer reads ``distributed_idempotency.redis_manager`` at call
     time, so patching that name is what makes the REAL locking algorithm run against a
     store this suite can inspect and expire.
  2. Clearing ``signal_service``'s per-process migration verdicts. Both the 005b column
     probe and the transition-table probe cache their answer in module globals, and a
     verdict cached by one test would otherwise decide the next one's behaviour.
  3. Keeping the environment out of it: ``ENV`` is forced to a non-production value so
     the idempotency layer's fail-closed-in-production branch is not what a test is
     accidentally measuring.
"""

import pytest

from backend_app.backend import signal_service as svc
from backend_app.core import distributed_idempotency as di
from tests.crash_recovery.harness import (
    CrashRecoveryWorld,
    ExchangeTestDouble,
    FakeClock,
    FakeDatabase,
    FrameSink,
    RecordingRedis,
)


@pytest.fixture(autouse=True)
def _forget_migration_verdicts():
    """Each test probes 005b and the transition log for itself."""
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()
    yield
    svc.reset_signal_lifecycle_column_support()
    svc.reset_transitions_table_support()


@pytest.fixture
def crash_world(monkeypatch) -> CrashRecoveryWorld:
    """The world a crashing process acts on, and that outlives it."""
    monkeypatch.setenv("ENV", "testing")

    clock = FakeClock()
    redis = RecordingRedis(clock)
    db = FakeDatabase()
    exchange = ExchangeTestDouble()

    # The layer resolves this name at call time, so this one patch is what puts the real
    # Lua check-and-set and the real owner-token release in front of the double.
    monkeypatch.setattr(di, "redis_manager", redis)

    return CrashRecoveryWorld(
        clock=clock,
        db=db,
        redis=redis,
        exchange=exchange,
        frames=FrameSink(db),
    )
