"""
tests/test_task_29_signal_environments.py - tasks 29.1 and 29.2.

Spec: marketplace-subscriptions-paper-trading. Requirements 23.1, 23.5, 23.7, 24.10.

WHAT THESE TWO TASKS ARE, AS ONE UNIT
    29.1 puts migration 010's two columns - ``signals.environment`` and
    ``signals.paper_session_id`` - into ``signal_service``'s projections, behind the SAME
    migration probe that already guards 005b's ``order_lifecycle_state`` /
    ``idempotency_key`` pair. 29.2 is the write that uses them: a ``PAPER`` signal travels
    the same ``public.signals`` path as a ``LIVE`` one, with ``environment='PAPER'``,
    ``paper_session_id`` set and ``deployment_id`` left NULL.

WHAT IS ASSERTED HERE, SECTION BY SECTION
    1. THE PROJECTIONS. The pair is APPENDED and the other thirty-four columns are not
       re-spelled, not re-ordered and not re-typed - asserted against this file's own
       transcription of that list, so a silent edit to any of the thirty-four fails here.
    2. THE PROBE. The pair is probed as a SET (one ``SELECT environment,paper_session_id``),
       because PostgREST reports only the first missing column; an absent pair DEGRADES with
       a warning naming ``010_signal_environment.sql``; and the cached verdict, the recheck
       window and the ``remember_*_absent`` escape hatch behave as 005b's do.
    3. THE PAPER WRITE. One INSERT into ``signals`` - not a second store (Requirement 23.5)
       - carrying ``environment='PAPER'``, the session identifier, and ``deployment_id``
       NULL, at the unchanged ``GENERATED`` state. A pre-010 database still records the
       signal, because an absent COLUMN costs an audit field and not the record.
    4. THE LIVE PATH, UNCHANGED. ``generate_signal`` issues the same one probe and the same
       INSERT payload it always did: 010 adds ``environment NOT NULL DEFAULT 'LIVE'``
       precisely so the writer that predates the Paper_Session needs no change
       (Requirement 23.7, and 25.1's "the signal-generation path retains its existing
       behaviour" - which ``tests/regression/test_baseline_unchanged.py`` also pins).
    5. THE RECORDER SEAM. ``paper_session_runtime.build_session_signal_recorder`` is what
       ``spawn_session_loop(record_signal=…)`` installs, and a recorder failure does not stop
       a paper order - ``paper_session_service._record_signal`` logs and swallows, which is
       the documented asymmetry with the live path's Requirement 15.5 refusal.

WHY THE DOUBLE IS LOCAL, AND WHY THAT IS NOT A SECOND PAPER DOUBLE
    The table under test is ``public.signals``, which is not one of the eleven ``paper_*``
    tables. ``tests/test_paper_repository.py::FakeSupabase`` is the one Persistence_Layer
    double for those, and it stays that: nothing here writes a ``paper_*`` table, and the
    Paper_Session and version rows below are plain dict PREMISES, not persisted state. The
    client here is the same minimal ``signals`` recorder every other signal-path suite
    declares locally (``tests/test_task_10_1_generate_signal.py``,
    ``tests/test_task_10_2_submit_signal.py``), for the same reason: what reached
    ``public.signals`` is the whole question, so the double records it.

    ``_run_coroutine`` is imported from ``tests/test_paper_order_lifecycle_writes.py`` rather
    than re-derived, and no test here calls ``asyncio.run``: see that module's
    ``_HARNESS_LOOP`` note - a fresh loop per call exhausts the Windows loopback port range.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend_app.backend import paper_session_runtime as runtime
from backend_app.backend import signal_service as svc
from backend_app.backend.execution_environment import ExecutionEnvironment
from backend_app.backend.order_lifecycle_state import OrderLifecycleState
from backend_app.backend.paper import paper_session_service as paper_sessions
from backend_app.backend.signal_service import (
    SIGNAL_ENVIRONMENT_COLUMNS,
    SIGNAL_ENVIRONMENT_MIGRATION,
    SIGNAL_LIFECYCLE_COLUMNS,
    SIGNAL_SUMMARY_COLUMNS,
    SIGNAL_TRACE_COLUMNS,
    SignalGenerationRefused,
    generate_paper_signal,
    generate_signal,
    paper_session_facts,
    without_signal_environment_columns,
)
from tests.test_paper_order_lifecycle_writes import _run_coroutine

# ══════════════════════════════════════════════════════════════════════════
# FIXTURES
# ══════════════════════════════════════════════════════════════════════════

#: The thirty-four columns ``SIGNAL_SUMMARY_COLUMNS`` carried before task 29.1, in order.
#: Transcribed here ON PURPOSE rather than sliced out of the constant: an assertion that
#: compares a constant with itself proves nothing, and "the other thirty-four columns are
#: not re-spelled" is only checkable against an independent copy of the list.
THE_THIRTY_FOUR = (
    "id", "user_id", "strategy_id", "strategy_version", "deployment_id", "exchange_id",
    "symbol", "timeframe", "worker_id", "decision", "status", "risk_passed", "risk_reason",
    "position_size", "capital", "exposure", "expected_loss", "expected_reward", "order_id",
    "order_status", "quantity", "filled", "remaining", "average_price", "fees", "slippage",
    "latency_ms", "trade_id", "pnl", "realized_pnl", "generated_at", "risk_evaluated_at",
    "order_updated_at", "executed_at",
)

SESSION_ID = "7f1c9a44-0000-4000-8000-0000000000ab"
OWNER = "user-aaaa"

#: A ``42703`` as PostgREST reports it: it names ONE missing column, which is the whole
#: reason the pair is probed as a set rather than one column at a time.
MISSING_ENVIRONMENT = (
    "{'code': '42703', 'message': 'column signals.environment does not exist'}"
)
MISSING_LIFECYCLE_COLUMN = (
    "{'code': '42703', 'message': 'column signals.order_lifecycle_state does not exist'}"
)


def paper_session_row(**overrides: Any) -> Dict[str, Any]:
    """A ``paper_sessions`` row, as the session loop holds it.

    Note what it does NOT have: a ``strategy_id`` (the column is ``source_strategy_id``), a
    version LABEL (only ``version_id``) and any exchange account. Those three absences are
    exactly what ``paper_session_facts`` has to reconcile.
    """
    row = {
        "id": SESSION_ID,
        "user_id": OWNER,
        "source_strategy_id": "strat-bbbb",
        "version_id": "ver-cccc",
        "environment": "PAPER",
        "session_state": "RUNNING",
        "exchange_id": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
    }
    row.update(overrides)
    return row


def version_row(**overrides: Any) -> Dict[str, Any]:
    """The ``strategy_versions`` row the session's plan was resolved from."""
    row = {"id": "ver-cccc", "version": "v3", "lifecycle_state": "LIVE"}
    row.update(overrides)
    return row


def deployment_row(**overrides: Any) -> Dict[str, Any]:
    """A ``strategy_deployments`` row - the LIVE path's own input, for the contrast."""
    row = {
        "id": "dep-1111",
        "user_id": OWNER,
        "strategy_id": "strat-bbbb",
        "version": "v3",
        "version_id": "ver-cccc",
        "exchange_account_id": "acct-dddd",
        "exchange_id": "kraken",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "mode": "live",
    }
    row.update(overrides)
    return row


def action_output(**overrides: Any) -> Dict[str, Any]:
    """What the strategy runtime produced for one bar."""
    output = {
        "decision": "BUY",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "quantity": 0.25,
        "price": 61234.5,
        "bar_time": "2024-05-01T12:00:00+00:00",
        "source_node_ids": ["action-1"],
        "closure_ready": True,
        "node_closure": {"rsi-1": 28.4},
        "risk_validation": {
            "passed": True,
            "reason": "within limits",
            "position_size": 0.25,
            "capital": 10000.0,
            "evaluated_at": "2024-05-01T12:00:01+00:00",
        },
    }
    output.update(overrides)
    return output


class _Result:
    def __init__(self, data: Any = None, error: Any = None) -> None:
        self.data = data
        self.error = error


class FakeSignalsClient:
    """The smallest PostgREST client that answers this path: two probes and one INSERT.

    ``select(cols).limit(1).execute()`` is a migration probe - 005b's or 010's, told apart by
    the columns it names, which is what makes "the pair is probed as a set" observable.
    ``insert(payload).execute()`` is the write. Both are recorded in order, because what
    reached ``public.signals`` and in what sequence is the whole question.
    """

    def __init__(
        self,
        *,
        missing_environment: bool = False,
        missing_lifecycle: bool = False,
        probe_error: Optional[str] = None,
    ) -> None:
        self.missing_environment = missing_environment
        self.missing_lifecycle = missing_lifecycle
        self.probe_error = probe_error
        self.selected: List[str] = []
        self.inserted: List[Dict[str, Any]] = []
        self._pending: Any = None

    def table(self, name: str) -> "FakeSignalsClient":
        assert name == "signals", f"nothing on this path may touch {name}"
        return self

    def select(self, columns: str) -> "FakeSignalsClient":
        self._pending = ("select", columns)
        return self

    def limit(self, _n: int) -> "FakeSignalsClient":
        return self

    def insert(self, payload: Dict[str, Any]) -> "FakeSignalsClient":
        self._pending = ("insert", payload)
        return self

    def execute(self) -> Any:
        kind, payload = self._pending
        if kind == "select":
            self.selected.append(payload)
            if self.probe_error is not None:
                raise Exception(self.probe_error)
            names = {name.strip() for name in payload.split(",")}
            if self.missing_environment and names & set(SIGNAL_ENVIRONMENT_COLUMNS):
                raise Exception(MISSING_ENVIRONMENT)
            if self.missing_lifecycle and names & set(SIGNAL_LIFECYCLE_COLUMNS):
                raise Exception(MISSING_LIFECYCLE_COLUMN)
            return _Result(data=[])
        self.inserted.append(payload)
        # A database missing a column answers the INSERT that names it, once. The retry that
        # drops the column lands, which is what "degrades" has to mean at the statement.
        if self.missing_environment and any(
            column in payload for column in SIGNAL_ENVIRONMENT_COLUMNS
        ):
            raise Exception(MISSING_ENVIRONMENT)
        if self.missing_lifecycle and any(
            column in payload for column in SIGNAL_LIFECYCLE_COLUMNS
        ):
            raise Exception(MISSING_LIFECYCLE_COLUMN)
        return _Result(data=[dict(payload)])

    # -- assertion helpers -------------------------------------------------
    def probes(self) -> List[str]:
        return list(self.selected)

    def the_one_row(self) -> Dict[str, Any]:
        landed = [row for row in self.inserted if "id" in row]
        assert landed, "no row reached public.signals"
        return landed[-1]


@pytest.fixture(autouse=True)
def _forget_migration_verdicts():
    """Both verdicts are cached per process; each test probes for itself."""
    svc.reset_signal_lifecycle_column_support()
    svc.reset_signal_environment_column_support()
    yield
    svc.reset_signal_lifecycle_column_support()
    svc.reset_signal_environment_column_support()


# ══════════════════════════════════════════════════════════════════════════
# 1. THE PROJECTIONS (task 29.1, Requirements 23.1, 24.10)
# ══════════════════════════════════════════════════════════════════════════


def test_the_pair_is_the_two_columns_010_adds():
    assert SIGNAL_ENVIRONMENT_COLUMNS == ("environment", "paper_session_id")


def test_the_two_columns_are_appended_to_the_summary_projection():
    """Appended - so the thirty-four keep their positions and nothing is re-ordered."""
    columns = SIGNAL_SUMMARY_COLUMNS.split(",")
    assert tuple(columns[:-2]) == THE_THIRTY_FOUR
    assert tuple(columns[-2:]) == SIGNAL_ENVIRONMENT_COLUMNS


def test_the_other_thirty_four_columns_are_not_re_spelled():
    """The count and the spelling both, against this file's independent transcription."""
    assert len(THE_THIRTY_FOUR) == 34
    for column in THE_THIRTY_FOUR:
        assert f",{column}," in f",{SIGNAL_SUMMARY_COLUMNS},", f"{column} was re-spelled"


def test_the_trace_projection_carries_the_pair_and_still_ends_with_the_three_jsonb_columns():
    assert SIGNAL_TRACE_COLUMNS.startswith(SIGNAL_SUMMARY_COLUMNS)
    assert SIGNAL_TRACE_COLUMNS.endswith(",indicators,market_info,ml_info")
    for column in SIGNAL_ENVIRONMENT_COLUMNS:
        assert f",{column}," in f",{SIGNAL_TRACE_COLUMNS},"


def test_neither_projection_names_a_column_twice():
    for projection in (SIGNAL_SUMMARY_COLUMNS, SIGNAL_TRACE_COLUMNS):
        names = projection.split(",")
        assert len(names) == len(set(names)), f"a column is named twice in {projection}"


def test_stripping_the_pair_returns_exactly_the_pre_010_projection():
    """What a database without 010 is read with: the thirty-four, in their order."""
    assert without_signal_environment_columns(SIGNAL_SUMMARY_COLUMNS) == ",".join(
        THE_THIRTY_FOUR
    )
    assert without_signal_environment_columns(SIGNAL_TRACE_COLUMNS) == (
        ",".join(THE_THIRTY_FOUR) + ",indicators,market_info,ml_info"
    )


def test_stripping_leaves_a_projection_that_does_not_name_the_pair():
    stripped = without_signal_environment_columns(SIGNAL_TRACE_COLUMNS)
    for column in SIGNAL_ENVIRONMENT_COLUMNS:
        assert column not in stripped.split(",")


# ══════════════════════════════════════════════════════════════════════════
# 2. THE PROBE - the same one 005b gets (task 29.1)
# ══════════════════════════════════════════════════════════════════════════


def test_the_pair_is_probed_as_one_set():
    """One SELECT naming BOTH, because PostgREST reports only the first missing column."""
    client = FakeSignalsClient()
    assert _run_coroutine(svc.signal_environment_columns_supported(client)) is True
    assert client.probes() == ["environment,paper_session_id"]


def test_an_absent_pair_degrades_with_a_warning_naming_010(caplog):
    client = FakeSignalsClient(missing_environment=True)
    with caplog.at_level("WARNING"):
        supported = _run_coroutine(svc.signal_environment_columns_supported(client))
    assert supported is False
    assert "010_signal_environment.sql" in caplog.text
    assert SIGNAL_ENVIRONMENT_MIGRATION in caplog.text


def test_an_absent_pair_is_a_degradation_and_not_a_refusal():
    """The projection narrows; nothing raises. An absent COLUMN costs an audit field."""
    client = FakeSignalsClient(missing_environment=True)
    projection = _run_coroutine(
        svc.signal_projection_for(client, SIGNAL_SUMMARY_COLUMNS)
    )
    assert projection == ",".join(THE_THIRTY_FOUR)


def test_an_applied_010_leaves_the_projection_alone():
    client = FakeSignalsClient()
    projection = _run_coroutine(
        svc.signal_projection_for(client, SIGNAL_TRACE_COLUMNS)
    )
    assert projection == SIGNAL_TRACE_COLUMNS


def test_the_verdict_is_cached_so_a_second_reader_does_not_re_probe():
    client = FakeSignalsClient()
    _run_coroutine(svc.signal_environment_columns_supported(client))
    _run_coroutine(svc.signal_environment_columns_supported(client))
    assert client.probes() == ["environment,paper_session_id"]
    assert svc.signal_environment_column_support_state() is True


def test_a_negative_verdict_is_re_probed_after_the_recheck_window():
    """Applying 010 to a running fleet takes effect without a redeploy."""
    client = FakeSignalsClient(missing_environment=True)
    assert _run_coroutine(svc.signal_environment_columns_supported(client)) is False
    assert svc.signal_environment_column_support_state() is False

    # Still inside the window: the cached "absent" answers, with no second statement.
    assert _run_coroutine(svc.signal_environment_columns_supported(client)) is False
    assert client.probes() == ["environment,paper_session_id"]

    # The window has passed, and the migration has since been applied by hand.
    svc._signal_environment_columns_checked_at = (
        time.monotonic() - svc.SIGNAL_ENVIRONMENT_COLUMN_RECHECK_SECONDS - 1.0
    )
    applied = FakeSignalsClient()
    assert _run_coroutine(svc.signal_environment_columns_supported(applied)) is True
    assert applied.probes() == ["environment,paper_session_id"]


def test_the_recheck_window_is_the_same_one_005b_uses():
    assert (
        svc.SIGNAL_ENVIRONMENT_COLUMN_RECHECK_SECONDS
        == svc.SIGNAL_LIFECYCLE_COLUMN_RECHECK_SECONDS
    )


def test_the_escape_hatch_records_absence_without_a_probe():
    """A process that met 42703 at the INSERT knows more than the probe did."""
    svc.remember_signal_environment_columns_absent()
    assert svc.signal_environment_column_support_state() is False

    client = FakeSignalsClient()
    projection = _run_coroutine(
        svc.signal_projection_for(client, SIGNAL_SUMMARY_COLUMNS)
    )
    assert projection == ",".join(THE_THIRTY_FOUR)
    assert client.probes() == []  # the cached verdict answered


def test_an_inconclusive_probe_resolves_to_supported():
    """A real error must surface at the statement, not be pre-empted by a downgrade."""
    client = FakeSignalsClient(probe_error="connection reset by peer")
    assert _run_coroutine(svc.signal_environment_columns_supported(client)) is True
    assert svc.signal_environment_column_support_state() is None


def test_no_client_at_all_is_not_a_positive_verdict():
    assert _run_coroutine(svc.signal_environment_columns_supported(None)) is False


def test_the_error_classifier_is_narrow():
    missing = svc.is_missing_signal_environment_column_error
    assert missing(Exception(MISSING_ENVIRONMENT)) is True
    assert missing(Exception("could not find the 'paper_session_id' column in the schema cache"))
    assert missing(Exception("PGRST205: relation public.signals does not exist")) is False
    assert missing(Exception("connection reset by peer")) is False
    assert missing(Exception("")) is False


def test_the_two_verdicts_are_independent():
    """005b and 010 are separate hand-applied files; one boolean cannot answer for two."""
    client = FakeSignalsClient(missing_environment=True)
    _run_coroutine(svc.signal_environment_columns_supported(client))
    assert svc.signal_environment_column_support_state() is False
    assert svc.signal_lifecycle_column_support_state() is None


# ══════════════════════════════════════════════════════════════════════════
# 3. THE PAPER WRITE (task 29.2, Requirements 23.1, 23.5)
# ══════════════════════════════════════════════════════════════════════════


def a_paper_signal(client: FakeSignalsClient, **overrides: Any):
    return _run_coroutine(
        generate_paper_signal(
            paper_session_row(),
            action_output(**overrides),
            version=version_row(),
            sb=client,
        )
    )


def test_a_paper_signal_reaches_the_signals_table_and_nothing_else():
    """Requirement 23.5: one signal store, and one INSERT into it."""
    client = FakeSignalsClient()
    a_paper_signal(client)
    assert len([row for row in client.inserted if "id" in row]) == 1


def test_a_paper_signal_names_the_environment_and_the_session():
    client = FakeSignalsClient()
    signal = a_paper_signal(client)
    row = client.the_one_row()

    assert row["environment"] == ExecutionEnvironment.PAPER.value == "PAPER"
    assert row["paper_session_id"] == SESSION_ID
    assert signal.symbol == "BTC/USDT"


def test_a_paper_signal_leaves_deployment_id_null():
    """A Paper_Session is not a deployment; paper_session_id is the applicable identifier."""
    client = FakeSignalsClient()
    signal = a_paper_signal(client)
    assert client.the_one_row()["deployment_id"] is None
    assert signal.deployment_id is None


def test_a_paper_row_is_the_same_shape_as_a_live_one_plus_the_two_columns():
    """Requirement 23.5, as a column set: distinguished BY a column, not by a table."""
    paper_client = FakeSignalsClient()
    a_paper_signal(paper_client)

    live_client = FakeSignalsClient()
    _run_coroutine(generate_signal(deployment_row(), action_output(), sb=live_client))

    added = set(paper_client.the_one_row()) - set(live_client.the_one_row())
    assert added == set(SIGNAL_ENVIRONMENT_COLUMNS)
    assert not set(live_client.the_one_row()) - set(paper_client.the_one_row())


def test_a_paper_signal_starts_at_the_unchanged_generated_state():
    """No paper-specific state vocabulary: the row starts where a live one does."""
    client = FakeSignalsClient()
    signal = a_paper_signal(client)
    assert signal.order_lifecycle_state is OrderLifecycleState.GENERATED
    assert client.the_one_row()["order_lifecycle_state"] == "GENERATED"


def test_a_paper_signal_carries_the_sessions_own_attribution():
    client = FakeSignalsClient()
    signal = a_paper_signal(client)
    row = client.the_one_row()
    assert row["user_id"] == OWNER
    assert row["strategy_id"] == "strat-bbbb"
    assert row["strategy_version"] == "v3"          # the LABEL, from the version row
    assert row["exchange_id"] == "binance"          # the venue slug, NOT NULL on this table
    assert row["market_info"]["mode"] == "paper"
    assert row["market_info"]["strategy_version_id"] == "ver-cccc"
    assert signal.idempotency_key == f"signal:{signal.id}"


def test_the_paper_write_probes_both_migrations_and_writes_once():
    client = FakeSignalsClient()
    a_paper_signal(client)
    assert set(client.probes()) == {
        ",".join(SIGNAL_LIFECYCLE_COLUMNS),
        ",".join(SIGNAL_ENVIRONMENT_COLUMNS),
    }


def test_a_pre_010_database_still_records_the_paper_signal(caplog):
    """Degrade, not refuse: the row is written and the warning names the file."""
    client = FakeSignalsClient(missing_environment=True)
    with caplog.at_level("WARNING"):
        signal = a_paper_signal(client)

    row = client.the_one_row()
    for column in SIGNAL_ENVIRONMENT_COLUMNS:
        assert column not in row
    assert row["id"] == signal.id                        # the signal itself is persisted
    assert row["order_lifecycle_state"] == "GENERATED"   # 005b's pair is unaffected
    assert "010_signal_environment.sql" in caplog.text


def test_an_absent_column_met_at_the_insert_degrades_and_retries(caplog):
    """The probe said yes and the INSERT said 42703: the escape hatch, at the statement."""
    client = FakeSignalsClient()
    _run_coroutine(svc.signal_environment_columns_supported(client))  # cache a yes
    client.missing_environment = True

    with caplog.at_level("WARNING"):
        signal = a_paper_signal(client)

    assert svc.signal_environment_column_support_state() is False
    landed = client.the_one_row()
    assert "environment" not in landed and landed["id"] == signal.id
    assert "010_signal_environment.sql" in caplog.text


def test_a_pre_005b_and_pre_010_database_still_records_the_paper_signal():
    """Both migrations absent: both pairs are dropped and the signal is still written."""
    client = FakeSignalsClient(missing_environment=True, missing_lifecycle=True)
    signal = a_paper_signal(client)
    row = client.the_one_row()
    for column in SIGNAL_ENVIRONMENT_COLUMNS + SIGNAL_LIFECYCLE_COLUMNS:
        assert column not in row
    assert row["id"] == signal.id
    assert row["decision"] == "BUY"


def test_a_paper_signal_with_no_session_identifier_is_refused():
    client = FakeSignalsClient()
    with pytest.raises(SignalGenerationRefused) as refusal:
        _run_coroutine(
            generate_paper_signal(
                {key: value for key, value in paper_session_row().items() if key != "id"},
                action_output(),
                version=version_row(),
                sb=client,
            )
        )
    assert refusal.value.code == "PAPER_SIGNAL_SESSION_UNIDENTIFIED"
    assert client.inserted == []


def test_a_session_with_no_resolvable_version_label_is_refused_not_invented():
    client = FakeSignalsClient()
    with pytest.raises(SignalGenerationRefused) as refusal:
        _run_coroutine(
            generate_paper_signal(paper_session_row(), action_output(), sb=client)
        )
    assert refusal.value.code == "SIGNAL_ATTRIBUTION_INCOMPLETE"
    assert client.inserted == []


def test_paper_session_facts_never_produce_a_deployment_identifier():
    facts = paper_session_facts(paper_session_row(), version=version_row())
    assert "id" not in facts and "deployment_id" not in facts
    assert facts["strategy_id"] == "strat-bbbb"
    assert facts["strategy_version"] == "v3"
    assert facts["mode"] == "paper"
    # A Paper_Session trades no real account, so there is nothing to point at.
    assert facts.get("exchange_account_id") is None


# ══════════════════════════════════════════════════════════════════════════
# 4. THE LIVE PATH IS UNCHANGED (Requirements 23.7, 25.1)
# ══════════════════════════════════════════════════════════════════════════


def test_a_live_signal_names_no_environment_and_no_session():
    """010 adds environment NOT NULL DEFAULT 'LIVE' so this writer needs no change."""
    client = FakeSignalsClient()
    _run_coroutine(generate_signal(deployment_row(), action_output(), sb=client))
    row = client.the_one_row()
    for column in SIGNAL_ENVIRONMENT_COLUMNS:
        assert column not in row


def test_a_live_signal_issues_only_the_005b_probe():
    """The 010 probe runs for a caller that names an environment, and for no other."""
    client = FakeSignalsClient()
    _run_coroutine(generate_signal(deployment_row(), action_output(), sb=client))
    assert client.probes() == [",".join(SIGNAL_LIFECYCLE_COLUMNS)]
    assert svc.signal_environment_column_support_state() is None


def test_to_row_adds_nothing_by_default():
    signal = svc.mint_signal(deployment_row(), action_output())
    for column in SIGNAL_ENVIRONMENT_COLUMNS:
        assert column not in signal.to_row()
        assert column not in signal.to_row(include_lifecycle_columns=False)


def test_a_session_identifier_without_an_environment_is_not_written_alone():
    """Half the fact is not a fact: Requirement 23.2 reads the two together."""
    signal = svc.mint_signal(deployment_row(), action_output())
    row = signal.to_row(paper_session_id=SESSION_ID)
    assert "paper_session_id" not in row and "environment" not in row


# ══════════════════════════════════════════════════════════════════════════
# 5. THE RECORDER SEAM (task 29.2, and task 27.2's contract)
# ══════════════════════════════════════════════════════════════════════════


def test_the_recorder_writes_the_paper_row_through_signal_service():
    client = FakeSignalsClient()
    record = runtime.build_session_signal_recorder(
        client, session=paper_session_row(), version_row=version_row()
    )
    recorded = _run_coroutine(
        record(
            action_output(),
            environment=paper_sessions.SIGNAL_ENVIRONMENT,
            paper_session_id=SESSION_ID,
        )
    )
    row = client.the_one_row()
    assert row["environment"] == "PAPER"
    assert row["paper_session_id"] == SESSION_ID
    assert row["deployment_id"] is None
    assert recorded.id == row["id"]


def test_the_recorder_matches_the_seam_the_loop_calls():
    """``record(signal, *, environment, paper_session_id)`` - a substitution, not a change."""
    import inspect

    record = runtime.build_session_signal_recorder(
        FakeSignalsClient(), session=paper_session_row(), version_row=version_row()
    )
    installed = inspect.signature(record).parameters
    default = inspect.signature(paper_sessions.no_signal_trace_recorder).parameters
    assert list(installed) == list(default)


@pytest.mark.parametrize("asked", ["LIVE", "BACKTEST", "paper", "", None])
def test_the_recorder_refuses_an_environment_that_is_not_paper(asked):
    """Never a default, and never a near-enough spelling: 'paper' is not 'PAPER'."""
    client = FakeSignalsClient()
    record = runtime.build_session_signal_recorder(
        client, session=paper_session_row(), version_row=version_row()
    )
    with pytest.raises(ValueError):
        _run_coroutine(
            record(action_output(), environment=asked, paper_session_id=SESSION_ID)
        )
    assert client.inserted == []


def test_the_loop_passes_paper_and_the_session_to_whatever_recorder_is_installed():
    """The constant task 27.2 pins, read from where the loop reads it."""
    calls: List[Dict[str, Any]] = []

    async def recorder(signal: Any, *, environment: str, paper_session_id: Any) -> str:
        calls.append({"environment": environment, "paper_session_id": paper_session_id})
        return "recorded"

    outcome = _run_coroutine(
        paper_sessions._record_signal(recorder, action_output(), session_id=SESSION_ID)
    )
    assert outcome == "recorded"
    assert calls == [{"environment": "PAPER", "paper_session_id": SESSION_ID}]


def test_a_recorder_failure_does_not_stop_the_order(caplog):
    """The documented asymmetry with the live path's Requirement 15.5 refusal."""

    async def broken(signal: Any, *, environment: str, paper_session_id: Any) -> None:
        raise RuntimeError("the trace store is unavailable")

    with caplog.at_level("ERROR"):
        outcome = _run_coroutine(
            paper_sessions._record_signal(broken, action_output(), session_id=SESSION_ID)
        )
    assert outcome is None  # swallowed, so the caller submits the intent
    assert "could not be written" in caplog.text
    assert SESSION_ID in caplog.text


def test_a_persistence_failure_inside_the_recorder_is_contained_the_same_way(caplog):
    """The real failure mode: the INSERT itself does not land."""
    client = FakeSignalsClient(probe_error="connection reset by peer")

    def unusable_insert(_payload: Dict[str, Any]) -> Any:
        raise RuntimeError("insert did not complete")

    client.insert = unusable_insert  # type: ignore[assignment]
    record = runtime.build_session_signal_recorder(
        client, session=paper_session_row(), version_row=version_row()
    )
    with caplog.at_level("ERROR"):
        outcome = _run_coroutine(
            paper_sessions._record_signal(record, action_output(), session_id=SESSION_ID)
        )
    assert outcome is None
    assert "the paper order still stands" in caplog.text


def test_the_recorder_is_exported_for_the_layer_that_installs_it():
    assert "build_session_signal_recorder" in runtime.__all__
