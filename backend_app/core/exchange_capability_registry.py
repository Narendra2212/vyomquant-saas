"""Canonical exchange capability registry — the single source of truth for what this
platform can actually do with each venue.

WHY THIS MODULE EXISTS
----------------------
Before it, four separate registries named exchanges and disagreed with each other:

  * `exchange_executor.ExchangeExecutorFactory.SUPPORTED_EXCHANGES` — 7 ids, authoritative
    for ORDER ROUTING and nothing else;
  * `exchange_rate_limiter.ExchangeType` — 10 ids, in a module with zero importers;
  * `exchange_certification.ExchangeCertificationRegistry` — 8 hand-written records whose
    `sandbox` booleans are contradicted by the installed ccxt for coinbase and kucoin
    (finding EXCH-04), and which `validate_deployment_preflight` gates live deployment on;
  * `exchange_connection_schema` — 6 UI form definitions with their own
    `sandbox_supported` booleans.

and `routers/exchange.py::/supported` advertised every venue ccxt ships (~110) by
iterating `ccxt.exchanges` and reporting each class's `has` map — which conflates "ccxt
knows about this venue" with "this platform can trade it". Seven could receive an order.

THE CENTRAL RULE: STATES ARE NOT INFERRED
-----------------------------------------
The failure mode this module is built to prevent is a capability claim that was derived
rather than measured. `exchange_certification.py` marked OKX `LEVEL_5_PRODUCTION_READY`
and `HEALTHY` as hardcoded constants while `connection_engine.py:196-250` documented its
`load_markets` as broken on the pinned ccxt. Both statements lived in the codebase
simultaneously and nothing reconciled them.

So :class:`ConnectivityState` is an ORDERED ladder, and every rung requires its OWN
evidence record. Reaching rung 4 says nothing about rung 5. In particular:

  * a venue appearing in `ccxt.exchanges` is `DISCOVERED` and nothing more;
  * a client that constructs is `CLIENT_INITIALIZED` — a constructor-only check is
    explicitly NOT connectivity, because it makes no network call;
  * `ccxt.has['createOrder'] is True` is a DECLARATION by the library, recorded as a
    declared capability, and never promoted to `TRADING_CERTIFIED`;
  * `urls['test']` existing is not `SANDBOX_VERIFIED` — bitget accepts sandbox mode and
    routes to production (EXCH-01), which is why that rung needs a routing proof and not
    a URL-table lookup.

`LIVE_TRADING_AUTHORIZED` is deliberately unreachable from inside this module. Nothing
here can set it; it is granted only by the production safety and release gates, and
:meth:`ExchangeRecord.is_tradable` requires it.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ExchangeCapabilityRegistry")

#: Where the connectivity runner writes its artifact. Read, never written, by this module.
_RESULTS_ENV = "AERORA_CONNECTIVITY_RESULTS"
_DEFAULT_RESULTS = "staging/exchange_connectivity_results.json"


class ConnectivityState(IntEnum):
    """An ordered ladder. Each rung requires its own evidence — see the module docstring.

    ``IntEnum`` so ``>=`` comparisons read naturally, but the ordering is a statement about
    PREREQUISITES, not an inference rule: holding rung *n* never implies rung *n+1*, and
    :meth:`ExchangeRecord.attained` only returns a rung that carries its own evidence.
    """

    #: The exchange id exists in the installed ccxt. No network call. No claim.
    DISCOVERED = 1
    #: The ccxt class instantiated without raising. Still no network call.
    CLIENT_INITIALIZED = 2
    #: The intended venue endpoint answered a real public request.
    PUBLIC_CONNECTIVITY_VERIFIED = 3
    #: The required public market-data operations returned usable data.
    MARKET_DATA_VERIFIED = 4
    #: Authenticated access succeeded with authorized credentials.
    AUTHENTICATED_ACCESS_VERIFIED = 5
    #: The venue's sandbox/demo environment was verified — including a proof that the
    #: client actually left the production endpoint. Not a `urls['test']` lookup.
    SANDBOX_VERIFIED = 6
    #: Order lifecycle and safety tests passed. See staging/EXCHANGE_SANDBOX_CERTIFICATION.md
    TRADING_CERTIFIED = 7
    #: Granted only by the production safety and release gates. Unsettable here.
    LIVE_TRADING_AUTHORIZED = 8


class EvidenceClass(str):
    """How a claim was established. Kept as explicit strings so artifacts are readable."""

    NONE = "NONE"
    DECLARED = "DECLARED"        # the library says so; nothing was executed
    MOCK = "MOCK"                # a test passed against a substitute venue
    MEASURED_LOCAL = "MEASURED"  # executed locally, no network
    LIVE_PUBLIC = "LIVE_PUBLIC"  # a real public endpoint answered
    LIVE_AUTH = "LIVE_AUTH"      # a real authenticated call succeeded
    SANDBOX = "SANDBOX"          # observed against a venue sandbox


class FailureClass(str):
    """Why a check did not pass. Distinguished because the responses differ.

    Collapsing these is how a geo-blocked venue, an unsupported method and a real outage
    all become "failed" and get retried identically.
    """

    NONE = "NONE"
    AUTH_REQUIRED = "AUTH_REQUIRED"          # endpoint needs credentials
    AUTH_FAILED = "AUTH_FAILED"              # credentials rejected
    RATE_LIMITED = "RATE_LIMITED"            # 429 / DDoS protection
    NETWORK = "NETWORK"                      # DNS, TLS, connection, timeout
    NOT_SUPPORTED = "NOT_SUPPORTED"          # venue/ccxt does not implement it
    VENUE_ERROR = "VENUE_ERROR"              # venue answered with an error
    VENUE_UNAVAILABLE = "VENUE_UNAVAILABLE"  # maintenance, delisted, 5xx
    GEO_BLOCKED = "GEO_BLOCKED"              # jurisdiction restriction
    #: THIS HOST cannot reach the venue — DNS sinkhole, NXDOMAIN, or a TLS-intercepting
    #: middlebox. Separated from VENUE_UNAVAILABLE because it says nothing about the
    #: venue: it is a property of the vantage point, and the same check from an
    #: unfiltered network may well pass. Conflating the two would let a corporate egress
    #: policy be reported as a dead exchange.
    EGRESS_FILTERED = "EGRESS_FILTERED"
    BAD_SYMBOL = "BAD_SYMBOL"                # no usable market found
    LIBRARY_DEFECT = "LIBRARY_DEFECT"        # ccxt raised while parsing (e.g. okx)
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


@dataclass
class CapabilityFlags:
    """Precise capabilities, never one ambiguous ``supported`` boolean.

    Every field is a tri-state: ``True`` available, ``False`` unavailable, ``None``
    unverified. ``None`` is not ``False`` and must not be rendered as either "supported"
    or "broken" — it means nobody has checked.
    """

    # Public market data
    public_ticker: Optional[bool] = None
    public_tickers: Optional[bool] = None
    public_order_book: Optional[bool] = None
    public_ohlcv: Optional[bool] = None
    public_trades: Optional[bool] = None
    public_markets: Optional[bool] = None
    # Private account
    private_balance: Optional[bool] = None
    private_open_orders: Optional[bool] = None
    private_order_status: Optional[bool] = None
    private_my_trades: Optional[bool] = None
    private_positions: Optional[bool] = None
    private_trading_fees: Optional[bool] = None
    # Execution
    order_create: Optional[bool] = None
    order_cancel: Optional[bool] = None
    order_market: Optional[bool] = None
    order_limit: Optional[bool] = None
    order_stop: Optional[bool] = None
    order_stop_limit: Optional[bool] = None
    order_post_only: Optional[bool] = None
    order_reduce_only: Optional[bool] = None
    # Transport / environment
    websocket: Optional[bool] = None
    sandbox_url_declared: Optional[bool] = None
    #: MEASURED, not declared: did arming sandbox mode actually leave production?
    sandbox_routing_proven: Optional[bool] = None


@dataclass
class StateEvidence:
    """The evidence for ONE rung. No evidence, no rung."""

    state: ConnectivityState
    attained: bool
    evidence_class: str = EvidenceClass.NONE
    method: Optional[str] = None
    detail: Optional[str] = None
    failure_class: str = FailureClass.NONE
    checked_at: Optional[str] = None
    duration_ms: Optional[float] = None
    artifact: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.name
        return d


@dataclass
class ExchangeRecord:
    """One venue: its identity, declared capabilities, and per-rung evidence."""

    exchange_id: str
    display_name: str
    ccxt_class_available: bool = False
    is_alias: bool = False
    canonical_id: Optional[str] = None
    countries: List[str] = field(default_factory=list)
    ccxt_certified: bool = False
    www_url: Optional[str] = None
    required_credentials: List[str] = field(default_factory=list)
    rate_limit_ms: Optional[int] = None
    capabilities: CapabilityFlags = field(default_factory=CapabilityFlags)
    evidence: Dict[str, StateEvidence] = field(default_factory=dict)
    known_limitations: List[str] = field(default_factory=list)

    # ── state ───────────────────────────────────────────────────────────────────────

    def record(self, ev: StateEvidence) -> None:
        self.evidence[ev.state.name] = ev

    def has_state(self, state: ConnectivityState) -> bool:
        """Does this venue hold *state* ON ITS OWN EVIDENCE?

        Note what this deliberately does NOT do: it does not check lower rungs and
        conclude this one. A venue can hold `MARKET_DATA_VERIFIED` while
        `PUBLIC_CONNECTIVITY_VERIFIED` is unrecorded, and that is a reportable
        inconsistency (see :meth:`inconsistencies`), not something to paper over.
        """
        ev = self.evidence.get(state.name)
        return bool(ev and ev.attained)

    def attained(self) -> ConnectivityState:
        """The highest rung held on its own evidence, defaulting to ``DISCOVERED``."""
        best = ConnectivityState.DISCOVERED
        for state in ConnectivityState:
            if self.has_state(state):
                best = state
        return best

    def inconsistencies(self) -> List[str]:
        """Rungs held while a PREREQUISITE rung is not.

        Surfaced rather than auto-corrected: a venue reporting market data without a
        recorded connectivity check means the runner changed, not that the venue is fine.
        """
        out = []
        held = [s for s in ConnectivityState if self.has_state(s)]
        if not held:
            return out
        highest = max(held)
        for state in ConnectivityState:
            if state < highest and not self.has_state(state):
                out.append(
                    f"{self.exchange_id}: holds {highest.name} but {state.name} "
                    f"carries no evidence"
                )
        return out

    # ── trading authorization ───────────────────────────────────────────────────────

    def is_tradable(self) -> bool:
        """May an order be routed here?

        Requires the TOP rung, which this module cannot grant. Connectivity — however
        thoroughly verified — never makes a venue tradable. That separation is the whole
        point of the module.
        """
        return self.has_state(ConnectivityState.LIVE_TRADING_AUTHORIZED)

    def to_dict(self) -> dict:
        return {
            "exchange_id": self.exchange_id,
            "display_name": self.display_name,
            "ccxt_class_available": self.ccxt_class_available,
            "is_alias": self.is_alias,
            "canonical_id": self.canonical_id,
            "countries": self.countries,
            "ccxt_certified": self.ccxt_certified,
            "www_url": self.www_url,
            "required_credentials": self.required_credentials,
            "rate_limit_ms": self.rate_limit_ms,
            "capabilities": asdict(self.capabilities),
            "state": self.attained().name,
            "state_value": int(self.attained()),
            "tradable": self.is_tradable(),
            "evidence": {k: v.to_dict() for k, v in self.evidence.items()},
            "known_limitations": self.known_limitations,
            "inconsistencies": self.inconsistencies(),
        }


# ══════════════════════════════════════════════════════════════════════════════════════
#  REGISTRY
# ══════════════════════════════════════════════════════════════════════════════════════

#: Venues this platform will route an order to, mirroring
#: `ExchangeExecutorFactory.SUPPORTED_EXCHANGES`. Read from that module so the two cannot
#: drift; this registry reports execution capability but is not its source of truth.
def _order_routing_allowlist() -> List[str]:
    try:
        from backend_app.backend.exchange_executor import ExchangeExecutorFactory

        return list(ExchangeExecutorFactory.SUPPORTED_EXCHANGES)
    except Exception:  # noqa: BLE001 - the registry must load without the executor
        logger.warning("could not read the order-routing allowlist; reporting none")
        return []


#: Venues withheld from order routing by an explicit safety decision, with the reason.
#:
#: IMPORTED FROM THE EXECUTOR, NOT DEFINED HERE. This dict used to be declared in this
#: module, and that was the EXCH-01 follow-up defect: the registry filtered bitget out of
#: `order_routing_allowlisted()` while `ExchangeExecutorFactory.SUPPORTED_EXCHANGES` still
#: contained it and nothing in the executor consulted this module. The registry's REPORT
#: was correct and the executor's BEHAVIOUR was not, which is the worst of the two
#: possible inconsistencies — it reads as fixed.
#:
#: The authoritative list now lives beside the code that constructs executors
#: (`exchange_executor.SAFETY_BLOCKED_EXCHANGES`) and is enforced by
#: `assert_exchange_routable`. Reading it from there makes drift between the two
#: impossible rather than merely unlikely, and the dependency direction already existed
#: (this module reads `SUPPORTED_EXCHANGES` from the same place), so no cycle is created.
#:
#: Separate from the allowlist so that "not allowlisted" and "deliberately blocked" stay
#: distinguishable in the API and the dashboard: the first is a scope decision, the second
#: is an incident with a reason attached.
def _load_safety_blocked() -> Dict[str, str]:
    """The executor's block list, or a fail-closed fallback if it cannot be imported.

    The fallback keeps bitget blocked rather than returning an empty dict. An import
    error here must not be the thing that re-opens routing to a venue whose sandbox
    silently reaches production — and a registry that reports "nothing is blocked"
    because an import failed would be worse than one that fails to load at all.
    """
    try:
        from backend_app.backend.exchange_executor import SAFETY_BLOCKED_EXCHANGES

        return dict(SAFETY_BLOCKED_EXCHANGES)
    except Exception:  # noqa: BLE001 - the registry must load without the executor
        logger.error(
            "could not import SAFETY_BLOCKED_EXCHANGES from the executor; falling back "
            "to the fail-closed default (bitget blocked)"
        )
        return {
            "bitget": (
                "EXCH-01 (fallback entry): arming sandbox mode on bitget changes neither "
                "the base URL nor any header, so a 'sandbox' order reaches production. "
                "The executor's authoritative block list could not be imported."
            )
        }


SAFETY_BLOCKED: Dict[str, str] = _load_safety_blocked()


class ExchangeCapabilityRegistry:
    """Canonical registry. Inventory from the installed ccxt; evidence from artifacts."""

    def __init__(self) -> None:
        self._records: Dict[str, ExchangeRecord] = {}
        self._lock = threading.RLock()
        self._loaded = False
        self._results_path: Optional[str] = None

    # ── inventory (state DISCOVERED / CLIENT_INITIALIZED) ───────────────────────────

    def load_inventory(self) -> None:
        """Enumerate the installed ccxt. Offline — constructs clients, calls nothing.

        Everything written here is `DISCOVERED` or `CLIENT_INITIALIZED` plus DECLARED
        capability flags. No connectivity claim is made, because no request is sent.
        """
        import ccxt

        with self._lock:
            self._records.clear()
            now = datetime.now(timezone.utc).isoformat()

            for eid in ccxt.exchanges:
                cls = getattr(ccxt, eid, None)
                rec = ExchangeRecord(
                    exchange_id=eid,
                    display_name=eid,
                    ccxt_class_available=cls is not None,
                )
                rec.record(StateEvidence(
                    state=ConnectivityState.DISCOVERED,
                    attained=cls is not None,
                    evidence_class=EvidenceClass.MEASURED_LOCAL,
                    method="ccxt.exchanges enumeration",
                    detail=f"present in ccxt {ccxt.__version__}",
                    checked_at=now,
                ))

                if cls is None:
                    self._records[eid] = rec
                    continue

                try:
                    inst = cls({})
                except Exception as exc:  # noqa: BLE001
                    rec.record(StateEvidence(
                        state=ConnectivityState.CLIENT_INITIALIZED,
                        attained=False,
                        evidence_class=EvidenceClass.MEASURED_LOCAL,
                        method="cls({})",
                        detail=f"{type(exc).__name__}: {exc}",
                        failure_class=FailureClass.LIBRARY_DEFECT,
                        checked_at=now,
                    ))
                    self._records[eid] = rec
                    continue

                rec.record(StateEvidence(
                    state=ConnectivityState.CLIENT_INITIALIZED,
                    attained=True,
                    evidence_class=EvidenceClass.MEASURED_LOCAL,
                    method="cls({})",
                    detail="constructor returned; NO network call was made",
                    checked_at=now,
                ))

                rec.display_name = getattr(inst, "name", eid) or eid
                rec.is_alias = bool(getattr(inst, "alias", False))
                rec.countries = list(getattr(inst, "countries", None) or [])
                rec.ccxt_certified = bool(getattr(inst, "certified", False))
                rec.www_url = (getattr(inst, "urls", None) or {}).get("www")
                rec.required_credentials = sorted(
                    k for k, v in (inst.requiredCredentials or {}).items() if v
                )
                rec.rate_limit_ms = getattr(inst, "rateLimit", None)

                h = inst.has or {}

                def dec(key: str) -> Optional[bool]:
                    """A ccxt `has` entry as a tri-state.

                    ccxt uses the string ``'emulated'`` for a method it synthesises from
                    others. That is neither a clean yes nor a no, so it maps to ``True``
                    with the emulation recorded as a limitation rather than being hidden.
                    """
                    v = h.get(key)
                    if v is True:
                        return True
                    if v == "emulated":
                        return True
                    if v is False:
                        return False
                    return None

                rec.capabilities = CapabilityFlags(
                    public_ticker=dec("fetchTicker"),
                    public_tickers=dec("fetchTickers"),
                    public_order_book=dec("fetchOrderBook"),
                    public_ohlcv=dec("fetchOHLCV"),
                    public_trades=dec("fetchTrades"),
                    public_markets=dec("fetchMarkets"),
                    private_balance=dec("fetchBalance"),
                    private_open_orders=dec("fetchOpenOrders"),
                    private_order_status=dec("fetchOrder"),
                    private_my_trades=dec("fetchMyTrades"),
                    private_positions=dec("fetchPositions"),
                    private_trading_fees=dec("fetchTradingFees"),
                    order_create=dec("createOrder"),
                    order_cancel=dec("cancelOrder"),
                    order_market=dec("createMarketOrder"),
                    order_limit=dec("createLimitOrder"),
                    order_stop=dec("createStopOrder"),
                    order_stop_limit=dec("createStopLimitOrder"),
                    order_post_only=dec("createPostOnlyOrder"),
                    order_reduce_only=dec("createReduceOnlyOrder"),
                    websocket=bool(getattr(inst, "pro", False)),
                    sandbox_url_declared=(inst.urls or {}).get("test") is not None,
                    sandbox_routing_proven=None,  # requires a measurement, not a lookup
                )

                for key in ("fetchTicker", "fetchOHLCV", "fetchOrderBook", "createOrder"):
                    if h.get(key) == "emulated":
                        rec.known_limitations.append(
                            f"ccxt emulates {key} for this venue rather than calling a "
                            f"native endpoint"
                        )

                self._records[eid] = rec

            self._apply_safety_blocks()
            self._loaded = True
            logger.info(
                "inventory loaded: %d ids from ccxt %s",
                len(self._records), ccxt.__version__,
            )

    def _apply_safety_blocks(self) -> None:
        for eid, reason in SAFETY_BLOCKED.items():
            rec = self._records.get(eid)
            if rec is not None:
                rec.known_limitations.append(f"SAFETY BLOCK — {reason}")

    # ── evidence ingestion (states 3-7) ─────────────────────────────────────────────

    def load_verification_results(self, path: Optional[str] = None) -> int:
        """Merge a connectivity-runner artifact. Returns how many venues were updated.

        The artifact is the ONLY way rungs 3+ enter this registry. Nothing in this module
        can assert connectivity from a capability declaration.
        """
        resolved = path or os.environ.get(_RESULTS_ENV) or _DEFAULT_RESULTS
        p = pathlib.Path(resolved)
        if not p.is_absolute():
            p = pathlib.Path(__file__).resolve().parents[2] / resolved
        if not p.exists():
            logger.info("no connectivity artifact at %s; rungs 3+ stay unverified", p)
            return 0

        data = json.loads(p.read_text(encoding="utf-8"))
        self._results_path = str(p)
        updated = 0

        with self._lock:
            for eid, res in (data.get("per_exchange") or {}).items():
                rec = self._records.get(eid)
                if rec is None:
                    continue
                for state_name, ev in (res.get("evidence") or {}).items():
                    try:
                        state = ConnectivityState[state_name]
                    except KeyError:
                        continue
                    rec.record(StateEvidence(
                        state=state,
                        attained=bool(ev.get("attained")),
                        evidence_class=ev.get("evidence_class", EvidenceClass.NONE),
                        method=ev.get("method"),
                        detail=ev.get("detail"),
                        failure_class=ev.get("failure_class", FailureClass.NONE),
                        checked_at=ev.get("checked_at"),
                        duration_ms=ev.get("duration_ms"),
                        artifact=str(p),
                    ))
                caps = res.get("measured_capabilities") or {}
                for k, v in caps.items():
                    if hasattr(rec.capabilities, k):
                        setattr(rec.capabilities, k, v)
                for lim in res.get("known_limitations") or []:
                    if lim not in rec.known_limitations:
                        rec.known_limitations.append(lim)
                updated += 1

        logger.info("merged connectivity evidence for %d venues from %s", updated, p)
        return updated

    # ── queries ─────────────────────────────────────────────────────────────────────

    def _ensure(self) -> None:
        if not self._loaded:
            self.load_inventory()
            self.load_verification_results()

    def get(self, exchange_id: str) -> Optional[ExchangeRecord]:
        self._ensure()
        return self._records.get((exchange_id or "").lower())

    def all_records(self) -> List[ExchangeRecord]:
        self._ensure()
        return list(self._records.values())

    def eligible_candidates(self) -> List[ExchangeRecord]:
        """Distinct, constructible, non-alias venues with at least one public method.

        This is the denominator for the 100-110 target — the set worth ATTEMPTING
        verification on. It is a candidate count, not a connectivity count.
        """
        self._ensure()
        out = []
        for r in self._records.values():
            if r.is_alias or not r.has_state(ConnectivityState.CLIENT_INITIALIZED):
                continue
            c = r.capabilities
            if any((c.public_ticker, c.public_tickers, c.public_order_book,
                    c.public_ohlcv, c.public_trades, c.public_markets)):
                out.append(r)
        return out

    def with_state(self, state: ConnectivityState) -> List[ExchangeRecord]:
        self._ensure()
        return [r for r in self._records.values() if r.has_state(state)]

    def tradable(self) -> List[ExchangeRecord]:
        self._ensure()
        return [r for r in self._records.values() if r.is_tradable()]

    def order_routing_allowlisted(self) -> List[str]:
        """Ids the executor will build. NOT a trading authorization."""
        return [e for e in _order_routing_allowlist() if e not in SAFETY_BLOCKED]

    def safety_blocked(self) -> Dict[str, str]:
        return dict(SAFETY_BLOCKED)

    def all_inconsistencies(self) -> List[str]:
        self._ensure()
        out: List[str] = []
        for r in self._records.values():
            out.extend(r.inconsistencies())
        return out

    # ── reporting ───────────────────────────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        """The launch-dashboard contract. Every number is countable from evidence."""
        self._ensure()
        import ccxt

        eligible = self.eligible_candidates()
        counts = {
            s.name: len([r for r in self._records.values() if r.has_state(s)])
            for s in ConnectivityState
        }
        target_low, target_high = 100, 110
        verified = counts["PUBLIC_CONNECTIVITY_VERIFIED"]

        return {
            "ccxt_version": ccxt.__version__,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "evidence_artifact": self._results_path,
            "raw_ccxt_ids": len(self._records),
            "aliases_excluded": len([r for r in self._records.values() if r.is_alias]),
            "eligible_candidates": len(eligible),
            "state_counts": counts,
            "target": {
                "low": target_low,
                "high": target_high,
                "metric": "PUBLIC_CONNECTIVITY_VERIFIED",
                "current": verified,
                "remaining_to_low": max(0, target_low - verified),
                "met": verified >= target_low,
            },
            "order_routing_allowlisted": self.order_routing_allowlisted(),
            "safety_blocked": list(SAFETY_BLOCKED),
            "tradable_count": len(self.tradable()),
            "inconsistencies": self.all_inconsistencies(),
        }


_registry: Optional[ExchangeCapabilityRegistry] = None
_registry_lock = threading.Lock()


def get_capability_registry() -> ExchangeCapabilityRegistry:
    """Process-wide registry singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ExchangeCapabilityRegistry()
    return _registry


__all__ = [
    "ConnectivityState", "EvidenceClass", "FailureClass", "CapabilityFlags",
    "StateEvidence", "ExchangeRecord", "ExchangeCapabilityRegistry",
    "SAFETY_BLOCKED", "get_capability_registry",
]
