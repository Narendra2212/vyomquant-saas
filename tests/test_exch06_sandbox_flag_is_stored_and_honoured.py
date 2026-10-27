"""EXCH-06 — the credential UI's "Testnet / Sandbox Mode" toggle must actually do something.

WHAT WAS WRONG
--------------
`core/exchange_connection_schema.py` offered the trader a boolean field::

    ExchangeCredentialField(field_id="sandbox", label="Testnet / Sandbox Mode",
                            type=FieldType.BOOLEAN,
                            description="Connect to Binance Testnet instead of Production.")

and the chain from that toggle to a connection was broken at every single link:

===============================  =========================================================
link                             state before this change
===============================  =========================================================
``ExchangeKeysRequest``          no ``sandbox`` field, and no ``extra="forbid"`` -- so a
                                 client that posted one got HTTP 200 and pydantic
                                 silently discarded it
``exchange_keys`` table          no ``sandbox`` column (queried against the live
                                 production database, not read off a migration)
``store_exchange_keys``          upserted a dict of exactly five keys; the toggle had
                                 nowhere to land
``load_decrypted_keys``          ``SELECT *`` but then hand-built a return dict of
                                 exactly ``api_key``/``secret_key``/``password``
``order_control._executor_for``  already read ``keys.get("sandbox")`` -- which against a
                                 three-key dict is ``False`` **unconditionally**
===============================  =========================================================

So a trader who ticked the box got a PRODUCTION connection, with no error and no warning,
on a venue where their real balance lives.

Not an active incident: those paths cancel and read rather than open positions, live
trading is hard-gated off (RT-15), and the only input that could arm the executor's
sandbox verifier was a ``"testnet": true`` key inside a strategy blueprint, which nothing
writes. **What was wrong was the promise.**

WHAT THESE TESTS ARE, AND ARE NOT
---------------------------------
Unit tests over a recording fake of the Supabase client. They prove the FLAG's journey:
request model -> vault write -> vault read -> executor argument. They contact no venue,
use no real credential, and prove nothing about whether any venue's sandbox works -- that
is EXCH-01's job and is tested in `test_exch01_sandbox_endpoint_verification.py`.
"""
from __future__ import annotations

import pytest

from backend_app.backend.exchange_executor import (SANDBOX_MECHANISMS,
                                                   SandboxMechanism)
from backend_app.core.exchange_connection_schema import \
    get_exchange_connection_schema_registry
from backend_app.core.models.pydantic_models import ExchangeKeysRequest

# ══════════════════════════════════════════════════════════════════════════════════════
#  A recording stand-in for the Supabase client the vault writes through
# ══════════════════════════════════════════════════════════════════════════════════════


class _Result:
    def __init__(self, data):
        self.data = data


class _RecordingTable:
    """One `supabase.table("exchange_keys")` chain.

    IT MODELS THE DESTRUCTIVE UPSERT SEMANTICS ON PURPOSE, and that choice is the
    difference between the rotation test below proving something and proving nothing.

    A PostgREST upsert emits `INSERT ... ON CONFLICT DO UPDATE SET <payload columns>`,
    and whether a column ABSENT from the payload keeps its value or is reset is a
    property of that implementation. This fake takes the UNFORGIVING reading -- the
    payload replaces the row -- because the production code must be correct under
    either, and a fake that merged would let `rotate_key` forget to carry the sandbox
    flag and still look right. Making the stricter assumption here is what forces the
    flag to be threaded explicitly rather than relied upon.
    """

    def __init__(self, store: dict, name: str):
        self._store = store
        self._name = name
        self._op: tuple = ("none", None)
        self._filters: dict = {}

    def upsert(self, data):
        self._op = ("upsert", dict(data))
        return self

    def select(self, _projection):
        self._op = ("select", None)
        return self

    def eq(self, column, value):
        self._filters[column] = value
        return self

    def execute(self):
        kind, payload = self._op
        if kind == "upsert":
            self._store.setdefault("upserts", []).append(dict(payload))
            key = (payload.get("user_id"), payload.get("exchange_id"))
            # Replace, do not merge. See the class docstring.
            self._store.setdefault("rows", {})[key] = dict(payload)
            return _Result([dict(payload)])
        key = (self._filters.get("user_id"), self._filters.get("exchange_id"))
        row = self._store.get("rows", {}).get(key)
        return _Result([dict(row)] if row else [])


class _RecordingSupabase:
    def __init__(self):
        self.store: dict = {}

    def table(self, name):
        return _RecordingTable(self.store, name)


@pytest.fixture
def vault():
    """An APIKeyVault with a REAL cipher and a recording database.

    `__init__` is bypassed deliberately: it requires `SUPABASE_URL` and
    `SUPABASE_SERVICE_ROLE_KEY` and calls `create_client`, none of which the three
    methods under test touch -- they use `self.supabase` and `self._cipher` only.
    Constructing it for real would make this a test of the constructor's environment.

    The CIPHER IS REAL, built here rather than faked, so `store` -> `load` has to
    round-trip through actual Fernet encryption. A stubbed cipher would let the round-trip
    pass by comparing plaintext to plaintext and would prove nothing about the credential
    surviving, which is the thing a sandbox flag must not break.
    """
    from cryptography.fernet import Fernet, MultiFernet

    from backend_app.backend.api_key_vault import APIKeyVault

    v = APIKeyVault.__new__(APIKeyVault)
    v._cipher = MultiFernet([Fernet(Fernet.generate_key())])
    fake = _RecordingSupabase()
    v.supabase = fake
    v._recording = fake
    return v


# ══════════════════════════════════════════════════════════════════════════════════════
#  1 — THE REQUEST MODEL CAN CARRY THE ANSWER
# ══════════════════════════════════════════════════════════════════════════════════════

def test_the_request_model_accepts_the_toggle_the_ui_offers():
    body = ExchangeKeysRequest(
        exchange_id="binance", api_key="k", secret_key="s", sandbox=True
    )
    assert body.sandbox is True


def test_omitting_the_toggle_means_production():
    """The default must preserve today's behaviour exactly, for every existing caller."""
    body = ExchangeKeysRequest(exchange_id="binance", api_key="k", secret_key="s")
    assert body.sandbox is False


def test_the_field_name_matches_what_the_credential_ui_asks_for():
    """The form field and the request field are declared in two different modules.

    They were already out of step -- that is the defect -- so this asserts the exact
    `field_id` the schema serves is a name the body model can receive. A rename on
    either side without the other would put the flag straight back in the bin.
    """
    registry = get_exchange_connection_schema_registry()
    binance = registry.get_schema("binance")
    offered = {f.field_id for f in binance.fields}

    assert "sandbox" in offered, "the UI no longer offers the toggle this test is about"
    assert "sandbox" in ExchangeKeysRequest.model_fields, (
        "the UI offers a 'sandbox' field the request body cannot receive -- which is "
        "EXCH-06 exactly"
    )


# ══════════════════════════════════════════════════════════════════════════════════════
#  2 — THE VAULT STORES IT AND READS IT BACK
# ══════════════════════════════════════════════════════════════════════════════════════

def test_a_sandbox_credential_is_written_with_the_flag_set(vault):
    vault.store_exchange_keys(
        user_id="user-1", exchange_id="binance",
        raw_api_key="key", raw_secret="secret", sandbox=True,
    )

    written = vault._recording.store["upserts"][-1]
    assert written["sandbox"] is True
    # And the flag is NOT encrypted -- it is routing, not a secret, and SQL has to be
    # able to filter on it.
    assert written["sandbox"] is not None
    assert isinstance(written["sandbox"], bool)


def test_the_flag_survives_the_round_trip_to_the_consumer(vault):
    """The whole chain in one assertion: what `_executor_for` would compute.

    `order_control._executor_for` does `bool(keys.get("sandbox") or
    keys.get("testnet"))`. That expression is reproduced here verbatim, because it is
    the thing that was unconditionally False.
    """
    vault.store_exchange_keys(
        user_id="user-1", exchange_id="binance",
        raw_api_key="key", raw_secret="secret", sandbox=True,
    )

    keys = vault.load_decrypted_keys("user-1", "binance")

    assert keys["api_key"] == "key", "the credential itself must still round-trip"
    assert keys["secret_key"] == "secret"
    assert bool(keys.get("sandbox") or keys.get("testnet")) is True, (
        "_executor_for would still build a LIVE executor for a credential the trader "
        "marked as testnet"
    )


def test_a_live_credential_round_trips_as_live(vault):
    vault.store_exchange_keys(
        user_id="user-1", exchange_id="binance",
        raw_api_key="key", raw_secret="secret",
    )
    keys = vault.load_decrypted_keys("user-1", "binance")
    assert bool(keys.get("sandbox") or keys.get("testnet")) is False


def test_a_row_predating_the_column_reads_as_live(vault):
    """A database that has not had migration 060 applied returns no such key.

    `load_decrypted_keys` must answer "production" for it rather than raising, because
    that is the behaviour it had before the column existed and a credential that still
    works must not start failing.
    """
    vault._recording.store["rows"] = {
        ("user-1", "binance"): {
            "encrypted_api_key": vault._encrypt("key"),
            "encrypted_secret_key": vault._encrypt("secret"),
            "encrypted_password": None,
            # no `sandbox` key at all
        }
    }
    keys = vault.load_decrypted_keys("user-1", "binance")
    assert keys["sandbox"] is False
    assert keys["api_key"] == "key"


def test_rotating_a_key_does_not_move_a_testnet_credential_to_production(vault):
    """The trap. `rotate_key` re-encrypts by load-then-store.

    It passes an explicit field list, so any field it forgets is a field the rotation
    silently changes. Forgetting this one would move a credential from testnet to
    production during a routine key rotation -- the single most dangerous direction for
    this flag to drift, and completely invisible.
    """
    vault.store_exchange_keys(
        user_id="user-1", exchange_id="binance",
        raw_api_key="key", raw_secret="secret", sandbox=True,
    )

    vault.rotate_key("user-1", "binance")

    keys = vault.load_decrypted_keys("user-1", "binance")
    assert keys["sandbox"] is True, (
        "a key rotation moved a testnet credential to production"
    )
    assert keys["api_key"] == "key", "rotation corrupted the credential itself"


# ══════════════════════════════════════════════════════════════════════════════════════
#  3 — THE UI MUST NOT OFFER WHAT THE CONNECT PATH WILL REFUSE
# ══════════════════════════════════════════════════════════════════════════════════════

REGISTRY_VENUES = sorted(get_exchange_connection_schema_registry()._definitions)


@pytest.mark.parametrize("venue", REGISTRY_VENUES)
def test_the_toggle_is_only_offered_where_a_sandbox_can_be_proven(venue):
    """The credential form and the MEASURED mechanism table must agree.

    Two independent declarations of the same fact had drifted: the schema claimed
    `sandbox_supported=True` and offered the field for coinbase and kucoin, while
    `SANDBOX_MECHANISMS` -- measured from the installed ccxt and enforced by
    `verify_sandbox_endpoint` -- records both as UNSUPPORTED. EXCH-04 had already
    recorded the same contradiction in the capability registry; this one lived in the
    form the trader actually sees.

    Offering a toggle the connect path refuses is worse than not offering one, because
    the refusal arrives only after a credential has been pasted in.
    """
    registry = get_exchange_connection_schema_registry()
    definition = registry.get_schema(venue)
    offers_toggle = any(f.field_id == "sandbox" for f in definition.fields)

    mechanism = SANDBOX_MECHANISMS.get(venue, (None, None))[0]
    can_be_proven = (
        mechanism is not None and mechanism is not SandboxMechanism.UNSUPPORTED
    )

    if offers_toggle:
        assert can_be_proven, (
            f"{venue}'s credential form offers 'Testnet / Sandbox Mode', but "
            f"SANDBOX_MECHANISMS records it as {mechanism!r}. A trader who ticks it is "
            f"refused at connect time after entering a credential."
        )
    assert definition.sandbox_supported == can_be_proven, (
        f"{venue} reports sandbox_supported={definition.sandbox_supported} while the "
        f"measured mechanism is {mechanism!r}. These are two statements of one fact and "
        f"they must not drift."
    )


def test_the_three_venues_with_no_sandbox_offer_no_toggle():
    """Named explicitly, so this cannot pass by the registry becoming empty."""
    registry = get_exchange_connection_schema_registry()
    for venue in ("coinbase", "kraken", "kucoin"):
        assert SANDBOX_MECHANISMS[venue][0] is SandboxMechanism.UNSUPPORTED
        definition = registry.get_schema(venue)
        assert definition is not None, f"{venue} vanished from the registry"
        assert not any(f.field_id == "sandbox" for f in definition.fields), (
            f"{venue} publishes no sandbox endpoint but its form still offers the toggle"
        )
        assert definition.sandbox_supported is False


def test_the_venues_with_a_real_sandbox_still_offer_it():
    """The positive control. Without it, deleting every toggle would pass the tests above."""
    registry = get_exchange_connection_schema_registry()
    for venue in ("binance", "bybit", "okx"):
        mechanism = SANDBOX_MECHANISMS[venue][0]
        assert mechanism is not SandboxMechanism.UNSUPPORTED
        definition = registry.get_schema(venue)
        assert definition.sandbox_supported is True
        assert any(f.field_id == "sandbox" for f in definition.fields), (
            f"{venue} has a verifiable sandbox but the trader is no longer offered it"
        )
