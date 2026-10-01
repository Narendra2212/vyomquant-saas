"""tests/test_no_phantom_blocked_execution_at_startup.py

Starting the process does not record an execution that nobody attempted.

THE DEFECT
----------
``main.py`` called ``log_blocked_execution(...)`` at import time whenever
``ExecutionFlags.PRODUCTION_ROUTER_ENABLED`` was False - which it permanently is, by design,
since the STEP 1 lockdown. That function exists to record an execution that was ATTEMPTED and
stopped, which is exactly how the two genuine callers use it:

  * ``dag_worker._run_dag_with_heartbeat`` - a DAG run a feature flag refused;
  * ``dag_event_loop._emit_signal`` - a signal emission a feature flag refused.

Nothing is attempted at startup. A router was simply not mounted. Recording that as a blocked
execution had three consequences:

  1. ``safety_monitor._blocked_events`` gained a phantom entry on EVERY container start;
  2. it was persisted to Redis under the retention window, so it accumulated across restarts;
  3. ``safety_monitor.get_statistics()`` reported it in ``total_blocked_events`` and added a
     ``production_router`` key to ``blocked_by_context`` that no caller caused.

Anyone auditing blocked executions - which is a security question - was reading an event that
never happened. That is the condition ``bugfix.md`` names: the system reporting a fact it has no
evidence of.

And it emitted ``logger.critical`` on every start for a setting that has not changed since STEP
1. CRITICAL is the channel that means look now; spending it on static configuration is how a
CRITICAL stops being read.
"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MAIN = "backend_app/main.py"
#: The two places that record a real, attempted-and-refused execution.
GENUINE_CALLERS = [
    "backend_app/backend/dag_worker.py",
    "backend_app/backend/dag_event_loop.py",
]


def executable(rel):
    """Source with ``#`` comments stripped, so the explanatory note cannot satisfy a scan."""
    text = io.open(os.path.join(REPO, rel), encoding="utf-8").read()
    return chr(10).join([line.split("#")[0] for line in text.split(chr(10))])


class TestStartupRecordsNoBlockedExecution:
    def test_main_does_not_call_it(self):
        body = executable(MAIN)

        assert "log_blocked_execution(" not in body, (
            "main.py still records a blocked execution at import time. Nothing is attempted "
            "at startup - a router is simply not mounted - so this fabricates an event and "
            "inflates safety_monitor.get_statistics()"
        )

    def test_main_does_not_even_import_it(self):
        """A dangling import invites the call back."""
        body = executable(MAIN)

        assert "log_blocked_execution" not in body

    def test_the_router_lockdown_is_still_announced(self):
        """The fact must still be recorded - as configuration, which is what it is."""
        body = executable(MAIN)

        assert "PRODUCTION EXECUTION ROUTER DISABLED" in body
        assert "/api/execution" in body


class TestTheGenuineCallersAreUntouched:
    @pytest.mark.parametrize("rel", GENUINE_CALLERS)
    def test_a_refused_attempt_is_still_recorded(self, rel):
        """These record a real call a flag refused, which is what the function is for."""
        body = executable(rel)

        assert "log_blocked_execution(" in body, (
            "%s no longer records blocked executions; a refused DAG run or signal emission "
            "must still be auditable" % rel
        )


class TestTheStatisticsStartClean:
    def test_a_fresh_monitor_reports_no_blocked_events(self):
        """What an auditor reads on a process that has refused nothing."""
        from backend_app.core.safety_monitor import SafetyMonitor

        stats = SafetyMonitor().get_statistics()

        assert stats["total_blocked_events"] == 0
        assert stats["blocked_by_context"] == {}

    def test_a_real_refusal_still_counts(self):
        """Preservation: the counter must still work for genuine refusals."""
        from backend_app.core.safety_monitor import SafetyMonitor

        monitor = SafetyMonitor()
        monitor.log_blocked_execution(
            source="tests",
            context="dag",
            details={"reason": "a flag refused a real attempt"},
        )

        stats = monitor.get_statistics()
        assert stats["total_blocked_events"] == 1
        assert stats["blocked_by_context"] == {"dag": 1}
