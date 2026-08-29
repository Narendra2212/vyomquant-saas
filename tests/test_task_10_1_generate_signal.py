"""
Task 10.1 - ``generate_signal(deployment, node_output) -> Signal``.

Requirements 15.1, 15.2, 15.3, 15.4, 15.5. Three things are under test and nothing else:

  1. THE RECORD'S SHAPE. Task 10.1 owns it (task 9.1 deliberately left it here), so the
     field set, its immutability, its derived Idempotency_Key and its projection onto the
     columns ``public.signals`` actually has are all pinned here.

  2. THE MINT. A signal is minted with a globally unique id, at ``GENERATED``, carrying
     every field Requirement 15.2 lists - and is REFUSED, before any id is consumed, when
     the inputs do not describe an actionable, attributable, sized decision.

  3. THE PERSIST. A ``Signal`` comes back only when a row exists (Requirement 15.5); a
     database without migration 005b degrades with a warning naming the file rather than
     failing the write.

What is NOT tested here, because it is not this task's:
  * ``submit_signal``, the idempotency-guarded submission and every transition after
    GENERATED - task 10.2. The one thing asserted about it here is the seam: the state a
    minted signal is in must be able to reach PENDING through
    ``assert_transition_legal``.
  * The Live_Runtime wiring, stale-feed suspension and per-event error containment -
    task 10.3.
  * ``idempotency_key_for``'s own determinism - task 9.1, covered by
    ``tests/test_task_9_1_signal_idempotency_key.py`` and property test 10.7.
  * The universal-quantifier versions of the credential-containment and
    id-uniqueness claims - tasks 10.6 and 10.7 (Properties 10 and 11). The cases here
    are the concrete examples.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend_app.backend import signal_service as svc
from backend_app.backend.order_lifecycle_state import (
    OrderLifecycleState,
    assert_transition_legal,
    resolve_order_lifecycle_state,
)
from backend_app.backend.signal_service import (
    INITIAL_ORDER_LIFECYCLE_STATE,
    SIGNAL_LIFECYCLE_COLUMNS,
    SIGNAL_LIFECYCLE_MIGRATION,
    Signal,
    SignalGenerationRefused,
    SignalPersistenceError,
    generate_signal,
    mint_signal,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNALS_DDL = REPO_ROOT / "backend_app" / "migrations" / "003_signal_trace_restoration.sql"


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES - a deployment, an action-node output, and a fake PostgREST client
# ══════════════════════════════════════════════════════════════════════════

#: Strings that must never reach a signal record. Values, not key names, so the assertion
#: is about what travelled and not about how it was spelled.
CREDENTIAL_MARKERS = (
    "AKIAWHATEVER1234",
    "s3cr3t-shhh",
    "my-passphrase",
    "eyJhbGciOiJIUzI1NiJ9.token",
    "EXCHANGE-ISSUED-ACCOUNT-99887766",
)


def deployment_row(**overrides):
    """A ``strategy_deployments`` row, deliberately carrying credentials it must not leak.

    A real row does not hold keys - ``deployment_binding`` stores only
    ``exchange_account_id`` - but the containment claim in Requirements 15.3/15.4/20.3 is
    that a credential CANNOT travel even if the input holds one, so the input holds one.
    """
    row = {
        "id": "dep-1111",
        "user_id": "user-aaaa",
        "strategy_id": "strat-bbbb",
        "version": "v3",
        "version_id": "ver-cccc",
        "exchange_account_id": "acct-dddd",
        "exchange_id": "kraken",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "mode": "paper",
        "worker_id": "worker-7",
        # ── none of the following may reach a signal ──
        "api_key": CREDENTIAL_MARKERS[0],
        "api_secret": CREDENTIAL_MARKERS[1],
        "passphrase": CREDENTIAL_MARKERS[2],
        "access_token": CREDENTIAL_MARKERS[3],
        "exchange_account_number": CREDENTIAL_MARKERS[4],
    }
    row.update(overrides)
    return row


def action_output(**overrides):
    """What an ACTION node evaluated to, plus the risk verdict it was validated against."""
    output = {
        "decision": "BUY",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "quantity": 0.25,
        "price": 61234.5,
        "bar_time": "2024-05-01T12:00:00+00:00",
        "source_node_ids": ["action-1"],
        "closure_ready": True,
        "node_closure": {
            "data-1": {"node_type": "DATA", "ready": True, "value": 61234.5},
            "rsi-1": 28.4,
            "logic-1": {"node_type": "LOGIC", "ready": True, "value": True},
        },
        "risk_validation": {
            "passed": True,
            "reason": "within limits",
            "position_size": 0.25,
            "capital": 10000.0,
            "exposure": 0.15,
            "expected_loss": 120.0,
            "expected_reward": 380.0,
            "drawdown_check": True,
            "evaluated_at": "2024-05-01T12:00:01+00:00",
        },
        "ml_inference": {
            "model_id": "model-eeee",
            "model_version": "3",
            "prediction": "BUY",
            "confidence": 0.81,
            "probabilities": {"BUY": 0.81, "SELL": 0.19},
        },
    }
    output.update(overrides)
    return output


class _Result:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


class FakeSupabase:
    """The smallest client that answers the two calls this path makes.

    ``select(...).limit(...).execute()`` is the 005b column probe;
    ``insert(payload).execute()`` is the write. Both are recorded, because what reached
    the database is the whole question.
    """

    def __init__(self, *, probe_error=None, insert_error=None, insert_returns=None,
                 raise_on_insert=None):
        self.probe_error = probe_error
        self.insert_error = insert_error
        self.insert_returns = insert_returns
        self.raise_on_insert = raise_on_insert
        self.inserted = []
        self.selected = []

    # table() -> self, so the chain reads like the real client's
    def table(self, name):
        assert name == "signals"
        return self

    def select(self, columns):
        self.selected.append(columns)
        self._pending = ("select", columns)
        return self

    def limit(self, _n):
        return self

    def insert(self, payload):
        self._pending = ("insert", payload)
        return self

    def execute(self):
        kind, payload = self._pending
        if kind == "select":
            if self.probe_error is not None:
                raise Exception(self.probe_error)
            return _Result(data=[])
        self.inserted.append(payload)
        if self.raise_on_insert is not None:
            error = self.raise_on_insert
            # A missing-column error is raised once; a retry without those columns lands.
            if all(column not in payload for column in SIGNAL_LIFECYCLE_COLUMNS):
                error = None
            if error is not None:
                raise Exception(error)
        if self.insert_error is not None:
            return _Result(error=self.insert_error)
        if self.insert_returns is not None:
            return _Result(data=self.insert_returns)
        return _Result(data=[dict(payload)])


@pytest.fixture(autouse=True)
def _forget_migration_verdict():
    """Each test probes 005b for itself: the verdict is cached per process."""
    svc.reset_signal_lifecycle_column_support()
    yield
    svc.reset_signal_lifecycle_column_support()


def flat_text(value) -> str:
    """Every string anywhere in ``value``, as one blob, for a containment scan."""
    return json.dumps(value, default=str)


# ══════════════════════════════════════════════════════════════════════════
# 1. THE RECORD'S SHAPE (Requirements 15.1, 15.2)
# ══════════════════════════════════════════════════════════════════════════


def test_initial_state_is_generated_and_is_read_from_the_resolver():
    """GENERATED is a real state a signal starts in, not a transcribed default."""
    assert INITIAL_ORDER_LIFECYCLE_STATE is OrderLifecycleState.GENERATED
    assert INITIAL_ORDER_LIFECYCLE_STATE == resolve_order_lifecycle_state()


def test_minted_signal_carries_every_field_requirement_15_2_lists():
    signal = mint_signal(deployment_row(), action_output())

    # identity + attribution
    assert signal.id
    assert signal.user_id == "user-aaaa"
    assert signal.strategy_id == "strat-bbbb"
    assert signal.strategy_version == "v3"
    assert signal.strategy_version_id == "ver-cccc"
    assert signal.deployment_id == "dep-1111"
    assert signal.symbol == "BTC/USDT"
    assert signal.timeframe == "1h"
    assert signal.mode == "paper"
    assert signal.worker_id == "worker-7"
    assert signal.generated_at.endswith("+00:00")

    # the decision
    assert signal.decision == "BUY"
    assert signal.signal_type == "ENTRY"
    assert signal.side == "BUY"
    assert signal.quantity == 0.25
    assert signal.source_node_ids == ("action-1",)

    # decision metadata: the evaluated state of every upstream node in the closure
    assert set(signal.node_closure) == {"data-1", "rsi-1", "logic-1"}
    assert signal.node_closure["rsi-1"] == 28.4
    assert signal.node_closure["data-1"]["ready"] is True
    assert signal.closure_ready is True

    # risk verdict + ML inference
    assert signal.risk_passed is True
    assert signal.risk_validation["reason"] == "within limits"
    assert signal.ml_inference["model_id"] == "model-eeee"
    assert signal.ml_inference["confidence"] == 0.81

    # lifecycle + idempotency
    assert signal.order_lifecycle_state is OrderLifecycleState.GENERATED
    assert signal.idempotency_key == f"signal:{signal.id}"

    # the order/execution reference is genuinely absent until it exists
    assert signal.order_id is None
    assert signal.execution_id is None


def test_signal_is_immutable_and_the_order_reference_arrives_as_a_copy():
    """Requirement 15.1's "immutable once assigned", enforced by the language."""
    signal = mint_signal(deployment_row(), action_output())

    with pytest.raises(Exception):
        signal.id = "something-else"  # frozen dataclass
    with pytest.raises(Exception):
        signal.order_lifecycle_state = OrderLifecycleState.EXECUTED

    referenced = signal.with_order_reference(order_id="ord-9", execution_id="exe-9")
    assert referenced is not signal
    assert (referenced.order_id, referenced.execution_id) == ("ord-9", "exe-9")
    assert signal.order_id is None and signal.execution_id is None
    assert referenced.id == signal.id
    assert referenced.idempotency_key == signal.idempotency_key


def test_idempotency_key_is_derived_not_stored():
    """A stored copy could disagree with the derivation after a database round-trip."""
    signal = mint_signal(deployment_row(), action_output())
    assert "idempotency_key" not in {f.name for f in signal.__dataclass_fields__.values()}
    assert signal.idempotency_key == f"signal:{signal.id}"
    # And it reads nothing but the id: a signal differing in every other field, same id,
    # derives the same key.
    twin = mint_signal(
        deployment_row(symbol="ETH/USDT", mode="live"),
        action_output(decision="SELL", symbol="ETH/USDT"),
        signal_id=signal.id,
    )
    assert twin.idempotency_key == signal.idempotency_key


def test_minted_ids_are_unique_across_a_bulk_mint():
    """Requirement 15.1. The universal version is property test 10.7."""
    ids = {mint_signal(deployment_row(), action_output()).id for _ in range(500)}
    assert len(ids) == 500


def test_generated_state_can_reach_pending_through_the_gate():
    """The seam task 10.2 starts from: the minted state must have a legal next step."""
    signal = mint_signal(deployment_row(), action_output())
    current, target = assert_transition_legal(
        signal.order_lifecycle_state, OrderLifecycleState.PENDING, signal_id=signal.id
    )
    assert (current, target) == (OrderLifecycleState.GENERATED, OrderLifecycleState.PENDING)


# ══════════════════════════════════════════════════════════════════════════
# 2. CREDENTIAL CONTAINMENT, STRUCTURALLY (Requirements 15.3, 15.4, 20.3)
# ══════════════════════════════════════════════════════════════════════════


def test_no_credential_field_exists_on_the_record():
    """Containment by construction: there is no field for a secret to land in."""
    names = set(Signal.__dataclass_fields__)
    for banned in ("api_key", "api_secret", "secret", "passphrase", "access_token",
                   "token", "credentials", "keys", "extra", "raw"):
        assert banned not in names, f"Signal must not have a {banned} field"
    # The only field naming an account is the platform-internal reference 20.3 exempts.
    assert "exchange_account_id" in names


def test_credentials_in_the_inputs_cannot_reach_the_record_the_row_or_the_wire():
    output = action_output(
        api_key=CREDENTIAL_MARKERS[0],
        api_secret=CREDENTIAL_MARKERS[1],
        passphrase=CREDENTIAL_MARKERS[2],
        access_token=CREDENTIAL_MARKERS[3],
    )
    signal = mint_signal(deployment_row(), output)

    for rendering in (signal.to_row(), signal.to_public_dict(), signal.decision_metadata):
        blob = flat_text(rendering)
        for marker in CREDENTIAL_MARKERS:
            assert marker not in blob, f"{marker} leaked into {flat_text(rendering)[:120]}"

    # The exempt internal reference IS carried, and is the only account naming present.
    assert signal.exchange_account_id == "acct-dddd"
    assert "acct-dddd" in flat_text(signal.to_row())


def test_the_log_line_for_a_generated_signal_carries_no_credential(caplog):
    import asyncio

    client = FakeSupabase()
    with caplog.at_level("INFO"):
        asyncio.run(generate_signal(deployment_row(), action_output(), sb=client))
    for marker in CREDENTIAL_MARKERS:
        assert marker not in caplog.text


# ══════════════════════════════════════════════════════════════════════════
# 3. THE ROW PROJECTION - only columns public.signals actually has
# ══════════════════════════════════════════════════════════════════════════


def signals_columns_from_ddl() -> set:
    """The column names ``003_signal_trace_restoration.sql`` declares on ``signals``."""
    ddl = SIGNALS_DDL.read_text(encoding="utf-8")
    body = re.search(
        r"CREATE TABLE IF NOT EXISTS signals \((.*?)\n\);", ddl, re.DOTALL
    ).group(1)
    columns = set()
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        name = line.split()[0].strip(",")
        if name.upper() in ("CONSTRAINT", "PRIMARY", "UNIQUE", "CHECK", "FOREIGN"):
            continue
        columns.add(name)
    return columns


def test_to_row_writes_only_existing_columns_plus_005b_s_two():
    known = signals_columns_from_ddl() | set(SIGNAL_LIFECYCLE_COLUMNS)
    assert "exchange_id" in known and "status" in known  # the parse found the real table

    row = mint_signal(deployment_row(), action_output()).to_row()
    unknown = set(row) - known
    assert not unknown, f"generate_signal would write column(s) that do not exist: {unknown}"


def test_to_row_maps_requirement_15_2_onto_the_columns_that_exist():
    signal = mint_signal(deployment_row(), action_output())
    row = signal.to_row()

    assert row["id"] == signal.id
    assert row["user_id"] == "user-aaaa"
    assert row["strategy_id"] == "strat-bbbb"
    assert row["strategy_version"] == "v3"
    assert row["deployment_id"] == "dep-1111"
    assert row["exchange_id"] == "kraken"          # the venue slug, NOT NULL on this table
    assert row["symbol"] == "BTC/USDT"
    assert row["decision"] == "BUY"
    assert row["quantity"] == 0.25
    assert row["order_lifecycle_state"] == "GENERATED"
    assert row["idempotency_key"] == f"signal:{signal.id}"

    # decision metadata over the three JSONB columns this table has
    assert row["indicators"]["rsi-1"] == 28.4
    assert row["market_info"]["signal_type"] == "ENTRY"
    assert row["market_info"]["side"] == "BUY"
    assert row["market_info"]["exchange_account_id"] == "acct-dddd"
    assert row["market_info"]["strategy_version_id"] == "ver-cccc"
    assert row["market_info"]["source_node_ids"] == ["action-1"]
    assert row["market_info"]["price"] == 61234.5
    assert row["ml_info"]["prediction"] == "BUY"

    # the risk verdict over the columns 002/003 already provide for it
    assert row["risk_passed"] is True
    assert row["risk_reason"] == "within limits"
    assert row["position_size"] == 0.25
    assert row["drawdown_check"] is True

    # the legacy status column is left to its own default, and no order reference is
    # invented before one exists
    assert "status" not in row
    assert "order_id" not in row and "trade_id" not in row


def test_to_row_is_json_serialisable_for_awkward_node_values():
    """Decimals, datetimes, enums and a deep nest all have to reach JSONB intact."""
    from decimal import Decimal

    output = action_output(
        node_closure={
            "dec-1": Decimal("1.5"),
            "time-1": datetime(2024, 5, 1, 12, tzinfo=timezone.utc),
            "state-1": OrderLifecycleState.PENDING,
            "nan-1": float("nan"),
            "deep-1": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}},
            "wide-1": list(range(1000)),
        }
    )
    row = mint_signal(deployment_row(), output).to_row()
    json.dumps(row)  # raises if anything survived un-scalarised
    assert row["indicators"]["dec-1"] == 1.5
    assert row["indicators"]["time-1"].startswith("2024-05-01T12:00:00")
    assert row["indicators"]["state-1"] == "PENDING"
    assert row["indicators"]["nan-1"] == "nan"
    assert len(row["indicators"]["wide-1"]) <= 257  # bounded, and says it was


# ══════════════════════════════════════════════════════════════════════════
# 4. REFUSALS - before an id is consumed
# ══════════════════════════════════════════════════════════════════════════


def test_hold_is_not_a_signal():
    with pytest.raises(SignalGenerationRefused) as excinfo:
        mint_signal(deployment_row(), action_output(decision="HOLD"))
    assert excinfo.value.code == "SIGNAL_DECISION_NOT_ACTIONABLE"


def test_an_unrecognised_decision_is_refused_not_guessed():
    with pytest.raises(SignalGenerationRefused) as excinfo:
        mint_signal(deployment_row(), action_output(decision="mabye-buy?"))
    assert excinfo.value.code == "SIGNAL_DECISION_UNRECOGNISED"


@pytest.mark.parametrize(
    "missing", ["user_id", "strategy_id", "version", "exchange_id"]
)
def test_incomplete_attribution_is_refused(missing):
    with pytest.raises(SignalGenerationRefused) as excinfo:
        mint_signal(deployment_row(**{missing: None}), action_output())
    assert excinfo.value.code == "SIGNAL_ATTRIBUTION_INCOMPLETE"


def test_a_failed_risk_verdict_generates_no_signal():
    """Requirement 14.3: a Signal exists only if risk validation passed."""
    output = action_output(
        risk_validation={"passed": False, "reason": "max drawdown breached"}
    )
    with pytest.raises(SignalGenerationRefused) as excinfo:
        mint_signal(deployment_row(), output)
    assert excinfo.value.code == "SIGNAL_RISK_VALIDATION_FAILED"
    assert "max drawdown breached" in json.dumps(excinfo.value.to_detail())


def test_a_blocked_risk_trace_is_read_as_a_failed_verdict():
    output = action_output(risk_validation={"blocked": True, "block_reason": "kill switch"})
    with pytest.raises(SignalGenerationRefused):
        mint_signal(deployment_row(), output)


def test_an_unreported_risk_verdict_is_not_read_as_a_failure():
    """"Not reported" is not "refused" - the gate itself is task 10.3's."""
    output = action_output()
    output.pop("risk_validation")
    signal = mint_signal(deployment_row(), output)
    assert signal.risk_passed is None
    assert signal.risk_validation == {}


def test_a_decision_with_no_size_at_all_is_refused():
    output = action_output()
    for key in ("quantity", "sizing_intention", "strength", "confidence"):
        output.pop(key, None)
    with pytest.raises(SignalGenerationRefused) as excinfo:
        mint_signal(deployment_row(), output)
    assert excinfo.value.code == "SIGNAL_SIZING_UNSPECIFIED"


def test_a_sizing_intention_stands_in_for_a_quantity():
    """Requirement 15.2 asks for a quantity OR a sizing intention."""
    output = action_output(strength=0.7)
    output.pop("quantity")
    signal = mint_signal(deployment_row(), output)
    assert signal.quantity is None
    assert signal.sizing_intention["strength"] == 0.7
    assert signal.sizing_intention["basis"] == "reported_by_action_node"


# ══════════════════════════════════════════════════════════════════════════
# 5. THE DAG'S OWN EMITTER SHAPE
# ══════════════════════════════════════════════════════════════════════════


def test_the_dag_event_loops_signal_shape_is_accepted_as_node_output():
    """``dag_event_loop.Signal`` spells it ``action``/``strength``/``trigger_node``."""
    from backend_app.backend.dag_event_loop import Signal as DagSignal

    emitted = DagSignal(
        timestamp=datetime(2024, 5, 1, 12, tzinfo=timezone.utc),
        symbol="ETH/USDT",
        action="sell",
        strength=0.62,
        trigger_node="action-77",
        confidence=0.62,
        metadata={"rsi": 71.2},
    )
    signal = mint_signal(deployment_row(symbol="ETH/USDT"), emitted)

    assert signal.decision == "SELL"
    assert signal.signal_type == "ENTRY"
    assert signal.side == "SELL"
    assert signal.symbol == "ETH/USDT"
    assert signal.source_node_ids == ("action-77",)
    assert signal.sizing_intention["strength"] == 0.62
    assert signal.market_context["bar_time"].startswith("2024-05-01T12:00:00")


def test_an_exit_decision_has_no_invented_side():
    """The closing side depends on the open position, which this module does not read."""
    signal = mint_signal(deployment_row(), action_output(decision="EXIT"))
    assert signal.decision == "EXIT"
    assert signal.signal_type == "EXIT"
    assert signal.side is None


# ══════════════════════════════════════════════════════════════════════════
# 6. PERSISTENCE (Requirements 15.5, 14.5) AND THE 005b DEGRADATION
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_generate_signal_persists_a_generated_row_and_returns_it():
    client = FakeSupabase()
    signal = await generate_signal(deployment_row(), action_output(), sb=client)

    assert len(client.inserted) == 1
    written = client.inserted[0]
    assert written["id"] == signal.id
    assert written["order_lifecycle_state"] == "GENERATED"
    assert written["idempotency_key"] == f"signal:{signal.id}"
    assert written["user_id"] == "user-aaaa"
    # the probe ran against the two 005b columns, through the caller's own client
    assert client.selected == [",".join(SIGNAL_LIFECYCLE_COLUMNS)]


@pytest.mark.asyncio
async def test_a_write_failure_raises_and_returns_no_signal():
    """Requirement 15.5: an unpersisted signal must not be reportable or routable."""
    client = FakeSupabase(insert_error="connection reset by peer")
    with pytest.raises(SignalPersistenceError) as excinfo:
        await generate_signal(deployment_row(), action_output(), sb=client)
    assert excinfo.value.code == "SIGNAL_NOT_PERSISTED"
    assert excinfo.value.http_status == 500


@pytest.mark.asyncio
async def test_an_insert_returning_no_row_is_a_persistence_failure():
    client = FakeSupabase(insert_returns=[])
    with pytest.raises(SignalPersistenceError):
        await generate_signal(deployment_row(), action_output(), sb=client)


@pytest.mark.asyncio
async def test_no_client_is_a_persistence_failure_not_an_in_memory_fallback():
    deployment = deployment_row()
    deployment.pop("access_token")
    with pytest.raises(SignalPersistenceError) as excinfo:
        await generate_signal(deployment, action_output(), sb=None, user={})
    assert excinfo.value.code == "SIGNAL_NO_PERSISTENCE_CLIENT"


@pytest.mark.asyncio
async def test_an_unapplied_005b_degrades_with_a_warning_naming_the_file(caplog):
    """The migration is applied by hand, so its absence must not be a 500."""
    client = FakeSupabase(
        probe_error='column signals.order_lifecycle_state does not exist (42703)'
    )
    with caplog.at_level("WARNING"):
        signal = await generate_signal(deployment_row(), action_output(), sb=client)

    assert signal.order_lifecycle_state is OrderLifecycleState.GENERATED  # in memory
    written = client.inserted[0]
    for column in SIGNAL_LIFECYCLE_COLUMNS:
        assert column not in written
    assert written["id"] == signal.id  # the signal itself is still persisted
    assert SIGNAL_LIFECYCLE_MIGRATION in caplog.text
    assert svc.signal_lifecycle_column_support_state() is False


@pytest.mark.asyncio
async def test_a_missing_column_discovered_at_the_insert_retries_without_it(caplog):
    """A process that cached a positive verdict learns the truth at the write."""
    client = FakeSupabase(
        raise_on_insert="PGRST204: Could not find the 'idempotency_key' column of "
        "'signals' in the schema cache"
    )
    with caplog.at_level("WARNING"):
        signal = await generate_signal(deployment_row(), action_output(), sb=client)

    assert len(client.inserted) == 2          # the full write, then the legacy one
    assert "order_lifecycle_state" in client.inserted[0]
    assert "order_lifecycle_state" not in client.inserted[1]
    assert client.inserted[1]["id"] == signal.id
    assert SIGNAL_LIFECYCLE_MIGRATION in caplog.text


@pytest.mark.asyncio
async def test_the_stored_id_wins_over_the_minted_one():
    """The Idempotency_Key must guard the row that exists, not the one intended."""
    client = FakeSupabase(insert_returns=[{"id": "id-the-database-chose"}])
    signal = await generate_signal(deployment_row(), action_output(), sb=client)
    assert signal.id == "id-the-database-chose"
    assert signal.idempotency_key == "signal:id-the-database-chose"
