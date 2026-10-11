"""Exchange connectivity verification runner — PUBLIC endpoints only.

WHAT IT DOES
------------
For every eligible venue in the installed ccxt, attempts the three rungs that can be
established WITHOUT a credential, and records per-venue evidence for each:

    CLIENT_INITIALIZED            the class instantiates                  (offline)
    PUBLIC_CONNECTIVITY_VERIFIED  load_markets() -> the venue answered    (network)
    MARKET_DATA_VERIFIED          fetch_ticker / order book / OHLCV       (network)

Writes `staging/exchange_connectivity_results.json`, which
`backend_app/core/exchange_capability_registry.py` ingests. That artifact is the ONLY
route by which rungs 3+ enter the registry — nothing infers connectivity from a ccxt
capability declaration.

HARD SAFETY PROPERTIES
----------------------
1. **No credentials.** Every client is built with an empty config. `_assert_public_only`
   additionally fails the run if any production-shaped credential name is set in the
   environment, so a runner invoked on a developer machine cannot accidentally
   authenticate.
2. **No order may be submitted.** Every private/mutating ccxt method on each client is
   replaced with a function that raises. This is not a convention or a review rule — a
   call to `create_order` from inside this runner raises `_ForbiddenCall` and fails the
   venue. Enumerated in `_FORBIDDEN`.
3. **Bounded.** Per-call timeout, per-venue wall-clock budget, a global concurrency cap,
   and at most one retry — and that retry only for a classified-transient failure, so a
   rate-limited or geo-blocked venue is not hammered.
4. **Read-only.** `load_markets`, `fetch_ticker`, `fetch_order_book` and `fetch_ohlcv` are
   the only venue calls made. All four are public GETs.

WHY `load_markets` IS THE CONNECTIVITY PROBE
--------------------------------------------
It is the one call essentially every ccxt venue implements, it is the call this platform
already makes on connect (`exchange_executor.py:876`), and it returns the symbol universe
the market-data checks then need — so it proves connectivity and supplies the next step's
input in one round trip. A `fetch_status` probe would have been cheaper and is not widely
implemented; a constructor check would have been free and proves nothing, which is exactly
the conflation this runner exists to avoid.

USAGE
-----
    python scripts/verify_exchange_connectivity.py                  # all eligible venues
    python scripts/verify_exchange_connectivity.py --limit 10       # smoke test
    python scripts/verify_exchange_connectivity.py --only binance,kraken
    python scripts/verify_exchange_connectivity.py --concurrency 8 --timeout 25
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ccxt  # noqa: E402
import ccxt.async_support as ccxta  # noqa: E402

from backend_app.core.exchange_capability_registry import (  # noqa: E402
    ConnectivityState, EvidenceClass, FailureClass)

# ══════════════════════════════════════════════════════════════════════════════════════
#  SAFETY
# ══════════════════════════════════════════════════════════════════════════════════════

#: Every ccxt method that could place, cancel, modify or move money, plus the private
#: reads that would require a credential. Replaced with a raising stub on every client.
_FORBIDDEN = (
    "create_order", "createOrder", "create_limit_order", "create_market_order",
    "create_order_ws", "createOrderWs", "create_orders", "createOrders",
    "edit_order", "editOrder", "cancel_order", "cancelOrder",
    "cancel_all_orders", "cancelAllOrders", "cancel_orders", "cancelOrders",
    "create_stop_order", "createStopOrder", "create_stop_limit_order",
    "create_post_only_order", "create_reduce_only_order",
    "withdraw", "transfer", "set_leverage", "setLeverage",
    "set_margin_mode", "setMarginMode", "add_margin", "reduce_margin",
    "fetch_balance", "fetchBalance", "fetch_my_trades", "fetchMyTrades",
    "fetch_open_orders", "fetchOpenOrders", "fetch_closed_orders", "fetchClosedOrders",
    "fetch_order", "fetchOrder", "fetch_orders", "fetchOrders",
    "fetch_positions", "fetchPositions", "fetch_deposit_address",
)

_PRODUCTION_CREDENTIAL_NAMES = (
    "EXCHANGE_API_KEY", "EXCHANGE_SECRET", "EXCHANGE_API_SECRET",
    "BINANCE_API_KEY", "BINANCE_SECRET", "BYBIT_API_KEY", "BYBIT_SECRET",
    "OKX_API_KEY", "OKX_SECRET", "COINBASE_API_KEY", "COINBASE_SECRET",
    "KRAKEN_API_KEY", "KRAKEN_SECRET", "KUCOIN_API_KEY", "KUCOIN_SECRET",
    "BITGET_API_KEY", "BITGET_SECRET",
)


class _ForbiddenCall(RuntimeError):
    """A public-connectivity runner attempted a private or mutating venue call."""


def _assert_public_only() -> None:
    leaked = sorted(n for n in _PRODUCTION_CREDENTIAL_NAMES if os.environ.get(n))
    if leaked:
        raise SystemExit(
            f"REFUSING TO RUN: production-shaped exchange credential name(s) are set: "
            f"{leaked}. This runner performs PUBLIC checks only and must never be able "
            f"to authenticate. Unset them and re-run. (No value was read.)"
        )


# ══════════════════════════════════════════════════════════════════════════════════════
#  EGRESS PREFLIGHT
# ══════════════════════════════════════════════════════════════════════════════════════

#: Hostnames known to resolve correctly and independently, used as a control group.
_EGRESS_CONTROLS = ("api.binance.com", "api.kraken.com", "www.bitstamp.net")


def _registrable(host: str) -> str:
    """Approximate eTLD+1, so same-company hosts are not mistaken for strangers.

    `api.kraken.com` and `futures.kraken.com` legitimately share a Cloudflare address.
    An earlier version of this diagnostic flagged that as interception and declared its
    own control group compromised — a false positive that would have inflated the
    "filtered" count and understated real connectivity. Grouping by registrable domain
    removes that whole class of error.

    Handles the common two-label public suffixes (`.co.uk`, `.com.br`, `.co.jp`) rather
    than pulling in a full Public Suffix List dependency for a diagnostic.
    """
    parts = (host or "").lower().strip(".").split(".")
    if len(parts) < 3:
        return ".".join(parts)
    two_label_suffixes = {
        "co.uk", "com.br", "co.jp", "com.au", "co.kr", "com.tr", "com.sg",
        "com.hk", "co.za", "com.mx", "co.in", "com.ar", "co.nz",
    }
    if ".".join(parts[-2:]) in two_label_suffixes:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _tls_intercepted(host: str, timeout: float = 8.0) -> Optional[bool]:
    """Does *host* present a certificate valid for its own name?

    This is the DEFINITIVE interception signal and the reason it is worth a handshake:
    a middlebox answering for `whitebit.com` cannot produce a certificate that chains to
    a public root for that name, so verification fails. DNS collapse alone is
    circumstantial — CDNs share addresses legitimately — but a name that resolves and
    then fails hostname verification is being impersonated.

    Returns ``True`` intercepted, ``False`` clean, ``None`` undetermined (the handshake
    did not complete, so nothing is proven either way).

    No HTTP request is sent; this is TCP + TLS only.
    """
    import socket
    import ssl

    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                return False if tls.getpeercert() else None
    except ssl.SSLCertVerificationError:
        return True
    except ssl.SSLError:
        return True
    except Exception:  # noqa: BLE001 - timeout/refused/DNS: proves nothing about certs
        return None


def egress_profile(hostnames: List[str]) -> Dict[str, Any]:
    """Characterise this host's DNS before blaming any venue.

    WHY THIS EXISTS. A connectivity runner that cannot distinguish "the exchange is down"
    from "this machine is not allowed to reach the exchange" produces a number that looks
    like a product fact and is actually a network-policy fact. On the host this was first
    run from, `www.bitmex.com`, `cex.io` and `whitebit.com` all resolved to the SINGLE
    address 139.5.241.93 while `api.binance.com` and `api.kraken.com` resolved to their
    real, distinct CDN addresses — and one of the collapsed hosts then failed TLS
    certificate verification. That is a DNS sinkhole plus an intercepting middlebox, and
    reporting it as 24 dead exchanges would have been wrong.

    Two signals, both cheap and both offline-ish (DNS only, no HTTP):

      * **NXDOMAIN** — the name does not resolve at all.
      * **COLLAPSE** — two or more unrelated hostnames share one address. Independent
        exchanges do not share an IP; a sinkhole makes them appear to.

    Returns the per-host address map plus the set of addresses that look like sinkholes,
    so `classify_with_egress` can label an unreachable venue `EGRESS_FILTERED` instead of
    `VENUE_UNAVAILABLE`.
    """
    import socket
    from collections import defaultdict

    resolved: Dict[str, Optional[str]] = {}
    by_ip: Dict[str, List[str]] = defaultdict(list)

    for h in list(dict.fromkeys(list(hostnames) + list(_EGRESS_CONTROLS))):
        try:
            ip = socket.gethostbyname(h)
            resolved[h] = ip
            by_ip[ip].append(h)
        except Exception:  # noqa: BLE001
            resolved[h] = None

    # Collapse is only suspicious when UNRELATED registrable domains share an address.
    # Same-company hosts on one CDN address are normal and must not count.
    suspicious: Dict[str, List[str]] = {}
    for ip, hs in by_ip.items():
        if len({_registrable(h) for h in hs}) > 1:
            suspicious[ip] = sorted(hs)

    # Confirm (or refute) each suspicion with a TLS handshake. Circumstantial DNS
    # evidence is promoted to proven interception only when a certificate fails to
    # verify for its own hostname.
    intercepted: List[str] = []
    tls_results: Dict[str, Optional[bool]] = {}
    for hs in suspicious.values():
        for h in hs:
            if h in tls_results:
                continue
            tls_results[h] = _tls_intercepted(h)
            if tls_results[h] is True:
                intercepted.append(h)

    proven_sinkholes = sorted({
        resolved[h] for h in intercepted if resolved.get(h)
    })

    # The control group is only compromised if a control host is itself PROVEN
    # intercepted — not merely because it shares a CDN address with its own sibling.
    controls_ok = all(
        resolved.get(c) and c not in intercepted for c in _EGRESS_CONTROLS
    )

    # `filtered_hosts` is what reclassification keys on, and it is deliberately
    # conservative: a name that does not resolve, or one proven to be impersonated, or
    # one sharing a proven-sinkhole address. A host that merely timed out is NOT here,
    # because a timeout does not distinguish a blocked path from a slow venue.
    filtered = sorted(
        {h for h, ip in resolved.items() if ip is None}
        | set(intercepted)
        | {h for h, ip in resolved.items() if ip in proven_sinkholes}
    )

    return {
        "resolved": resolved,
        "nxdomain": sorted(h for h, ip in resolved.items() if ip is None),
        "suspicious_collapsed_groups": suspicious,
        "tls_probe": {k: v for k, v in tls_results.items()},
        "tls_interception_proven": sorted(intercepted),
        "sinkhole_addresses": proven_sinkholes,
        "controls_resolve_independently": controls_ok,
        "filtered_hosts": filtered,
    }


def _venue_hostnames(eid: str) -> List[str]:
    """Hostnames a venue's public REST base URL would use. Best-effort, offline."""
    cls = getattr(ccxt, eid, None)
    if cls is None:
        return []
    try:
        urls = (cls({}).urls or {})
    except Exception:  # noqa: BLE001
        return []
    out: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str):
            if v.startswith("http") and "{" not in v:
                try:
                    # Strip any :port — `socket.gethostbyname("api.ndax.io:8443")`
                    # raises, which an earlier version recorded as NXDOMAIN and so
                    # mislabelled a reachable venue as filtered.
                    out.add(v.split("/")[2].split(":")[0])
                except Exception:  # noqa: BLE001
                    pass
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(urls.get("api"))
    return sorted(out)


def _seal(client: Any) -> None:
    """Replace every private/mutating method with a raising stub."""
    for name in _FORBIDDEN:
        if hasattr(client, name):
            def _refuse(*_a, _n=name, **_kw):
                raise _ForbiddenCall(
                    f"{_n} is forbidden in the public connectivity runner"
                )
            try:
                setattr(client, name, _refuse)
            except Exception:  # noqa: BLE001 - some attrs are read-only; has[] still gates
                pass


# ══════════════════════════════════════════════════════════════════════════════════════
#  FAILURE CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════════════

def classify(exc: BaseException) -> Tuple[str, bool]:
    """(:class:`FailureClass`, retry_worth_it) for a venue exception.

    Order matters. ccxt's typed hierarchy is consulted FIRST — unlike
    `exchange_executor._handle_ccxt_error`, which substring-matches `str(error)` and so
    cannot tell a geo-block from a bad symbol. Substrings are only a fallback for
    untyped errors.
    """
    name = type(exc).__name__
    msg = str(exc).lower()

    if isinstance(exc, asyncio.TimeoutError):
        return FailureClass.TIMEOUT, True

    # ccxt typed exceptions, most specific first.
    for attr, cls_, retry in (
        ("AuthenticationError", FailureClass.AUTH_REQUIRED, False),
        ("PermissionDenied", FailureClass.GEO_BLOCKED, False),
        ("AccountSuspended", FailureClass.AUTH_FAILED, False),
        ("RateLimitExceeded", FailureClass.RATE_LIMITED, False),
        ("DDoSProtection", FailureClass.RATE_LIMITED, False),
        ("OnMaintenance", FailureClass.VENUE_UNAVAILABLE, False),
        ("ExchangeNotAvailable", FailureClass.VENUE_UNAVAILABLE, True),
        ("RequestTimeout", FailureClass.TIMEOUT, True),
        ("NetworkError", FailureClass.NETWORK, True),
        ("BadSymbol", FailureClass.BAD_SYMBOL, False),
        ("NotSupported", FailureClass.NOT_SUPPORTED, False),
        ("ExchangeError", FailureClass.VENUE_ERROR, False),
    ):
        typ = getattr(ccxt, attr, None)
        if typ is not None and isinstance(exc, typ):
            # A geo-block often arrives as PermissionDenied, but several venues send a
            # plain ExchangeError naming the restriction. Catch that wording here so a
            # jurisdiction block is not filed as a generic venue error.
            if cls_ == FailureClass.VENUE_ERROR and any(
                t in msg for t in ("restricted", "not available in", "unavailable in",
                                   "jurisdiction", "country", "region", "eligibility")
            ):
                return FailureClass.GEO_BLOCKED, False
            return cls_, retry

    # ccxt parsing faults raised inside the library while reading a venue response —
    # deterministic, so never retried. The okx `parse_market` TypeError is this class.
    if isinstance(exc, (TypeError, AttributeError, KeyError, IndexError, ValueError)):
        return FailureClass.LIBRARY_DEFECT, False

    if any(t in msg for t in ("timed out", "timeout")):
        return FailureClass.TIMEOUT, True
    if any(t in msg for t in ("getaddrinfo", "dns", "ssl", "tls", "certificate",
                              "connection", "connect", "unreachable", "reset")):
        return FailureClass.NETWORK, True
    if "429" in msg or "too many requests" in msg or "rate limit" in msg:
        return FailureClass.RATE_LIMITED, False
    if any(t in msg for t in ("403", "forbidden", "restricted", "jurisdiction")):
        return FailureClass.GEO_BLOCKED, False
    if any(t in msg for t in ("maintenance", "503", "502", "504")):
        return FailureClass.VENUE_UNAVAILABLE, True
    if "not supported" in msg or "not have" in msg:
        return FailureClass.NOT_SUPPORTED, False

    return f"{FailureClass.UNKNOWN}:{name}", False


# ══════════════════════════════════════════════════════════════════════════════════════
#  SYMBOL SELECTION
# ══════════════════════════════════════════════════════════════════════════════════════

#: Quote currencies tried in order of expected liquidity. Deterministic so a re-run
#: probes the same symbol and a regression is attributable.
_QUOTE_PREFERENCE = ("USDT", "USD", "USDC", "BUSD", "EUR", "BTC", "ETH", "JPY",
                     "GBP", "TRY", "BRL", "KRW", "INR", "ZAR", "PLN", "AUD")
_BASE_PREFERENCE = ("BTC", "ETH", "SOL", "XRP", "LTC", "ADA", "DOGE")


def pick_symbol(markets: Dict[str, Any]) -> Optional[str]:
    """A liquid, active, spot symbol — chosen deterministically.

    Venue-specific symbol formats are handled by reading the venue's OWN market map
    rather than by constructing a string, which is why this works across 100+ venues
    with wildly different conventions (``BTC/USDT``, ``XBT/EUR``, ``BTC-PERP``).
    """
    if not markets:
        return None

    def usable(m: Dict[str, Any]) -> bool:
        if m.get("active") is False:
            return False
        # Prefer spot; derivatives often need extra params on public endpoints.
        return bool(m.get("spot", True))

    candidates = {s: m for s, m in markets.items() if isinstance(m, dict) and usable(m)}
    if not candidates:
        candidates = {s: m for s, m in markets.items() if isinstance(m, dict)}
    if not candidates:
        return None

    for base in _BASE_PREFERENCE:
        for quote in _QUOTE_PREFERENCE:
            for sym, m in candidates.items():
                if m.get("base") == base and m.get("quote") == quote:
                    return sym
    for quote in _QUOTE_PREFERENCE:
        for sym in sorted(s for s, m in candidates.items() if m.get("quote") == quote):
            return sym
    return sorted(candidates)[0]


# ══════════════════════════════════════════════════════════════════════════════════════
#  PER-VENUE CHECK
# ══════════════════════════════════════════════════════════════════════════════════════

def _ev(state: ConnectivityState, attained: bool, *, ec: str, method: str,
        detail: str, fc: str = FailureClass.NONE, ms: float | None = None) -> dict:
    return {
        "state": state.name,
        "attained": attained,
        "evidence_class": ec,
        "method": method,
        "detail": detail[:400],
        "failure_class": fc,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": round(ms, 1) if ms is not None else None,
    }


async def _call(coro_fn, *args, timeout: float):
    return await asyncio.wait_for(coro_fn(*args), timeout=timeout)


def reclassify_for_egress(fc: str, eid: str, egress: Dict[str, Any]) -> str:
    """Relabel an unreachable venue as `EGRESS_FILTERED` when THIS HOST is the cause.

    Only applied to failure classes that mean "no usable answer arrived"
    (`NETWORK`, `TIMEOUT`, `VENUE_UNAVAILABLE`, `GEO_BLOCKED`). A venue that answered
    with a real error, or that ccxt failed to parse, is NOT reclassified — those are
    genuine findings regardless of the network path.
    """
    if fc not in (FailureClass.NETWORK, FailureClass.TIMEOUT,
                  FailureClass.VENUE_UNAVAILABLE, FailureClass.GEO_BLOCKED):
        return fc
    filtered = set(egress.get("filtered_hosts") or ())
    if not filtered:
        return fc
    hosts = _venue_hostnames(eid)
    if hosts and all(h in filtered for h in hosts):
        return FailureClass.EGRESS_FILTERED
    if any(h in filtered for h in hosts):
        return FailureClass.EGRESS_FILTERED
    return fc


async def verify_one(eid: str, *, timeout: float, budget: float) -> dict:
    """Run the three credential-free rungs for one venue."""
    out: dict = {
        "exchange_id": eid,
        "evidence": {},
        "measured_capabilities": {},
        "known_limitations": [],
        "probe_symbol": None,
        "market_count": None,
    }
    started = time.monotonic()

    cls = getattr(ccxta, eid, None)
    if cls is None:
        out["evidence"]["CLIENT_INITIALIZED"] = _ev(
            ConnectivityState.CLIENT_INITIALIZED, False,
            ec=EvidenceClass.MEASURED_LOCAL, method="getattr(ccxt.async_support, id)",
            detail="class absent from ccxt.async_support",
            fc=FailureClass.NOT_SUPPORTED)
        return out

    client = None
    try:
        t0 = time.monotonic()
        try:
            # Empty credentials, explicitly. `enableRateLimit` honours each venue's own
            # published rateLimit, which is the per-venue throttle this runner relies on.
            client = cls({"enableRateLimit": True, "timeout": int(timeout * 1000)})
            _seal(client)
        except Exception as exc:  # noqa: BLE001
            fc, _ = classify(exc)
            out["evidence"]["CLIENT_INITIALIZED"] = _ev(
                ConnectivityState.CLIENT_INITIALIZED, False,
                ec=EvidenceClass.MEASURED_LOCAL, method="cls({})",
                detail=f"{type(exc).__name__}: {exc}", fc=fc,
                ms=(time.monotonic() - t0) * 1000)
            return out

        out["evidence"]["CLIENT_INITIALIZED"] = _ev(
            ConnectivityState.CLIENT_INITIALIZED, True,
            ec=EvidenceClass.MEASURED_LOCAL, method="cls({}) with empty credentials",
            detail="constructor returned; no network call yet",
            ms=(time.monotonic() - t0) * 1000)

        # ── Rung 3: PUBLIC_CONNECTIVITY_VERIFIED ────────────────────────────────────
        markets: Dict[str, Any] = {}
        attempts = 0
        last_detail = ""
        last_fc = FailureClass.UNKNOWN
        t0 = time.monotonic()

        while attempts < 2:
            attempts += 1
            try:
                markets = await _call(client.load_markets, timeout=timeout) or {}
                break
            except BaseException as exc:  # noqa: BLE001
                fc, retry = classify(exc)
                last_fc, last_detail = fc, f"{type(exc).__name__}: {exc}"
                if not retry or (time.monotonic() - started) > budget * 0.6:
                    break
                await asyncio.sleep(1.5)

        elapsed = (time.monotonic() - t0) * 1000

        if not markets:
            out["evidence"]["PUBLIC_CONNECTIVITY_VERIFIED"] = _ev(
                ConnectivityState.PUBLIC_CONNECTIVITY_VERIFIED, False,
                ec=EvidenceClass.NONE,
                method=f"load_markets() x{attempts}",
                detail=last_detail or "returned no markets", fc=last_fc, ms=elapsed)
            out["measured_capabilities"]["public_markets"] = False
            return out

        out["market_count"] = len(markets)
        out["evidence"]["PUBLIC_CONNECTIVITY_VERIFIED"] = _ev(
            ConnectivityState.PUBLIC_CONNECTIVITY_VERIFIED, True,
            ec=EvidenceClass.LIVE_PUBLIC,
            method=f"load_markets() x{attempts}",
            detail=f"venue answered with {len(markets)} markets", ms=elapsed)
        out["measured_capabilities"]["public_markets"] = True

        # ── Rung 4: MARKET_DATA_VERIFIED ────────────────────────────────────────────
        symbol = pick_symbol(markets)
        out["probe_symbol"] = symbol
        if symbol is None:
            out["evidence"]["MARKET_DATA_VERIFIED"] = _ev(
                ConnectivityState.MARKET_DATA_VERIFIED, False,
                ec=EvidenceClass.NONE, method="pick_symbol()",
                detail="venue published markets but none were usable",
                fc=FailureClass.BAD_SYMBOL)
            return out

        checks: List[Tuple[str, str, Any]] = []
        h = client.has or {}

        async def run(label: str, cap: str, fn, *args) -> None:
            if h.get(cap) is not True and h.get(cap) != "emulated":
                out["measured_capabilities"][label] = False
                out["known_limitations"].append(f"{cap} not offered by this venue")
                checks.append((label, FailureClass.NOT_SUPPORTED, None))
                return
            try:
                res = await _call(fn, *args, timeout=timeout)
                ok = res is not None and (not hasattr(res, "__len__") or len(res) > 0)
                out["measured_capabilities"][label] = bool(ok)
                checks.append((label, FailureClass.NONE if ok else FailureClass.VENUE_ERROR,
                               res))
            except BaseException as exc:  # noqa: BLE001
                fc, _ = classify(exc)
                out["measured_capabilities"][label] = False
                checks.append((label, fc, None))
                out["known_limitations"].append(
                    f"{cap} failed: {fc} — {type(exc).__name__}")

        t0 = time.monotonic()
        await run("public_ticker", "fetchTicker", client.fetch_ticker, symbol)
        if (time.monotonic() - started) < budget:
            await run("public_order_book", "fetchOrderBook",
                      client.fetch_order_book, symbol)
        if (time.monotonic() - started) < budget:
            await run("public_ohlcv", "fetchOHLCV", client.fetch_ohlcv, symbol)
        md_ms = (time.monotonic() - t0) * 1000

        passed = [c for c in checks if c[1] == FailureClass.NONE]
        # The rung requires at least ONE real market-data response. A venue that offers
        # only an order book is market-data-verified; a venue where every offered method
        # errored is not, even though load_markets succeeded.
        attained = len(passed) > 0
        unsupported = [c[0] for c in checks if c[1] == FailureClass.NOT_SUPPORTED]
        failed = [(c[0], c[1]) for c in checks
                  if c[1] not in (FailureClass.NONE, FailureClass.NOT_SUPPORTED)]

        out["evidence"]["MARKET_DATA_VERIFIED"] = _ev(
            ConnectivityState.MARKET_DATA_VERIFIED, attained,
            ec=EvidenceClass.LIVE_PUBLIC if attained else EvidenceClass.NONE,
            method=f"fetch_ticker/fetch_order_book/fetch_ohlcv on {symbol}",
            detail=(f"passed={[c[0] for c in passed]} "
                    f"unsupported={unsupported} failed={failed}"),
            fc=FailureClass.NONE if attained else (failed[0][1] if failed
                                                   else FailureClass.NOT_SUPPORTED),
            ms=md_ms)
        return out

    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass


# ══════════════════════════════════════════════════════════════════════════════════════
#  RUNNER
# ══════════════════════════════════════════════════════════════════════════════════════

def eligible_ids() -> List[str]:
    """Eligible candidates, from the registry so the runner and registry cannot drift."""
    from backend_app.core.exchange_capability_registry import get_capability_registry

    reg = get_capability_registry()
    reg.load_inventory()
    return sorted(r.exchange_id for r in reg.eligible_candidates())


async def main_async(args: argparse.Namespace) -> int:
    _assert_public_only()

    ids = eligible_ids()
    if args.only:
        requested = [x.strip().lower() for x in args.only.split(",") if x.strip()]
        ids = [i for i in ids if i in requested]
    if args.limit:
        ids = ids[: args.limit]

    print("=" * 78)
    print("EXCHANGE CONNECTIVITY VERIFICATION — PUBLIC ENDPOINTS ONLY")
    print("no credentials · no orders · read-only")
    print("=" * 78)
    print(f"ccxt            : {ccxt.__version__}")
    print(f"venues to check : {len(ids)}")
    print(f"concurrency     : {args.concurrency}")
    print(f"per-call timeout: {args.timeout}s | per-venue budget: {args.budget}s")
    print("-" * 78)

    # ── Egress preflight, BEFORE any venue is blamed ────────────────────────────────
    all_hosts: List[str] = []
    for e in ids:
        all_hosts.extend(_venue_hostnames(e))
    egress = egress_profile(sorted(set(all_hosts)))

    print("EGRESS PREFLIGHT")
    print(f"  hostnames checked              : {len(egress['resolved'])}")
    print(f"  NXDOMAIN (do not resolve)      : {len(egress['nxdomain'])}")
    print(f"  suspicious collapsed groups    : "
          f"{len(egress['suspicious_collapsed_groups'])} "
          f"(unrelated domains sharing one IP)")
    print(f"  TLS interception PROVEN        : "
          f"{len(egress['tls_interception_proven'])}")
    print(f"  proven sinkhole addresses      : {egress['sinkhole_addresses']}")
    print(f"  control hosts clean            : "
          f"{egress['controls_resolve_independently']}")
    print(f"  hosts filtered from THIS host  : {len(egress['filtered_hosts'])}")
    if egress["tls_interception_proven"]:
        print("  PROVEN INTERCEPTION (cert does not verify for its own hostname):")
        for h in egress["tls_interception_proven"]:
            print(f"    {h}  ->  {egress['resolved'].get(h)}")
        print("  Failures for these hosts are labelled EGRESS_FILTERED. They are facts")
        print("  about THIS network vantage point, not about the exchange.")
    if egress["nxdomain"]:
        print(f"  NXDOMAIN hosts: {egress['nxdomain']}")
    print("-" * 78)

    # ── Merge into any existing artifact so partial runs accumulate ─────────────────
    out_path = ROOT / "staging" / "exchange_connectivity_results.json"
    results: Dict[str, dict] = {}
    if args.merge and out_path.exists():
        try:
            prior = json.loads(out_path.read_text(encoding="utf-8"))
            results = dict(prior.get("per_exchange") or {})
            print(f"merging into {len(results)} existing venue result(s)")
        except Exception as exc:  # noqa: BLE001
            print(f"could not read prior artifact ({exc}); starting fresh")

    sem = asyncio.Semaphore(args.concurrency)
    done = 0
    t_start = time.monotonic()

    async def worker(eid: str) -> None:
        nonlocal done
        async with sem:
            try:
                res = await asyncio.wait_for(
                    verify_one(eid, timeout=args.timeout, budget=args.budget),
                    timeout=args.budget + args.timeout,
                )
            except BaseException as exc:  # noqa: BLE001
                fc, _ = classify(exc)
                res = {
                    "exchange_id": eid, "measured_capabilities": {},
                    "known_limitations": [f"runner budget exceeded: {fc}"],
                    "evidence": {
                        "PUBLIC_CONNECTIVITY_VERIFIED": _ev(
                            ConnectivityState.PUBLIC_CONNECTIVITY_VERIFIED, False,
                            ec=EvidenceClass.NONE, method="verify_one",
                            detail=f"{type(exc).__name__}: {exc}", fc=fc)
                    },
                }
            # Relabel host-caused failures before they are recorded, so the artifact
            # never asserts a venue is unavailable when this machine simply cannot
            # reach it.
            for ev in (res.get("evidence") or {}).values():
                if not ev.get("attained"):
                    ev["failure_class"] = reclassify_for_egress(
                        ev.get("failure_class", FailureClass.UNKNOWN), eid, egress
                    )
            res["egress_hostnames"] = _venue_hostnames(eid)

            results[eid] = res
            done += 1

            conn = res["evidence"].get("PUBLIC_CONNECTIVITY_VERIFIED", {})
            md = res["evidence"].get("MARKET_DATA_VERIFIED", {})
            if conn.get("attained"):
                tag = "MARKET_DATA" if md.get("attained") else "CONNECTED  "
                extra = f"{res.get('market_count')} mkts, {res.get('probe_symbol')}"
            else:
                tag = "FAILED     "
                extra = conn.get("failure_class", "?")
            print(f"[{done:>3}/{len(ids)}] {eid:<22} {tag}  {extra}")

    await asyncio.gather(*(worker(e) for e in ids))
    wall = time.monotonic() - t_start

    connected = [e for e, r in results.items()
                 if r["evidence"].get("PUBLIC_CONNECTIVITY_VERIFIED", {}).get("attained")]
    market_data = [e for e, r in results.items()
                   if r["evidence"].get("MARKET_DATA_VERIFIED", {}).get("attained")]
    failed = [e for e in results if e not in connected]

    by_failure: Dict[str, List[str]] = {}
    for e in failed:
        fc = (results[e]["evidence"]
              .get("PUBLIC_CONNECTIVITY_VERIFIED", {})
              .get("failure_class", "UNKNOWN"))
        by_failure.setdefault(str(fc), []).append(e)

    target_low, target_high = 100, 110
    summary = {
        "ccxt_version": ccxt.__version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "wall_clock_seconds": round(wall, 1),
        "eligible_candidates": len(eligible_ids()),
        "attempted": len(ids),
        "public_connectivity_verified": len(connected),
        "market_data_verified": len(market_data),
        "failed": len(failed),
        "failures_by_class": {k: sorted(v) for k, v in sorted(by_failure.items())},
        "target_low": target_low,
        "target_high": target_high,
        "remaining_to_target_low": max(0, target_low - len(connected)),
        "target_met": len(connected) >= target_low,
        # The shortfall split that decides whether this is a product problem or a
        # network problem. `egress_filtered` venues are NOT evidence about the venue.
        "egress_filtered": len(by_failure.get(FailureClass.EGRESS_FILTERED, [])),
        "genuine_failures": len([
            e for e in failed
            if (results[e]["evidence"].get("PUBLIC_CONNECTIVITY_VERIFIED", {})
                .get("failure_class")) != FailureClass.EGRESS_FILTERED
        ]),
        "egress_profile": {
            k: v for k, v in egress.items() if k != "resolved"
        },
        # Stated explicitly so no reader can mistake connectivity for trading capability.
        "authenticated_access_verified": 0,
        "sandbox_verified": 0,
        "trading_certified": 0,
        "live_trading_authorized": 0,
    }

    print("-" * 78)
    print("SUMMARY")
    print("-" * 78)
    print(f"eligible candidates           : {summary['eligible_candidates']}")
    print(f"attempted                     : {summary['attempted']}")
    print(f"PUBLIC_CONNECTIVITY_VERIFIED  : {summary['public_connectivity_verified']}")
    print(f"MARKET_DATA_VERIFIED          : {summary['market_data_verified']}")
    print(f"failed                        : {summary['failed']}")
    print(f"wall clock                    : {summary['wall_clock_seconds']}s")
    print()
    print(f"TARGET {target_low}-{target_high} (metric: PUBLIC_CONNECTIVITY_VERIFIED)")
    print(f"  current   : {summary['public_connectivity_verified']}")
    print(f"  remaining : {summary['remaining_to_target_low']}")
    print(f"  met       : {summary['target_met']}")
    print()
    if by_failure:
        print("FAILURES BY CLASS")
        for fc, es in sorted(by_failure.items()):
            print(f"  {fc:<28} {len(es):>3}  {sorted(es)}")
        print()
        print("SHORTFALL ATTRIBUTION")
        print(f"  EGRESS_FILTERED (this host's network, NOT the venue) : "
              f"{summary['egress_filtered']}")
        print(f"  genuine venue/library failures                       : "
              f"{summary['genuine_failures']}")
        reachable_ceiling = len(connected) + summary["egress_filtered"]
        print(f"  verifiable ceiling from an UNFILTERED network        : "
              f"<= {reachable_ceiling} of {summary['attempted']}")
    print()
    print("NOT ESTABLISHED BY THIS RUNNER (require their own evidence):")
    print("  AUTHENTICATED_ACCESS_VERIFIED : 0  (needs authorized credentials)")
    print("  SANDBOX_VERIFIED              : 0  (needs sandbox credentials + routing proof)")
    print("  TRADING_CERTIFIED             : 0  (needs the order-lifecycle suite)")
    print("  LIVE_TRADING_AUTHORIZED       : 0  (release gate only)")

    out_path = ROOT / "staging" / "exchange_connectivity_results.json"
    out_path.write_text(
        json.dumps({"summary": summary, "per_exchange": results}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nartifact -> {out_path.relative_to(ROOT)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument("--timeout", type=float, default=20.0, help="per-call seconds")
    p.add_argument("--budget", type=float, default=70.0, help="per-venue wall seconds")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--only", type=str, default="")
    p.add_argument(
        "--no-merge", dest="merge", action="store_false", default=True,
        help="overwrite the artifact instead of merging into it (default: merge, so a "
             "targeted re-run of failures does not discard the venues it did not attempt)",
    )
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
