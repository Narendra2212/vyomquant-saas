# -*- coding: utf-8 -*-
"""backend/asset_universe.py — the cached, exchange-agnostic tradeable market universe.

Spec: strategy-builder task 7.1. ``design.md`` §
*DATA blocks and exchange-agnostic asset discovery*. Requirements 11.1, 11.2, 11.3,
11.4, 11.5, 11.6 and 25.5.

WHAT THIS MODULE IS FOR
-----------------------
``GET /api/market/symbols`` calls ``ccxt.binance().load_markets()`` synchronously inside
the request, keeps ``/USDT`` pairs only, truncates to the first fifty alphabetically and
falls back to a ten-symbol constant when anything goes wrong. That is a hardcoded
universe wearing a live-data costume, and it blocks the event loop while it puts it on.
This module is the replacement: one cached universe, refreshed **off** the request path,
served with the exchange's own precision and limit figures attached.

THE THREE RULES THIS MODULE IS BUILT AROUND
-------------------------------------------
1. **No ``load_markets()`` inside a request.** ``discover_assets`` reads a cache and
   nothing else. A miss *schedules* a refresh and answers immediately; it never awaits
   an exchange round-trip on behalf of a caller. The only code here that talks to an
   exchange is :func:`refresh_universe`, and its callers are the startup warm and the
   scheduled loop.
2. **No hardcoded symbol list. Anywhere. Ever.** When the cache holds nothing, the
   answer is :class:`AssetUniverseUnavailable` → ``503 ASSET_UNIVERSE_UNAVAILABLE``
   (Requirement 11.6). There is no constant in this file that names a market, and the
   set of *exchanges* is read from ``ExchangeExecutorFactory.SUPPORTED_EXCHANGES`` —
   the venues the platform's own executor can place an order on — rather than written
   out here a second time.
3. **The exchange's own figures, carried through unrounded.** ``price_precision``,
   ``amount_precision``, ``min_notional`` and ``min_amount`` are whatever CCXT reported,
   including ``None`` when the venue stated nothing. Later phases validate order sizes
   against these, so a defaulted ``8`` or a rounded tick size would be a fabricated
   trading constraint. Absent is absent.

DELIBERATE DECISIONS, RECORDED RATHER THAN LEFT IMPLICIT
--------------------------------------------------------
* **DEV_MODE's mock market map is rejected, not cached.**
  ``ConnectionEngine.connect()`` has a pre-existing DEV_MODE branch that, when the
  exchange is unreachable, injects ``_apply_mock_interface`` — a two-entry market map
  holding ``BTC/USDT`` and ``ETH/USDT``. Caching that and serving it as the tradeable
  universe would substitute a hardcoded list by another route, which is exactly what
  Requirement 11.6 forbids. :func:`_markets_are_mocked` detects the injected interface
  and the refresh treats that exchange as **failed**. A developer with no network gets
  ``503 ASSET_UNIVERSE_UNAVAILABLE``, which is true, instead of a two-symbol universe,
  which is not.
* **Ordering is by listing breadth, not by "liquidity".**
  ``design.md`` sorts by ``liquidity_rank DESC, symbol ASC``. Nothing in this platform
  caches per-market volume, and fetching a ticker for every market to invent one is a
  separate decision belonging with the latency work. So the sort is
  ``(listing_count DESC, symbol ASC)`` where ``listing_count`` is how many of the
  supported venues list the market — a real, checkable number that is **not** named
  ``liquidity_rank`` and is not presented as one.
* **Precision is attributed to one named venue.**
  One record per ``(symbol, market_type)`` may exist on several venues with different
  tick sizes and minimums. Taking a min, a max or an average across venues would
  describe a market that trades nowhere. So the record carries the figures of exactly
  one exchange and names it in ``precision_source``; ``available_on`` lists every venue
  that has the market. The chosen venue is deterministic: the first in the platform's
  supported order that lists the market.
* **A stale-but-real universe is served, and labelled stale.**
  Requirement 11.6's error condition is "cache empty **and** refresh fails". A universe
  older than its TTL is neither, so it is served with ``source_meta.stale = true`` and
  its ``age_seconds``, and a refresh is scheduled. Withholding real markets that are six
  hours old would be a worse answer than serving them with their age stated.

Redis is the cache of record (``redis_manager.get_redis_manager()``, DB 0, 6 h TTL per
Requirement 25.5). A process-local last-known-good copy sits behind it so that a Redis
outage degrades to "serving a local copy" rather than to a 503, and so that an
environment with no Redis at all still works once a refresh has succeeded. Nothing here
raises at import time and nothing here raises on a Redis failure.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("AssetUniverse")


# ══════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════

#: Requirement 25.5 states the cache time-to-live: six hours, in seconds.
UNIVERSE_TTL_SECONDS: int = 6 * 60 * 60

#: The scheduled refresh runs at half the TTL so that a healthy refresher never lets a
#: served entry expire. Requirement 11.5 asks for the refresh to be off the request path
#: and on a schedule; this is that schedule. Overridable for operations, never below a
#: minute (a tighter loop would hammer the venues' public endpoints).
REFRESH_INTERVAL_SECONDS: float = max(
    60.0, float(os.getenv("ASSET_UNIVERSE_REFRESH_SECONDS", str(UNIVERSE_TTL_SECONDS // 2)))
)

#: Redis key. Versioned, so a shape change cannot be read as the old shape.
CACHE_KEY: str = "strategy_builder:asset_universe:v1"

#: The serialised payload's own version, checked on read.
UNIVERSE_SCHEMA_VERSION: int = 1

#: The market types the DATA descriptor offers (``design.md`` → ``ohlcv_feed``:
#: ``options := ["spot", "swap", "future"]``). A market outside this set — an option, an
#: index, a margin pair — is excluded and **counted**, so the exclusion is visible in
#: ``source_meta`` rather than silent.
SUPPORTED_MARKET_TYPES: Tuple[str, ...] = ("spot", "swap", "future")

#: Cursor envelope version.
_CURSOR_VERSION: int = 1

#: How long a caller should wait before retrying an unavailable universe. Reported in the
#: 503's ``retry_after_seconds`` and in the ``Retry-After`` header.
RETRY_AFTER_SECONDS: int = 30


# ══════════════════════════════════════════════════════════════════════════
#  ERRORS
# ══════════════════════════════════════════════════════════════════════════


class AssetUniverseUnavailable(RuntimeError):
    """The cache holds no universe and no refresh has produced one.

    Requirement 11.6. The router maps this to ``503 ASSET_UNIVERSE_UNAVAILABLE``. It
    carries the last refresh failure so an operator reads the cause rather than
    reproducing it, and it exists precisely so that no caller is tempted to answer 200
    with a substitute list.
    """

    code = "ASSET_UNIVERSE_UNAVAILABLE"

    def __init__(self, message: str, *, last_error: Optional[str] = None,
                 refresh_in_flight: bool = False) -> None:
        super().__init__(message)
        self.last_error = last_error
        self.refresh_in_flight = refresh_in_flight


class InvalidAssetCursor(ValueError):
    """The continuation cursor is unreadable, or belongs to a different query.

    The router maps this to ``422 ASSET_CURSOR_INVALID``. Continuing a cursor under
    different filters would silently page through a different result set, so it is
    refused rather than reinterpreted.
    """

    code = "ASSET_CURSOR_INVALID"


# ══════════════════════════════════════════════════════════════════════════
#  ASSET REF
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AssetRef:
    """One tradeable market, exchange-agnostic. ``design.md`` → ``STRUCTURE AssetRef``.

    ``price_precision``, ``amount_precision``, ``min_notional`` and ``min_amount`` are
    the venue's own figures, unrounded and undefaulted — ``None`` means the venue stated
    nothing, which is information a later order-size check needs to have rather than a
    zero to divide by.

    ``available_on`` is informational (deploy-time compatibility: "your Kraken account
    does not list SOL/USDT") and is excluded from ``dag_hash`` by construction — it never
    enters a graph.
    """

    symbol: str
    base: str
    quote: str
    market_type: str
    active: Optional[bool]
    price_precision: Optional[float]
    amount_precision: Optional[float]
    min_notional: Optional[float]
    min_amount: Optional[float]
    available_on: Tuple[str, ...] = ()
    #: Which venue in ``available_on`` the precision and limit figures above came from.
    #: A record never blends figures from two venues.
    precision_source: Optional[str] = None

    @property
    def listing_count(self) -> int:
        """How many supported venues list this market. Not a liquidity figure."""
        return len(self.available_on)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["available_on"] = list(self.available_on)
        payload["listing_count"] = self.listing_count
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AssetRef":
        return cls(
            symbol=payload["symbol"],
            base=payload.get("base") or "",
            quote=payload.get("quote") or "",
            market_type=payload.get("market_type") or "",
            active=payload.get("active"),
            price_precision=payload.get("price_precision"),
            amount_precision=payload.get("amount_precision"),
            min_notional=payload.get("min_notional"),
            min_amount=payload.get("min_amount"),
            available_on=tuple(payload.get("available_on") or ()),
            precision_source=payload.get("precision_source"),
        )

    def sort_key(self) -> Tuple[int, str]:
        """The total order pages are cut on: listing breadth first, then symbol.

        Total (no ties beyond identical keys for identical records) and deterministic, so
        a keyset cursor built from it positions correctly in any later universe.
        """
        return (-self.listing_count, self.symbol)


@dataclass
class AssetUniverse:
    """A whole cached universe plus the provenance a caller is entitled to see."""

    assets: List[AssetRef]
    generated_at: float
    exchanges: List[str] = field(default_factory=list)
    exchanges_failed: List[str] = field(default_factory=list)
    universe_hash: str = ""
    #: ``"redis"`` or ``"process"`` — where this copy was actually read from.
    cache_backend: str = "process"

    def __post_init__(self) -> None:
        if not self.universe_hash:
            self.universe_hash = _universe_hash(self.assets)

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.generated_at)

    @property
    def is_stale(self) -> bool:
        return self.age_seconds > UNIVERSE_TTL_SECONDS

    @property
    def is_empty(self) -> bool:
        return not self.assets

    def to_payload(self) -> Dict[str, Any]:
        return {
            "schema_version": UNIVERSE_SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "generated_at_iso": datetime.fromtimestamp(
                self.generated_at, tz=timezone.utc
            ).isoformat(),
            "exchanges": list(self.exchanges),
            "exchanges_failed": list(self.exchanges_failed),
            "universe_hash": self.universe_hash,
            "assets": [asset.to_dict() for asset in self.assets],
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any], *, cache_backend: str) -> "AssetUniverse":
        if not isinstance(payload, dict):
            raise ValueError("Cached asset universe is not an object")
        if int(payload.get("schema_version") or 0) != UNIVERSE_SCHEMA_VERSION:
            raise ValueError(
                f"Cached asset universe schema {payload.get('schema_version')!r} is not "
                f"{UNIVERSE_SCHEMA_VERSION}"
            )
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            raise ValueError("Cached asset universe carries no asset list")
        return cls(
            assets=[AssetRef.from_dict(entry) for entry in raw_assets],
            generated_at=float(payload.get("generated_at") or 0.0),
            exchanges=list(payload.get("exchanges") or []),
            exchanges_failed=list(payload.get("exchanges_failed") or []),
            universe_hash=str(payload.get("universe_hash") or ""),
            cache_backend=cache_backend,
        )


def _universe_hash(assets: Sequence[AssetRef]) -> str:
    """A content hash over the universe, used to detect a mid-pagination change."""
    digest = hashlib.sha256()
    for asset in assets:
        digest.update(
            f"{asset.symbol}|{asset.market_type}|{asset.active}|"
            f"{asset.price_precision}|{asset.amount_precision}|"
            f"{asset.min_notional}|{asset.min_amount}|"
            f"{','.join(asset.available_on)}\n".encode("utf-8")
        )
    return f"u_{digest.hexdigest()[:16]}"


# ══════════════════════════════════════════════════════════════════════════
#  WHICH EXCHANGES
# ══════════════════════════════════════════════════════════════════════════


def supported_exchange_ids() -> List[str]:
    """The venues whose markets make up the universe.

    Read from ``ExchangeExecutorFactory.SUPPORTED_EXCHANGES`` — the platform's own
    statement of where it can place an order. A market the executor could never trade has
    no business in a strategy author's asset selector, and duplicating the list here would
    create a second answer to one question.

    ``ASSET_UNIVERSE_EXCHANGES`` narrows or overrides it for an operator (comma-separated
    ids). It is an exchange list, not a symbol list; no override can conjure a market.
    """
    override = (os.getenv("ASSET_UNIVERSE_EXCHANGES") or "").strip()
    if override:
        return [part.strip().lower() for part in override.split(",") if part.strip()]

    from backend_app.backend.exchange_executor import ExchangeExecutorFactory

    return [str(name).lower() for name in ExchangeExecutorFactory.SUPPORTED_EXCHANGES]


# ══════════════════════════════════════════════════════════════════════════
#  READING A MARKET MAP FROM connection_engine
# ══════════════════════════════════════════════════════════════════════════


#: The name ``connection_engine._apply_mock_interface`` binds its market loader under. Named
#: here so the two callers of :func:`markets_are_mocked` and the test that asserts the real
#: installer against it all read one literal.
MOCK_MARKET_LOADER_NAME = "mock_load_markets"


def markets_are_mocked(exchange: Any) -> bool:
    """True when ``ConnectionEngine`` injected its DEV_MODE mock interface.

    ``connection_engine._apply_mock_interface`` binds a closure named
    ``mock_load_markets`` onto the instance and sets a two-entry ``markets`` dict. That
    map is a constant, and caching a constant as the tradeable universe is the dishonesty
    Requirement 11.6 exists to prevent — so this is detected and the exchange is treated
    as unreachable.

    Detection is by the closure's ``__name__``, which is asserted against the real
    ``_apply_mock_interface`` in the tests: if that function is ever renamed, the test
    fails rather than this guard silently going quiet.

    **Not by reading ``DEV_MODE``**, deliberately, and this is the reason the check is a
    property of the resolved object rather than of the configuration: ``connect()`` installs
    the mock interface only on the failure branch, so a ``DEV_MODE`` deployment that reached
    its exchange carries a real interface, and a deployment whose flag was turned off after
    an interface was installed carries a mock one. The flag and the installed interface can
    therefore disagree, and it is the interface that decides what the prices are.

    Public since marketplace-subscriptions-paper-trading task 24.1, which needs the same
    detection to refuse a Paper_Session against a mock feed (Requirement 14.8). The private
    spelling :func:`_markets_are_mocked` is retained below as an alias so existing callers and
    ``tests/test_asset_discovery.py`` are unaffected; there is one implementation.
    """
    loader = getattr(exchange, "load_markets", None)
    return getattr(loader, "__name__", "") == MOCK_MARKET_LOADER_NAME


#: The pre-task-24.1 spelling. The same function object, not a wrapper, so no call site can
#: reach a second implementation.
_markets_are_mocked = markets_are_mocked


def _coerce_number(value: Any) -> Optional[float]:
    """A CCXT numeric field as a float, or ``None`` — never a default.

    A venue that states no minimum notional gets ``None``. Substituting ``0`` would read
    as "no minimum" and substituting ``10`` would invent one; both are wrong in a
    direction that reaches an order router.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return number


def _asset_from_market(market: Dict[str, Any], exchange_id: str) -> Optional[AssetRef]:
    """One CCXT market as an :class:`AssetRef`, or ``None`` when it is not usable.

    ``None`` for: a market with no canonical symbol, no base or no quote, or whose type
    is outside the DATA descriptor's ``spot | swap | future``. Nothing is guessed — a
    market that does not state what it is does not enter the universe.
    """
    if not isinstance(market, dict):
        return None

    symbol = market.get("symbol")
    base = market.get("base")
    quote = market.get("quote")
    if not symbol or not base or not quote:
        return None

    market_type = market.get("type")
    if not market_type:
        # Some adapters express the type only through flags.
        for flag in SUPPORTED_MARKET_TYPES:
            if market.get(flag) is True:
                market_type = flag
                break
    market_type = str(market_type).lower() if market_type else ""
    if market_type not in SUPPORTED_MARKET_TYPES:
        return None

    precision = market.get("precision") or {}
    limits = market.get("limits") or {}
    amount_limits = limits.get("amount") or {}
    cost_limits = limits.get("cost") or {}

    active = market.get("active")
    if active is not None:
        active = bool(active)

    return AssetRef(
        symbol=str(symbol),
        base=str(base),
        quote=str(quote),
        market_type=market_type,
        active=active,
        price_precision=_coerce_number(precision.get("price")),
        amount_precision=_coerce_number(precision.get("amount")),
        min_notional=_coerce_number(cost_limits.get("min")),
        min_amount=_coerce_number(amount_limits.get("min")),
        available_on=(exchange_id,),
        precision_source=exchange_id,
    )


async def load_exchange_markets(exchange_id: str) -> Dict[str, Any]:
    """The market map for one venue, via the existing ``connection_engine``.

    ``ConnectionEngine.connect()`` owns the CCXT construction and the retrying
    ``load_markets`` (fix CE-1); this function adds no second connection path. It is
    called **only** from :func:`refresh_universe`, which is called only from the startup
    warm and the scheduled loop — never from a request handler.

    Raises ``ConnectionError`` when the venue is unreachable, and also when the DEV_MODE
    mock interface was injected, because a mock market map is not a market map.
    """
    from backend_app.backend.connection_engine import ConnectionEngine

    engine = ConnectionEngine(exchange_id=exchange_id)
    try:
        exchange = await engine.connect()
        if exchange is None:
            raise ConnectionError(f"{exchange_id}: connect() produced no exchange")
        if _markets_are_mocked(exchange):
            raise ConnectionError(
                f"{exchange_id}: DEV_MODE injected a mock market map; refusing to cache "
                f"it as the tradeable universe"
            )
        markets = getattr(exchange, "markets", None) or {}
        # Copy out before the session is closed — the instance is about to go away.
        return dict(markets)
    finally:
        try:
            await engine.disconnect()
        except Exception as exc:  # pragma: no cover - shutdown noise only
            logger.debug("[%s] disconnect after market load: %s", exchange_id, exc)


def merge_markets(
    per_exchange: Sequence[Tuple[str, Dict[str, Any]]]
) -> Tuple[List[AssetRef], Dict[str, int]]:
    """The union of several venues' market maps, keyed on ``(symbol, market_type)``.

    Precision and limits come from the **first** venue in the given order that lists the
    market, and that venue is named in ``precision_source``; later venues only extend
    ``available_on``. No figure is averaged, min-ed or max-ed across venues, because the
    result would describe a market that trades nowhere.

    Returns the assets in the served order plus a count of what was excluded and why.
    """
    merged: Dict[Tuple[str, str], AssetRef] = {}
    stats = {"considered": 0, "excluded_unusable": 0, "duplicate_listings": 0}

    for exchange_id, markets in per_exchange:
        for market in (markets or {}).values():
            stats["considered"] += 1
            asset = _asset_from_market(market, exchange_id)
            if asset is None:
                stats["excluded_unusable"] += 1
                continue
            key = (asset.symbol, asset.market_type)
            existing = merged.get(key)
            if existing is None:
                merged[key] = asset
                continue
            stats["duplicate_listings"] += 1
            if exchange_id not in existing.available_on:
                merged[key] = AssetRef(
                    symbol=existing.symbol,
                    base=existing.base,
                    quote=existing.quote,
                    market_type=existing.market_type,
                    # A market listed as active anywhere is offerable; the venue-specific
                    # answer stays reachable through `available_on`.
                    active=(
                        True
                        if (existing.active or asset.active)
                        else (existing.active if asset.active is None else asset.active)
                    ),
                    price_precision=existing.price_precision,
                    amount_precision=existing.amount_precision,
                    min_notional=existing.min_notional,
                    min_amount=existing.min_amount,
                    available_on=existing.available_on + (exchange_id,),
                    precision_source=existing.precision_source,
                )

    assets = sorted(merged.values(), key=lambda a: a.sort_key())
    return assets, stats


# ══════════════════════════════════════════════════════════════════════════
#  THE CACHE
# ══════════════════════════════════════════════════════════════════════════

#: Process-local last-known-good. Behind Redis, not instead of it: a Redis outage then
#: degrades to "serving a local copy, and saying so" rather than to a 503.
_local_universe: Optional[AssetUniverse] = None

#: Single-flight guard for the refresh, plus the last failure for the 503 body.
_refresh_task: Optional[asyncio.Task] = None
#: Whatever went wrong most recently, reported in the 503 so an operator reads the cause
#: rather than reproducing it. Set on a partial success too (a venue that failed while
#: others succeeded), which is why the backoff below keys off `_last_failure_at` instead.
_last_refresh_error: Optional[str] = None
#: When a refresh last produced *no* universe. Only a total failure backs off.
_last_failure_at: float = 0.0

#: A failed refresh is not retried more often than this, so a cache miss under a storm of
#: requests cannot turn into a storm of ``load_markets`` calls against the venues.
REFRESH_BACKOFF_SECONDS: float = float(os.getenv("ASSET_UNIVERSE_REFRESH_BACKOFF", "30"))


async def _redis_cache():
    """The platform's Redis manager, or ``None``. Never raises."""
    try:
        from backend_app.backend.redis_manager import get_redis_manager

        return await get_redis_manager()
    except Exception as exc:
        logger.warning("[AssetUniverse] Redis manager unavailable: %s", exc)
        return None


async def read_cached_universe() -> Optional[AssetUniverse]:
    """The cached universe: Redis first, then the process-local copy. Never raises."""
    manager = await _redis_cache()
    if manager is not None:
        try:
            payload = await manager.cache_get_json(CACHE_KEY)
        except Exception as exc:
            logger.warning("[AssetUniverse] Redis read failed: %s", exc)
            payload = None
        if payload:
            try:
                universe = AssetUniverse.from_payload(payload, cache_backend="redis")
                if not universe.is_empty:
                    return universe
                logger.warning("[AssetUniverse] Cached universe is empty; ignoring it")
            except Exception as exc:
                # A shape we cannot read is treated as no cache at all, loudly. It is
                # never partially interpreted.
                logger.error("[AssetUniverse] Cached universe is unreadable: %s", exc)

    if _local_universe is not None and not _local_universe.is_empty:
        return _local_universe
    return None


async def write_cached_universe(universe: AssetUniverse) -> bool:
    """Store a refreshed universe. Returns whether Redis accepted it.

    The process-local copy is set either way, so a Redis-less environment still serves a
    real universe after a successful refresh.
    """
    global _local_universe
    _local_universe = universe

    manager = await _redis_cache()
    if manager is None:
        logger.warning(
            "[AssetUniverse] No Redis; %d markets held in process only",
            len(universe.assets),
        )
        return False
    try:
        stored = await manager.cache_set_json(
            CACHE_KEY, universe.to_payload(), ttl=UNIVERSE_TTL_SECONDS
        )
    except Exception as exc:
        logger.warning("[AssetUniverse] Redis write failed: %s", exc)
        return False
    if not stored:
        logger.warning("[AssetUniverse] Redis declined the universe write")
    return bool(stored)


# ══════════════════════════════════════════════════════════════════════════
#  REFRESH — OFF THE REQUEST PATH
# ══════════════════════════════════════════════════════════════════════════


async def refresh_universe() -> AssetUniverse:
    """Rebuild the universe from the supported venues and cache it.

    Requirement 11.5's "outside the request path". Callers: the startup warm, the
    scheduled loop and :func:`schedule_refresh`'s background task. **Not** a request
    handler — this is the only code here that awaits an exchange.

    A venue that fails is recorded in ``exchanges_failed`` and the remaining venues still
    produce a universe: one unreachable exchange must not blank an asset selector. When
    **every** venue fails, this raises, and the cache keeps whatever it already had.
    """
    global _last_refresh_error, _last_failure_at

    exchange_ids = supported_exchange_ids()
    if not exchange_ids:
        _last_refresh_error = "No exchanges are configured for asset discovery"
        _last_failure_at = time.time()
        raise AssetUniverseUnavailable(_last_refresh_error)

    per_exchange: List[Tuple[str, Dict[str, Any]]] = []
    failed: List[str] = []
    errors: List[str] = []

    results = await asyncio.gather(
        *(load_exchange_markets(exchange_id) for exchange_id in exchange_ids),
        return_exceptions=True,
    )
    for exchange_id, result in zip(exchange_ids, results):
        if isinstance(result, BaseException):
            failed.append(exchange_id)
            errors.append(f"{exchange_id}: {result}")
            logger.warning("[AssetUniverse] %s market load failed: %s", exchange_id, result)
            continue
        if not result:
            failed.append(exchange_id)
            errors.append(f"{exchange_id}: no markets returned")
            continue
        per_exchange.append((exchange_id, result))

    assets, stats = merge_markets(per_exchange)
    if not assets:
        _last_refresh_error = "; ".join(errors) or "No venue returned a usable market"
        _last_failure_at = time.time()
        logger.error("[AssetUniverse] Refresh produced no markets: %s", _last_refresh_error)
        raise AssetUniverseUnavailable(
            "The tradeable market universe could not be refreshed from any exchange",
            last_error=_last_refresh_error,
        )

    universe = AssetUniverse(
        assets=assets,
        generated_at=time.time(),
        exchanges=[exchange_id for exchange_id, _ in per_exchange],
        exchanges_failed=failed,
    )
    await write_cached_universe(universe)
    _last_refresh_error = "; ".join(errors) or None
    _last_failure_at = 0.0
    logger.info(
        "[AssetUniverse] Refreshed: %d markets from %s (failed: %s, considered %d, "
        "excluded %d)",
        len(assets),
        ", ".join(universe.exchanges) or "none",
        ", ".join(failed) or "none",
        stats["considered"],
        stats["excluded_unusable"],
    )
    return universe


def _refresh_is_in_flight() -> bool:
    return _refresh_task is not None and not _refresh_task.done()


def schedule_refresh() -> bool:
    """Start a refresh in the background and return immediately.

    This is what a cache miss does instead of awaiting ``load_markets``: the design's
    ``refresh_async()``, which "returns last-known-good immediately". Single-flight — a
    hundred concurrent misses schedule one refresh — and backed off after a failure so a
    miss storm cannot become a request storm against the venues.

    Returns whether a task was started. Never raises: with no running loop (a synchronous
    test, a management command) there is simply nothing to schedule.
    """
    global _refresh_task

    if _refresh_is_in_flight():
        return False
    if _last_failure_at and time.time() - _last_failure_at < REFRESH_BACKOFF_SECONDS:
        return False

    async def _run() -> None:
        try:
            await refresh_universe()
        except Exception as exc:
            logger.warning("[AssetUniverse] Background refresh failed: %s", exc)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _refresh_task = loop.create_task(_run())
    return True


# ══════════════════════════════════════════════════════════════════════════
#  CURSOR — KEYSET, NOT OFFSET
# ══════════════════════════════════════════════════════════════════════════


def _filter_fingerprint(query: "AssetQuery") -> str:
    raw = json.dumps(
        {
            "search": query.search,
            "base": query.base,
            "quote": query.quote,
            "market_type": query.market_type,
            "active_only": query.active_only,
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def encode_cursor(asset: AssetRef, query: "AssetQuery", universe_hash: str) -> str:
    """An opaque continuation cursor pinned to the last item's **sort key**.

    Not an offset. Under a refresh between pages, a keyset cursor still positions
    correctly: a market inserted before the key is skipped rather than shifting every
    later row into a duplicate, and one inserted after the key appears on a later page.
    The cursor's own item does not have to survive the refresh, because the comparison is
    against the ordering and not against a row identity.

    ``u`` records the universe the cursor was cut against, so the next page can *tell the
    client* the universe moved (``source_meta.universe_changed``) rather than pretending
    it did not. ``f`` pins the filters: continuing a cursor under different filters would
    page through a different result set, so it is refused.
    """
    listing_rank, symbol = asset.sort_key()
    payload = {
        "v": _CURSOR_VERSION,
        "k": [listing_rank, symbol],
        "f": _filter_fingerprint(query),
        "u": universe_hash,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, query: "AssetQuery") -> Dict[str, Any]:
    """Read a cursor, or refuse it. Never silently reinterprets one."""
    if not cursor or not isinstance(cursor, str):
        raise InvalidAssetCursor("The cursor is empty")
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidAssetCursor(f"The cursor is not readable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise InvalidAssetCursor("The cursor was issued for a different response shape")
    key = payload.get("k")
    if (
        not isinstance(key, list)
        or len(key) != 2
        or not isinstance(key[0], int)
        or not isinstance(key[1], str)
    ):
        raise InvalidAssetCursor("The cursor carries no usable position")
    if payload.get("f") != _filter_fingerprint(query):
        raise InvalidAssetCursor(
            "The cursor was issued for a different filter set. Start a new query rather "
            "than continuing this one."
        )
    return payload


# ══════════════════════════════════════════════════════════════════════════
#  QUERY AND DISCOVERY
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AssetQuery:
    """``design.md`` → ``INPUT: query { search, base, quote, market_type, active_only,
    limit, cursor }``."""

    search: Optional[str] = None
    base: Optional[str] = None
    quote: Optional[str] = None
    market_type: Optional[str] = None
    active_only: bool = True
    limit: int = 50
    cursor: Optional[str] = None


def _matches_search(asset: AssetRef, needle: str) -> bool:
    """Substring match over symbol, base and quote, case-insensitive.

    Deliberately a substring match rather than an edit-distance "fuzzy" one: a selector
    that offered ``ETH/USDT`` for the query ``BTC`` because the strings are close would be
    worse than one that offered nothing, and this is a control the author uses to pick
    what gets traded.
    """
    return (
        needle in asset.symbol.lower()
        or needle in asset.base.lower()
        or needle in asset.quote.lower()
    )


def filter_assets(assets: Iterable[AssetRef], query: AssetQuery) -> List[AssetRef]:
    """Apply the query's filters. Each narrows monotonically (the design's invariant)."""
    results = list(assets)
    if query.active_only:
        results = [a for a in results if a.active is True]
    if query.market_type:
        wanted_type = query.market_type.strip().lower()
        results = [a for a in results if a.market_type == wanted_type]
    if query.base:
        wanted_base = query.base.strip().upper()
        results = [a for a in results if a.base.upper() == wanted_base]
    if query.quote:
        wanted_quote = query.quote.strip().upper()
        results = [a for a in results if a.quote.upper() == wanted_quote]
    if query.search:
        needle = query.search.strip().lower()
        if needle:
            results = [a for a in results if _matches_search(a, needle)]
    return results


def paginate(
    assets: Sequence[AssetRef], query: AssetQuery, universe_hash: str
) -> Tuple[List[AssetRef], Optional[str]]:
    """One page plus the cursor for the next, cut on the sort key.

    ``assets`` is already in served order. The page is the first ``limit`` records whose
    sort key is strictly greater than the cursor's — a keyset scan, so no page ever
    depends on a row count that a refresh could change underneath it.
    """
    start = 0
    if query.cursor:
        payload = decode_cursor(query.cursor, query)
        rank, symbol = payload["k"][0], payload["k"][1]
        after = (int(rank), str(symbol))
        start = len(assets)
        for index, asset in enumerate(assets):
            if asset.sort_key() > after:
                start = index
                break

    page = list(assets[start : start + max(1, query.limit)])
    next_cursor = None
    if page and start + len(page) < len(assets):
        next_cursor = encode_cursor(page[-1], query, universe_hash)
    return page, next_cursor


async def discover_assets(query: AssetQuery) -> Dict[str, Any]:
    """``design.md`` → ``ALGORITHM discover_assets(query)``.

    Reads the cache and nothing else. On an empty cache it schedules a refresh and raises
    :class:`AssetUniverseUnavailable`; it never awaits ``load_markets`` on a caller's
    behalf, and it never substitutes a list of its own (Requirement 11.6).

    A stale-but-present universe is served with ``source_meta.stale`` and ``age_seconds``
    set, and a refresh scheduled — the requirement's error condition is an *empty* cache,
    and real markets with their age stated beat no markets at all.
    """
    universe = await read_cached_universe()

    if universe is None or universe.is_empty:
        # The design's `refresh_async()`: kicked off, never awaited here.
        started = schedule_refresh()
        raise AssetUniverseUnavailable(
            "The tradeable market universe is not available yet. It is refreshed on a "
            "schedule outside the request path; retry shortly.",
            last_error=_last_refresh_error,
            refresh_in_flight=started or _refresh_is_in_flight(),
        )

    if universe.is_stale:
        schedule_refresh()

    matching = filter_assets(universe.assets, query)
    page, next_cursor = paginate(matching, query, universe.universe_hash)

    universe_changed = False
    if query.cursor:
        try:
            issued_against = decode_cursor(query.cursor, query).get("u")
        except InvalidAssetCursor:
            raise
        universe_changed = bool(issued_against) and issued_against != universe.universe_hash

    return {
        "assets": [asset.to_dict() for asset in page],
        # Requirement 11.3: the count of everything matching the filters, not of this page.
        "total": len(matching),
        "limit": query.limit,
        "next_cursor": next_cursor,
        # Stated rather than implied: a client that paged across a refresh is told so.
        "universe_changed": universe_changed,
        "source_meta": {
            "generated_at": datetime.fromtimestamp(
                universe.generated_at, tz=timezone.utc
            ).isoformat(),
            "age_seconds": round(universe.age_seconds, 3),
            "ttl_seconds": UNIVERSE_TTL_SECONDS,
            "refresh_interval_seconds": REFRESH_INTERVAL_SECONDS,
            "stale": universe.is_stale,
            "universe_total": len(universe.assets),
            "universe_hash": universe.universe_hash,
            "exchanges": list(universe.exchanges),
            "exchanges_failed": list(universe.exchanges_failed),
            "cache_backend": universe.cache_backend,
            "refresh_in_flight": _refresh_is_in_flight(),
        },
    }


# ══════════════════════════════════════════════════════════════════════════
#  THE SCHEDULED REFRESHER — warmed at startup, looped off the request path
# ══════════════════════════════════════════════════════════════════════════


class AssetUniverseRefresher:
    """Warms the cache at startup and refreshes it on a schedule.

    Shaped like the platform's other background services (``PortfolioCacheUpdater``,
    ``DashboardDataIngester``): ``start()`` returns as soon as the task exists, so a slow
    or unreachable exchange delays no boot, and ``stop()`` cancels it. A failed cycle logs
    and waits; it never clears a cached universe, because last-known-good markets are more
    useful than none.
    """

    def __init__(self, interval_seconds: float = REFRESH_INTERVAL_SECONDS) -> None:
        self.interval = interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "[AssetUniverse] Refresher started (interval %.0fs, TTL %ds)",
            self.interval,
            UNIVERSE_TTL_SECONDS,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover - shutdown noise only
                logger.debug("[AssetUniverse] Refresher stop: %s", exc)
            self._task = None
        logger.info("[AssetUniverse] Refresher stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await refresh_universe()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # Loud, and then wait. The cache keeps what it had.
                logger.warning("[AssetUniverse] Scheduled refresh failed: %s", exc)
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break


asset_universe_refresher = AssetUniverseRefresher()


async def start_asset_universe_refresher() -> None:
    """Start the global refresher. Called once from the application lifespan."""
    await asset_universe_refresher.start()


async def stop_asset_universe_refresher() -> None:
    """Stop the global refresher. Called once from the application lifespan."""
    await asset_universe_refresher.stop()


def reset_asset_universe_state_for_tests() -> None:
    """Clear the process-local cache and refresh bookkeeping.

    Test-only. The module holds process state on purpose (a last-known-good copy and a
    single-flight guard), and a test that wants a cold cache has to be able to say so
    without reaching into module globals by hand.
    """
    global _local_universe, _refresh_task, _last_refresh_error, _last_failure_at

    _local_universe = None
    if _refresh_task is not None and not _refresh_task.done():
        _refresh_task.cancel()
    _refresh_task = None
    _last_refresh_error = None
    _last_failure_at = 0.0
