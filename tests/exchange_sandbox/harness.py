"""Phase 10 / Workstream E — shared constants and safety helpers for sandbox certification.

Separated from ``conftest.py`` for the reason ``tests/sandbox_lifecycle/harness.py`` is:
a conftest is not importable as a module from a sibling test file, and these constants
are the subject of assertions as well as the machinery behind them.

THE SAFETY INVARIANT THIS MODULE EXISTS TO HOLD
-----------------------------------------------
A test in this directory must be unable to send an order to a production endpoint even if
it tries. Three independent mechanisms, because one of them is one editing mistake away
from failing silently:

1. :data:`SANDBOX_ROUTABLE` — a name allowlist derived by MEASUREMENT, not from any
   registry claim in the codebase (several of those are wrong; see EXCH-04).
2. :func:`require_sandbox_credentials` — credentials must arrive under names carrying an
   explicit ``SANDBOX`` token, so a production key sitting in the ambient environment
   cannot be picked up by accident.
3. ``_assert_not_production`` in the lifecycle suite — the CONNECTED client is re-measured
   against a pristine reference client immediately before the first order. This is the
   one that catches EXCH-01, where a venue accepts sandbox mode and changes nothing.
"""
from __future__ import annotations

import json
import os

import pytest

#: The opt-in collection gate. Absent -> the whole directory skips.
GATE_ENV = "AERORA_EXCHANGE_SANDBOX_CERT"

#: Venues whose ORDER path demonstrably leaves production when sandbox mode is armed.
#:
#: Measured, not claimed. ``staging/probe_phase10_exchange_sandbox.py`` constructs each
#: client exactly as ``CCXTExchangeExecutor.connect`` does and compares the resulting REST
#: base URL and header set against an unarmed client on ccxt 4.3.92:
#:
#:   binance -> ``urls['api']`` replaced by ``urls['test']``       (testnet.binance*)
#:   bybit   -> ``urls['api']`` replaced by ``urls['test']``       (api-testnet.*)
#:   okx     -> URL unchanged, header ``x-simulated-trading: 1``   (demo trading)
#:
#: Deliberately EXCLUDED, with the measured reason:
#:
#:   coinbase, kraken, kucoin
#:       ``connect(sandbox=True)`` RAISES ccxt ``NotSupported`` from the constructor. No
#:       sandbox exists for them on this ccxt, so there is nothing to certify — these
#:       scenarios are untestable rather than untested.
#:   bitget
#:       the flag is ACCEPTED and changes nothing: no URL swap, no header, only
#:       ``options['sandboxMode']``. An order would reach PRODUCTION while the executor
#:       logs "SANDBOX mode". Finding EXCH-01, and the reason bitget is excluded by name
#:       here rather than left to a generic check.
SANDBOX_ROUTABLE = frozenset({"binance", "bybit", "okx"})

#: Credential env var names. The ``SANDBOX`` token is mandatory and is the point: an
#: operator cannot satisfy this harness with the production keys they already hold.
CREDENTIAL_TEMPLATE = (
    "AERORA_SANDBOX_{VENUE}_API_KEY",
    "AERORA_SANDBOX_{VENUE}_SECRET",
    "AERORA_SANDBOX_{VENUE}_PASSPHRASE",  # optional overall; OKX requires it
)

#: Names that mean somebody exported a PRODUCTION credential into this process. The suite
#: refuses to run rather than risk using one.
PRODUCTION_CREDENTIAL_NAMES = (
    "EXCHANGE_API_KEY", "EXCHANGE_SECRET", "EXCHANGE_API_SECRET",
    "BINANCE_API_KEY", "BINANCE_SECRET", "BINANCE_API_SECRET",
    "BYBIT_API_KEY", "BYBIT_SECRET",
    "OKX_API_KEY", "OKX_SECRET", "OKX_PASSPHRASE",
    "COINBASE_API_KEY", "COINBASE_SECRET",
    "KRAKEN_API_KEY", "KRAKEN_SECRET",
    "KUCOIN_API_KEY", "KUCOIN_SECRET",
    "BITGET_API_KEY", "BITGET_SECRET",
)


def api_url(exchange) -> str:
    """A comparable rendering of a ccxt client's REST base URL (may be a nested dict)."""
    return json.dumps((exchange.urls or {}).get("api"), sort_keys=True, default=str)


def executor_sandbox_config() -> dict:
    """The config dict ``CCXTExchangeExecutor.connect`` builds when ``sandbox=True``.

    Verbatim from ``exchange_executor.py:856-868``::

        config = {"apiKey": ..., "secret": ..., "enableRateLimit": True}
        if self.sandbox:
            config["sandbox"] = True
            config["options"] = {"sandbox": True}
        exchange_class = getattr(ccxt, self.exchange_id)
        self._exchange = exchange_class(config)

    Reproduced rather than imported because the subject of measurement is what those exact
    keys do to a real ccxt constructor; importing ``connect()`` would require a credential
    and a ``fetch_balance`` round trip.
    """
    return {
        "apiKey": "",
        "secret": "",
        "enableRateLimit": True,
        "sandbox": True,
        "options": {"sandbox": True},
    }


def sandbox_credentials(venue: str) -> dict | None:
    """The sandbox credential for *venue*, or ``None`` if not supplied.

    Returned for immediate hand-off to ccxt. Never logged, never written to a report and
    never placed in an assertion message — callers report only the NAMES, via
    :func:`credential_env_names`.
    """
    up = venue.upper()
    key = os.environ.get(f"AERORA_SANDBOX_{up}_API_KEY")
    secret = os.environ.get(f"AERORA_SANDBOX_{up}_SECRET")
    if not key or not secret:
        return None
    return {
        "api_key": key,
        "api_secret": secret,
        "password": os.environ.get(f"AERORA_SANDBOX_{up}_PASSPHRASE") or None,
    }


def credential_env_names(venue: str) -> list[str]:
    """The env var names this harness reads for *venue*. Names only, never values."""
    up = venue.upper()
    return [t.format(VENUE=up) for t in CREDENTIAL_TEMPLATE]


def require_sandbox_credentials(venue: str) -> dict:
    """Credentials for *venue*, or skip naming exactly what an operator must set."""
    creds = sandbox_credentials(venue)
    if creds is None:
        pytest.skip(
            f"NO SANDBOX CREDENTIAL for {venue}. This is MISSING EVIDENCE, not a pass. "
            f"To produce it, set: {credential_env_names(venue)} "
            f"(testnet/demo keys only — see tests/exchange_sandbox/README.md)."
        )
    return creds
