"""
core/worker_restart_recovery_manager.py — Worker Restart Recovery Manager

Orchestrates the recovery sequence after an execution worker restarts.

STATUS: NOT IMPLEMENTED, AND IT NOW SAYS SO
===========================================
This module is a stub. ``recover_worker`` performs no recovery: it reads no event journal,
reconstructs no order, restores no position and reconciles nothing against the venue.

What changed is what it REPORTS. It used to return::

    WorkerRestartRecoveryResult(success=True, positions_restored=0, orders_restored=0, ...)

unconditionally, with the comment "Return successful stub result for testing compatibility".
That is a fabricated result, and it was consumed as one. ``ExecutionWorker.start``
(``backend/distributed_execution/execution_worker.py``) branches on ``.success`` and, on the
true arm, logs

    Worker <id> restart recovery successful: 0 positions, 0 orders, 0 executions,
    0 fills, 0 mismatches

and increments ``stats["restart_recovery_count"]``. So a restarted worker that recovered
nothing *because no recovery exists* reported, in its own logs and its own counters, that
recovery had run and found nothing to do. An operator cannot tell that line apart from a
genuinely clean restart — which is the distinction that matters most at exactly the moment
it is least available.

"Nothing to recover" and "recovery is not implemented" must not be the same line. This is
the same substitution — an absence reported as an established figure — that the rest of this
codebase refuses on principle: ``ExecutionEngine`` refuses to invent ``total_equity``,
``_get_portfolio_state`` omits a balance it could not read rather than sending ``0``, and the
dashboard renders a not-available marker rather than a zero. A recovery result is held to the
same rule.

WHY THE RESULT IS A REFUSAL RATHER THAN AN EXCEPTION
----------------------------------------------------
``ExecutionWorker`` already handles the false arm correctly: it logs a warning and continues
startup, which it annotates "fail-safe". That is the right behaviour for a worker whose
recovery is unavailable — refusing to start would take the fleet down over a component that
has never run. Raising here would convert that handled path into an exception the caller
catches and logs less informatively, for no gain.

BLAST RADIUS OF THIS CHANGE: NONE
---------------------------------
Nothing in the repository constructs this class. ``ExecutionWorker`` takes it as
``worker_restart_recovery_manager: Optional[Any] = None`` and guards every use behind
``if self.enable_worker_restart_recovery and self.worker_restart_recovery_manager``, so with
no injection site the call is unreachable today. (That worker is itself not started by any
deployment artifact.)

WHAT IMPLEMENTING THIS WOULD REQUIRE
------------------------------------
The two engines it is meant to orchestrate are real and tested
(``tests/test_execution_correctness.py`` exercises both): ``ReplayReconstructionEngine``
rebuilds orders, positions, executions and fills from the event journal in sequence order,
and ``ReconciliationEngine`` compares the result against the venue and classifies
divergences. What is missing is the sequence that drives them, decides what to do with a
detected mismatch, and establishes which tenants a restarted worker is responsible for —
``recover_worker``'s current ``tenant_id="default"`` / ``user_id="default"`` call site is
itself marked "would be configurable in production". That is a feature with design
decisions in it, not a defect with a fix, so it is left undone and declared rather than
simulated.

Regression: tests/test_execution_correctness.py
  ``test_worker_restart_recovery`` — the full contract, xfail(strict=True) until this lands.
  ``test_worker_restart_recovery_does_not_report_a_success_it_did_not_achieve`` — this file.
"""

from dataclasses import dataclass, field
from typing import List, Optional

#: The single error a refusal carries. Spelled once so the message an operator reads and the
#: string the regression test matches on cannot drift apart.
NOT_IMPLEMENTED_ERROR = (
    "Worker restart recovery is NOT IMPLEMENTED: "
    "backend_app/core/worker_restart_recovery_manager.py is a stub. No event journal was "
    "read, no order or position was restored, and no reconciliation against the exchange "
    "was performed. The worker's pre-restart state has NOT been recovered. Treat every "
    "restored count in this result as unmeasured rather than zero."
)


@dataclass
class WorkerRestartRecoveryResult:
    """Result of a worker restart recovery sequence.

    ``success`` defaults to ``False``. The previous default was ``True``, which meant any
    code path that built one of these without deciding anything produced a success — the
    direction a safety-relevant default must not point.

    The restored counts default to ``0`` and that value means "nothing was restored". When
    ``success`` is ``False`` it carries no measurement at all, which is why the refusal names
    that explicitly in :data:`NOT_IMPLEMENTED_ERROR` instead of leaving a reader to infer a
    clean restart from four zeroes.
    """

    success: bool = False
    positions_restored: int = 0
    orders_restored: int = 0
    executions_restored: int = 0
    fills_restored: int = 0
    reconciliation_mismatches: int = 0
    errors: List[str] = field(default_factory=list)


class WorkerRestartRecoveryManager:
    """
    Manages the recovery sequence for restarted execution workers.

    Intended to reconstruct worker state from the event journal and reconcile it against the
    exchange. Not implemented — see the module docstring.
    """

    def __init__(self, redis_client=None):
        # Retained so the constructor signature is unchanged for any caller or test that
        # injects one. It is deliberately NOT used: a stub that accepted a client and
        # appeared to read it would be harder to recognise as a stub.
        self.redis_client = redis_client

    async def recover_worker(
        self,
        tenant_id: str,
        worker_id: str,
        exchange_name: str,
        user_id: str,
        strategy_id: Optional[str] = None,
        from_sequence: Optional[int] = None
    ) -> WorkerRestartRecoveryResult:
        """Refuse, naming the reason. Recovers nothing, and does not claim to.

        Returns:
            A :class:`WorkerRestartRecoveryResult` with ``success=False`` and one error.
            ``ExecutionWorker.start`` logs that as a warning and continues startup, which is
            its existing fail-safe arm.
        """
        return WorkerRestartRecoveryResult(
            success=False,
            positions_restored=0,
            orders_restored=0,
            executions_restored=0,
            fills_restored=0,
            reconciliation_mismatches=0,
            errors=[NOT_IMPLEMENTED_ERROR],
        )
