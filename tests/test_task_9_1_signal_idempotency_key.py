"""
Task 9.1 — signal-scoped idempotency key derivation and configurable result TTL.

Requirement 19.1. Two things are under test, and nothing else:

  1. `idempotency_key_for(signal)` is a deterministic, pure function of the
     signal's own id, and of nothing else — so a network retry, a WebSocket
     reconnect and a worker restart submitting for the SAME signal all compute
     the SAME key, and two distinct signals never collide onto one key.

  2. `execute_with_idempotency` / `store_result` accept an optional
     `result_ttl`, defaulting to the pre-existing `RESULT_TTL` so that no
     caller that predates the signal path changes behaviour, with
     `SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS` (6 h, configurable) as the value
     the trading path passes.

The locking algorithm is explicitly NOT re-tested here — it is reused verbatim
and `tests/test_atomic_idempotency_fix.py` already covers it. What IS asserted
is that adding the TTL override left the in-flight lock's own TTL
(`PROCESSING_TTL`) alone.

The universal-quantifier version of property 1 is task 9.2's property test
(`tests/test_idempotency_key_property.py`, Property 18); the cases here are the
concrete examples and the error paths.
"""

import importlib
import json
from uuid import uuid4

import pytest

from backend_app.core import distributed_idempotency as di
from backend_app.core.distributed_idempotency import (
    DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
    SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
    SIGNAL_KEY_PREFIX,
    DistributedIdempotencyLayer,
    idempotency_key_for,
)


class _Signal:
    """The smallest stand-in for task 10.1's Signal: it has an id."""

    def __init__(self, signal_id, **extra):
        self.id = signal_id
        for name, value in extra.items():
            setattr(self, name, value)


class _RecordingRedis:
    """A minimal store that remembers the expiry each SET was given.

    Needed because the shipped MockRedisClient accepts `ex` and discards it, so
    it cannot answer the only question this half of the task turns on: which TTL
    actually reached Redis.
    """

    def __init__(self):
        self.store = {}
        self.expiries = {}
        self.set_calls = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=False, **kwargs):
        self.set_calls.append({"key": key, "ex": ex, "nx": nx})
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.expiries[key] = ex
        return True

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
            self.expiries.pop(key, None)
        return True

    async def eval_lua(self, script, keys, args):
        """The same contract the layer's Lua script has: [status, value]."""
        key = keys[0]
        lock_payload, processing_ttl = args[0], args[1]
        current = self.store.get(key)
        if current is not None and not str(current).startswith("processing"):
            return [1, current]
        if current is None:
            await self.set(key, lock_payload, ex=int(processing_ttl), nx=True)
            return [0, lock_payload]
        return [0, False]


@pytest.fixture
def redis(monkeypatch):
    """Swap the module-level redis handle the layer talks to."""
    fake = _RecordingRedis()
    monkeypatch.setattr(di, "redis_manager", fake)
    return fake


# ---------------------------------------------------------------------------
# 1) Key derivation
# ---------------------------------------------------------------------------


def test_key_is_the_signal_prefix_plus_the_signal_id():
    signal = _Signal("018f3a2c-9d5e-7c11-9b0f-2a5c8e4d1b33")
    assert (
        idempotency_key_for(signal)
        == "signal:018f3a2c-9d5e-7c11-9b0f-2a5c8e4d1b33"
    )
    assert idempotency_key_for(signal).startswith(SIGNAL_KEY_PREFIX)


def test_derivation_is_stable_across_repeated_calls():
    """A retry recomputes the key; it must get the same answer every time."""
    signal = _Signal(str(uuid4()))
    keys = {idempotency_key_for(signal) for _ in range(25)}
    assert len(keys) == 1


def test_derivation_ignores_every_field_except_the_id():
    """Same id, different everything else — the retry case — is the same key.

    A submission attempt that carries a new timestamp, a new attempt counter and
    a different quantity is still the same DECISION, and must not get a second
    key, or it would become a second order.
    """
    signal_id = str(uuid4())
    first = _Signal(
        signal_id,
        attempt=1,
        generated_at="2024-01-01T00:00:00Z",
        quantity=1.0,
        order_lifecycle_state="GENERATED",
    )
    retry = _Signal(
        signal_id,
        attempt=7,
        generated_at="2024-06-01T12:34:56Z",
        quantity=2.5,
        order_lifecycle_state="PENDING",
    )
    assert idempotency_key_for(first) == idempotency_key_for(retry)


def test_distinct_signals_get_distinct_keys():
    ids = [str(uuid4()) for _ in range(200)]
    keys = {idempotency_key_for(_Signal(sid)) for sid in ids}
    assert len(keys) == len(ids)


def test_ids_differing_only_in_whitespace_stay_distinct():
    """No normalisation: stripping would collapse two real signals onto one key."""
    assert idempotency_key_for(_Signal("abc")) != idempotency_key_for(
        _Signal(" abc")
    )


def test_accepts_a_mapping_and_a_bare_id_identically():
    """A row read back from the database is a dict, not a Signal object."""
    signal_id = str(uuid4())
    assert (
        idempotency_key_for(_Signal(signal_id))
        == idempotency_key_for({"id": signal_id})
        == idempotency_key_for(signal_id)
    )


def test_non_string_ids_are_accepted_and_stringified():
    assert idempotency_key_for(_Signal(41)) == "signal:41"
    uuid_id = uuid4()
    assert idempotency_key_for(_Signal(uuid_id)) == f"signal:{uuid_id}"


@pytest.mark.parametrize(
    "signal",
    [
        _Signal(""),
        _Signal("   "),
        _Signal(None),
        {"id": ""},
        {"id": None},
        {},
        _Signal.__new__(_Signal),  # an object with no id attribute at all
    ],
)
def test_a_signal_without_a_usable_id_is_refused(signal):
    """An unnamed signal has no identity to be idempotent on.

    Deriving a key anyway would hand every unnamed signal the SAME key, which
    would silently suppress real, distinct orders. Fail loudly instead.
    """
    with pytest.raises(ValueError):
        idempotency_key_for(signal)


def test_key_carries_no_credential_and_no_exchange_identity():
    """Requirement 20.3: the key is indexed, logged and sent as a client order id."""
    signal = _Signal(
        "018f3a2c-9d5e-7c11-9b0f-2a5c8e4d1b33",
        api_key="AKIA_LEAKED_KEY",
        api_secret="s3cr3t-do-not-leak",
        passphrase="hunter2",
        access_token="tok_live_abc123",
        exchange_id="binance",
        exchange="binance",
        exchange_account_id="acct-91",
    )
    key = idempotency_key_for(signal)
    for forbidden in (
        "AKIA_LEAKED_KEY",
        "s3cr3t-do-not-leak",
        "hunter2",
        "tok_live_abc123",
        "binance",
        "acct-91",
    ):
        assert forbidden not in key
    assert key == "signal:018f3a2c-9d5e-7c11-9b0f-2a5c8e4d1b33"


# ---------------------------------------------------------------------------
# 2) Configurable result TTL
# ---------------------------------------------------------------------------


def test_signal_ttl_default_is_six_hours():
    assert DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS == 6 * 60 * 60
    assert SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS > 0


def test_signal_ttl_is_longer_than_the_layers_own_default():
    """The whole point of the override: 1 h is too short for a signal's lifecycle."""
    assert (
        SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS
        > DistributedIdempotencyLayer.RESULT_TTL
    )


def test_the_class_default_result_ttl_is_not_widened_globally():
    """Every pre-existing caller keeps the 1-hour window it had."""
    assert DistributedIdempotencyLayer.RESULT_TTL == 3600
    assert DistributedIdempotencyLayer.PROCESSING_TTL == 60


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("900", 900),
        ("  900  ", 900),
        (None, DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS),
        ("", DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS),
        ("not-a-number", DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS),
        ("0", DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS),
        ("-5", DEFAULT_SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS),
    ],
)
def test_ttl_is_configured_from_the_environment_and_never_crashes_import(
    monkeypatch, raw, expected
):
    """Configured, not hardcoded — and a bad override falls back, loudly logged.

    A zero or negative TTL would mean "no idempotency window at all", which is
    the one value this layer must not adopt from an operator typo.
    """
    if raw is None:
        monkeypatch.delenv("SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS", raising=False)
    else:
        monkeypatch.setenv("SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS", raw)
    assert di._read_signal_result_ttl() == expected


def test_resolve_result_ttl_defaults_to_the_existing_constant():
    layer = DistributedIdempotencyLayer()
    assert layer._resolve_result_ttl(None) == DistributedIdempotencyLayer.RESULT_TTL
    assert layer._resolve_result_ttl(21600) == 21600


@pytest.mark.parametrize("bad", [0, -1, "abc", 3.5j, object()])
def test_resolve_result_ttl_refuses_a_non_positive_or_unparseable_override(bad):
    layer = DistributedIdempotencyLayer()
    with pytest.raises(ValueError):
        layer._resolve_result_ttl(bad)


@pytest.mark.asyncio
async def test_store_result_uses_the_one_hour_default_when_no_override(redis):
    layer = DistributedIdempotencyLayer()
    key = await layer.store_result("tenant-a", "clord-1", {"ok": True})
    assert redis.expiries[key] == DistributedIdempotencyLayer.RESULT_TTL


@pytest.mark.asyncio
async def test_store_result_honours_an_explicit_override(redis):
    layer = DistributedIdempotencyLayer()
    key = await layer.store_result(
        "tenant-a",
        "clord-2",
        {"ok": True},
        result_ttl=SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
    )
    assert redis.expiries[key] == SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS


@pytest.mark.asyncio
async def test_execute_with_idempotency_stores_the_result_under_the_override(redis):
    """The signal path end to end: derived key in, 6-hour result TTL out."""
    layer = DistributedIdempotencyLayer()
    signal = _Signal(str(uuid4()))
    key = idempotency_key_for(signal)

    async def submit():
        return {"exchange_order_id": "X-1"}

    result = await layer.execute_with_idempotency(
        tenant_id="tenant-a",
        client_order_id=key,
        operation=submit,
        result_ttl=SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
    )

    assert result == {"exchange_order_id": "X-1"}
    redis_key = f"idempotency:tenant-a:{key}"
    assert redis.expiries[redis_key] == SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS
    assert json.loads(redis.store[redis_key])["client_order_id"] == key


@pytest.mark.asyncio
async def test_the_in_flight_lock_ttl_is_untouched_by_the_override(redis):
    """`result_ttl` widens the COMPLETED-result window only.

    PROCESSING_TTL bounds how long a crashed worker can hold the lock; widening
    it would let a dead worker block a signal for hours.
    """
    layer = DistributedIdempotencyLayer()

    async def submit():
        return {"ok": True}

    await layer.execute_with_idempotency(
        tenant_id="tenant-a",
        client_order_id="signal:lock-ttl-check",
        operation=submit,
        result_ttl=SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
    )

    lock_sets = [c for c in redis.set_calls if c["nx"]]
    assert lock_sets, "expected the Lua path to have taken a processing lock"
    assert all(
        c["ex"] == DistributedIdempotencyLayer.PROCESSING_TTL for c in lock_sets
    )


@pytest.mark.asyncio
async def test_result_ttl_is_consumed_by_the_layer_not_forwarded_to_the_operation(
    redis,
):
    """Same treatment `exchange_id` already gets, so an operation signature
    that has no `result_ttl` parameter keeps working."""
    layer = DistributedIdempotencyLayer()
    seen = {}

    async def submit(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    await layer.execute_with_idempotency(
        tenant_id="tenant-a",
        client_order_id="signal:forwarding-check",
        operation=submit,
        result_ttl=SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
        symbol="BTC/USDT",
    )

    assert seen == {"symbol": "BTC/USDT"}


@pytest.mark.asyncio
async def test_a_bad_override_is_rejected_before_the_operation_runs(redis):
    """Fail up front, not after the order has already been placed."""
    layer = DistributedIdempotencyLayer()
    ran = []

    async def submit():
        ran.append(True)
        return {"ok": True}

    with pytest.raises(ValueError):
        await layer.execute_with_idempotency(
            tenant_id="tenant-a",
            client_order_id="signal:bad-ttl",
            operation=submit,
            result_ttl=0,
        )

    assert ran == []
    assert redis.set_calls == []


@pytest.mark.asyncio
async def test_a_second_attempt_under_the_same_derived_key_returns_the_cached_result(
    redis,
):
    """Requirement 19.1: at most one order per signal, however many attempts."""
    layer = DistributedIdempotencyLayer()
    signal = _Signal(str(uuid4()))
    key = idempotency_key_for(signal)
    submissions = []

    async def submit():
        submissions.append(True)
        return {"exchange_order_id": f"X-{len(submissions)}"}

    first = await layer.execute_with_idempotency(
        tenant_id="tenant-a",
        client_order_id=key,
        operation=submit,
        result_ttl=SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
    )
    second = await layer.execute_with_idempotency(
        tenant_id="tenant-a",
        client_order_id=idempotency_key_for(signal),
        operation=submit,
        result_ttl=SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS,
    )

    assert len(submissions) == 1
    assert first == second == {"exchange_order_id": "X-1"}


def test_signal_service_re_exports_the_derivation():
    """design.md's module map puts `idempotency_key_for` on signal_service."""
    signal_service = importlib.import_module("backend_app.backend.signal_service")
    assert signal_service.idempotency_key_for is idempotency_key_for
    assert (
        signal_service.SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS
        == SIGNAL_IDEMPOTENCY_RESULT_TTL_SECONDS
    )
