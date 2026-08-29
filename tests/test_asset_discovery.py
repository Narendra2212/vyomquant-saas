# -*- coding: utf-8 -*-
"""Asset discovery is a claim about honesty, so it is tested as one.

Spec: strategy-builder task 7.1. ``design.md`` § *DATA blocks and exchange-agnostic asset
discovery* and its API table's ``GET /api/strategy-operations/assets``. Requirements 11.1,
11.2, 11.3, 11.4, 11.5, 11.6 and 25.5.

What is actually asserted, and why each assertion earns its place
----------------------------------------------------------------
* **No hardcoded symbol universe exists in the module.** Every string constant in
  ``asset_universe.py`` — docstrings excluded, because prose naming the old defect is not
  the defect — is checked against a market-symbol pattern. The ten-symbol fallback task 7.2
  deletes must not reappear here under a new name, and a comment saying so is not a test.
* **``load_markets()`` cannot run inside a request.** Asserted structurally, on the AST:
  the only thing ``discover_assets`` awaits is the cache read, and the only thing the router
  handler awaits is ``discover_assets``. A timing assertion would be a flake; this is the
  actual claim.
* **An empty cache with a failed refresh is a 503 carrying no assets.** The body is searched
  for an asset list, not merely checked for a status code, because the failure mode this
  endpoint exists to prevent is a *successful-looking* response.
* **DEV_MODE's mock market map is refused.** ``ConnectionEngine._apply_mock_interface`` is
  invoked for real and its output fed to the detector, so if that function is renamed this
  test fails rather than the guard silently going quiet and a two-symbol universe being
  cached as the tradeable one.
* **Precision and limits are the venue's own figures.** Carried unrounded, ``None`` where
  the venue published nothing, and never blended across venues — a later order-size check
  reads these, and a fabricated minimum reaches an order router.
* **Pagination is a keyset cursor.** Swept over every page size against a fixed universe:
  pages are disjoint, their union is the whole filtered set in order, and a refresh that
  inserts and removes markets mid-pagination produces no duplicate.

Nothing here mocks the code under test. The only substitutions are the *inputs*: CCXT-shaped
market dictionaries instead of a live exchange, and a seeded cache instead of a Redis
server. Every filter, merge, sort, cursor and status-code decision is the real one.
"""

import ast
import inspect
import json
import re
import time
from types import SimpleNamespace

import pytest

from backend_app.backend import asset_universe as au
from backend_app.core.dependencies import get_current_user, get_request_supabase


ASSETS_ENDPOINT = "/api/strategy-operations/assets"

#: Captured before the autouse fixture replaces the module attribute, so the real
#: exchange-set resolution stays reachable from a test.
REAL_SUPPORTED_EXCHANGE_IDS = au.supported_exchange_ids


# ---------------------------------------------------------------------------
# CCXT-shaped inputs. Deliberately awkward: absent limits, an option market, a
# market with no quote, differing precision for the same symbol on two venues.
# ---------------------------------------------------------------------------


def market(
    symbol,
    base,
    quote,
    market_type="spot",
    active=True,
    price_precision=2,
    amount_precision=6,
    min_amount=0.0001,
    min_notional=10.0,
):
    return {
        "id": symbol.replace("/", "").replace(":", ""),
        "symbol": symbol,
        "base": base,
        "quote": quote,
        "type": market_type,
        market_type: True,
        "active": active,
        "precision": {"price": price_precision, "amount": amount_precision},
        "limits": {
            "amount": {"min": min_amount, "max": None},
            "cost": {"min": min_notional, "max": None},
        },
    }


BINANCE_MARKETS = {
    "BTC/USDT": market("BTC/USDT", "BTC", "USDT", price_precision=2, amount_precision=5),
    "ETH/USDT": market("ETH/USDT", "ETH", "USDT", price_precision=2, amount_precision=4),
    "SOL/USDT": market("SOL/USDT", "SOL", "USDT", price_precision=3, amount_precision=2),
    "ADA/BTC": market("ADA/BTC", "ADA", "BTC", price_precision=8, amount_precision=1),
    "DOGE/USDT": market("DOGE/USDT", "DOGE", "USDT", active=False),
    "BTC/USDT:USDT": market(
        "BTC/USDT:USDT", "BTC", "USDT", market_type="swap", price_precision=1
    ),
    # An option: outside the DATA descriptor's spot|swap|future, so excluded.
    "BTC/USDT:USDT-240628-70000-C": {
        "symbol": "BTC/USDT:USDT-240628-70000-C",
        "base": "BTC",
        "quote": "USDT",
        "type": "option",
        "active": True,
        "precision": {"price": 1, "amount": 1},
        "limits": {},
    },
    # No quote at all: unusable, and nothing is guessed for it.
    "BROKEN": {"symbol": "BROKEN", "base": "BRK", "type": "spot", "active": True},
}

KRAKEN_MARKETS = {
    # Same market, different figures. The merge must not blend them.
    "BTC/USDT": market("BTC/USDT", "BTC", "USDT", price_precision=1, amount_precision=8,
                       min_amount=0.002, min_notional=None),
    "XRP/USD": market("XRP/USD", "XRP", "USD", price_precision=5, amount_precision=8),
    # A venue that publishes no limits at all.
    "DOT/USD": {
        "symbol": "DOT/USD",
        "base": "DOT",
        "quote": "USD",
        "type": "spot",
        "active": True,
        "precision": {},
        "limits": {},
    },
}


def build_universe(per_exchange=None, generated_at=None):
    """A real :class:`AssetUniverse` built through the real merge."""
    if per_exchange is None:
        per_exchange = [("binance", BINANCE_MARKETS), ("kraken", KRAKEN_MARKETS)]
    assets, _stats = au.merge_markets(per_exchange)
    return au.AssetUniverse(
        assets=assets,
        generated_at=generated_at if generated_at is not None else time.time(),
        exchanges=[name for name, _ in per_exchange],
        exchanges_failed=[],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def offline_cache(monkeypatch):
    """No Redis, no exchange, no leaked module state between tests.

    ``_redis_cache`` is forced to ``None`` so the process-local copy is the cache under
    test and no test can hang on a Redis socket. ``supported_exchange_ids`` is emptied so
    that a refresh scheduled by the code under test fails immediately and offline instead
    of dialling seven venues — the scheduling itself stays real.
    """
    au.reset_asset_universe_state_for_tests()

    async def no_redis():
        return None

    monkeypatch.setattr(au, "_redis_cache", no_redis)
    monkeypatch.setattr(au, "supported_exchange_ids", lambda: [])
    yield
    au.reset_asset_universe_state_for_tests()


@pytest.fixture
def warm_cache():
    """A seeded, current universe in the process-local cache."""
    universe = build_universe()
    au._local_universe = universe
    return universe


@pytest.fixture
def user():
    return {
        "id": "usr_asset_reader",
        "email": "assets@example.com",
        "role": "authenticated",
        "access_token": "token_assets",
    }


@pytest.fixture
def client(user):
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client():
    from fastapi.testclient import TestClient

    from backend_app.main import app

    app.dependency_overrides.clear()
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. No hardcoded symbol universe. Anywhere.
# ---------------------------------------------------------------------------


SYMBOL_LITERAL = re.compile(r"\b[A-Z0-9]{2,10}/[A-Z0-9]{2,10}\b")


def _non_docstring_constants(module):
    """Every string constant in a module except docstrings.

    Docstrings are excluded deliberately: this module's prose names the ten-symbol fallback
    it exists to replace, and prose describing a defect is not the defect. Executable string
    constants are where a fallback list would actually have to live.
    """
    source = inspect.getsource(module)
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


class TestNoHardcodedUniverse:
    def test_the_module_holds_no_market_symbol_literal(self):
        """Requirement 11.6 - the fallback list must not reappear under a new name."""
        offenders = [
            text
            for text in _non_docstring_constants(au)
            if SYMBOL_LITERAL.search(text)
        ]
        assert not offenders, f"symbol-shaped literals in asset_universe.py: {offenders}"

    def test_the_exchange_set_comes_from_the_executor_not_from_here(self, monkeypatch):
        """The venues are read from the platform's own executor, not restated here.

        ``REAL_SUPPORTED_EXCHANGE_IDS`` is captured at import, before the autouse fixture
        empties the module attribute, so this exercises the real function.
        """
        monkeypatch.delenv("ASSET_UNIVERSE_EXCHANGES", raising=False)
        from backend_app.backend.exchange_executor import ExchangeExecutorFactory

        assert REAL_SUPPORTED_EXCHANGE_IDS() == [
            str(name).lower() for name in ExchangeExecutorFactory.SUPPORTED_EXCHANGES
        ]
        assert REAL_SUPPORTED_EXCHANGE_IDS()

    def test_an_operator_override_names_exchanges_and_cannot_name_a_market(
        self, monkeypatch
    ):
        monkeypatch.setenv("ASSET_UNIVERSE_EXCHANGES", "kraken, OKX ")
        assert REAL_SUPPORTED_EXCHANGE_IDS() == ["kraken", "okx"]

    def test_a_dev_mode_mock_market_map_is_refused(self):
        """DEV_MODE's two-entry mock must never be cached as the tradeable universe.

        The real ``_apply_mock_interface`` is invoked, so a rename of the injected loader
        breaks this test rather than quietly disarming the guard.
        """
        from backend_app.backend.connection_engine import ConnectionEngine

        engine = ConnectionEngine(exchange_id="binance")
        engine.exchange = SimpleNamespace()
        engine._apply_mock_interface()

        assert au._markets_are_mocked(engine.exchange) is True
        # And the mock really is a hardcoded pair list, which is why it is refused.
        assert set(engine.exchange.markets) == {"BTC/USDT", "ETH/USDT"}

    def test_a_real_exchange_object_is_not_mistaken_for_a_mock(self):
        real_ish = SimpleNamespace(markets={}, load_markets=lambda: {})
        assert au._markets_are_mocked(real_ish) is False


# ---------------------------------------------------------------------------
# 2. No load_markets() on the request path
# ---------------------------------------------------------------------------


def _awaited_call_names(func):
    """The names of every call this coroutine awaits, from its own AST."""
    source = inspect.getsource(func)
    tree = ast.parse(inspect.cleandoc(source) if source.startswith(" ") else source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            target = node.value.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


class TestRefreshIsOffTheRequestPath:
    def test_discover_assets_awaits_only_the_cache_read(self):
        """Requirement 11.5 - each request is served from the cache and nothing else."""
        assert _awaited_call_names(au.discover_assets) == {"read_cached_universe"}

    def test_discover_assets_schedules_the_refresh_without_awaiting_it(self):
        """Asserted on the AST, so the function's own prose about ``load_markets``
        cannot make the check pass or fail."""
        tree = ast.parse(inspect.getsource(au.discover_assets))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    called.add(target.id)
                elif isinstance(target, ast.Attribute):
                    called.add(target.attr)
        assert "schedule_refresh" in called
        assert "load_exchange_markets" not in called
        assert "load_markets" not in called
        assert "refresh_universe" not in called

    def test_the_endpoint_awaits_only_discover_assets(self):
        from backend_app.routers import strategy_operations as ops

        assert _awaited_call_names(ops.discover_assets_endpoint) == {"discover_assets"}

    def test_the_serve_path_never_touches_an_exchange(self, client, warm_cache, monkeypatch):
        """A warm cache is served even when the exchange loader would explode."""

        async def explode(exchange_id):
            raise AssertionError(
                f"load_exchange_markets({exchange_id!r}) was called on a request path"
            )

        monkeypatch.setattr(au, "load_exchange_markets", explode)
        response = client.get(ASSETS_ENDPOINT, params={"limit": 5})
        assert response.status_code == 200, response.text
        assert response.json()["assets"]

    def test_only_the_refresher_and_the_warm_call_the_loader(self):
        """The exchange round-trip has exactly one caller, and it is not a handler."""
        source = inspect.getsource(au)
        callers = [
            line.strip()
            for line in source.splitlines()
            if "load_exchange_markets(" in line and "def " not in line
        ]
        # One call site: the gather inside `refresh_universe`.
        assert len(callers) == 1, callers
        assert "load_exchange_markets" in inspect.getsource(au.refresh_universe)


# ---------------------------------------------------------------------------
# 3. AssetRef: the venue's own figures, carried through
# ---------------------------------------------------------------------------


class TestAssetRefMetadata:
    def test_every_documented_field_is_published(self, warm_cache):
        """Requirement 11.4 - the whole list, per asset."""
        record = next(a for a in warm_cache.assets if a.symbol == "SOL/USDT").to_dict()
        assert set(record) >= {
            "symbol",
            "base",
            "quote",
            "market_type",
            "active",
            "price_precision",
            "amount_precision",
            "min_notional",
            "min_amount",
            "available_on",
        }

    def test_precision_and_limits_are_verbatim(self, warm_cache):
        sol = next(a for a in warm_cache.assets if a.symbol == "SOL/USDT")
        assert sol.price_precision == 3
        assert sol.amount_precision == 2
        assert sol.min_amount == 0.0001
        assert sol.min_notional == 10.0

    def test_an_unpublished_figure_is_none_and_not_a_default(self, warm_cache):
        """A defaulted 8, or a 0, would be an invented trading constraint."""
        dot = next(a for a in warm_cache.assets if a.symbol == "DOT/USD")
        assert dot.price_precision is None
        assert dot.amount_precision is None
        assert dot.min_amount is None
        assert dot.min_notional is None

    def test_a_fractional_tick_size_is_not_rounded_to_an_integer(self):
        assets, _ = au.merge_markets(
            [("binance", {"X/Y": market("XBT/EUR", "XBT", "EUR", price_precision=0.01)})]
        )
        assert assets[0].price_precision == 0.01

    def test_market_types_outside_the_descriptor_are_excluded_not_relabelled(self, warm_cache):
        types = {a.market_type for a in warm_cache.assets}
        assert types <= set(au.SUPPORTED_MARKET_TYPES)
        assert not [a for a in warm_cache.assets if "option" in a.symbol]

    def test_a_market_with_no_quote_is_dropped(self, warm_cache):
        assert not [a for a in warm_cache.assets if a.symbol == "BROKEN"]

    def test_spot_and_swap_for_one_base_are_separate_records(self, warm_cache):
        symbols = {(a.symbol, a.market_type) for a in warm_cache.assets}
        assert ("BTC/USDT", "spot") in symbols
        assert ("BTC/USDT:USDT", "swap") in symbols


# ---------------------------------------------------------------------------
# 4. The union across venues
# ---------------------------------------------------------------------------


class TestMergeAcrossVenues:
    def test_available_on_lists_every_venue_holding_the_market(self, warm_cache):
        btc = next(
            a for a in warm_cache.assets if a.symbol == "BTC/USDT" and a.market_type == "spot"
        )
        assert set(btc.available_on) == {"binance", "kraken"}
        assert btc.listing_count == 2

    def test_precision_is_attributed_to_one_named_venue_and_not_blended(self, warm_cache):
        """Averaging two venues' tick sizes would describe a market that trades nowhere."""
        btc = next(
            a for a in warm_cache.assets if a.symbol == "BTC/USDT" and a.market_type == "spot"
        )
        assert btc.precision_source == "binance"
        assert btc.price_precision == 2  # binance's figure, whole
        assert btc.amount_precision == 5
        assert btc.min_amount == 0.0001
        assert btc.min_notional == 10.0

    def test_a_single_venue_market_lists_only_that_venue(self, warm_cache):
        xrp = next(a for a in warm_cache.assets if a.symbol == "XRP/USD")
        assert xrp.available_on == ("kraken",)
        assert xrp.precision_source == "kraken"

    def test_the_merge_counts_what_it_excluded(self):
        _assets, stats = au.merge_markets(
            [("binance", BINANCE_MARKETS), ("kraken", KRAKEN_MARKETS)]
        )
        assert stats["considered"] == len(BINANCE_MARKETS) + len(KRAKEN_MARKETS)
        # The option and the quote-less entry.
        assert stats["excluded_unusable"] == 2
        assert stats["duplicate_listings"] == 1


# ---------------------------------------------------------------------------
# 5. Ordering
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_served_order_is_listing_breadth_then_symbol(self, warm_cache):
        keys = [a.sort_key() for a in warm_cache.assets]
        assert keys == sorted(keys)

    def test_the_most_widely_listed_market_comes_first(self, warm_cache):
        assert warm_cache.assets[0].symbol == "BTC/USDT"
        assert warm_cache.assets[0].listing_count == 2

    def test_listing_count_is_not_presented_as_liquidity(self):
        """A proxy must not be labelled with the thing it is a proxy for."""
        served = json.dumps(build_universe().assets[0].to_dict())
        assert "liquidity" not in served.lower()


# ---------------------------------------------------------------------------
# 6. Filtering (Requirement 11.2)
# ---------------------------------------------------------------------------


class TestFiltering:
    def test_active_only_removes_inactive_markets(self, warm_cache):
        active = au.filter_assets(warm_cache.assets, au.AssetQuery(active_only=True))
        everything = au.filter_assets(warm_cache.assets, au.AssetQuery(active_only=False))
        assert {a.symbol for a in everything} - {a.symbol for a in active} == {"DOGE/USDT"}
        assert all(a.active is True for a in active)

    def test_market_type_filter(self, warm_cache):
        swaps = au.filter_assets(
            warm_cache.assets, au.AssetQuery(market_type="swap", active_only=False)
        )
        assert {a.symbol for a in swaps} == {"BTC/USDT:USDT"}

    def test_base_and_quote_filters_are_exact_and_case_insensitive(self, warm_cache):
        by_quote = au.filter_assets(
            warm_cache.assets, au.AssetQuery(quote="usdt", active_only=False)
        )
        assert by_quote and all(a.quote == "USDT" for a in by_quote)
        by_base = au.filter_assets(
            warm_cache.assets, au.AssetQuery(base="btc", active_only=False)
        )
        assert {a.symbol for a in by_base} == {"BTC/USDT", "BTC/USDT:USDT"}

    def test_search_matches_symbol_base_and_quote(self, warm_cache):
        hits = au.filter_assets(
            warm_cache.assets, au.AssetQuery(search="sol", active_only=False)
        )
        assert {a.symbol for a in hits} == {"SOL/USDT"}
        by_quote_text = au.filter_assets(
            warm_cache.assets, au.AssetQuery(search="usd", active_only=False)
        )
        assert {"XRP/USD", "DOT/USD"} <= {a.symbol for a in by_quote_text}

    def test_search_does_not_offer_a_market_the_author_did_not_ask_for(self, warm_cache):
        """An edit-distance match that answered BTC with ETH would be worse than nothing."""
        hits = au.filter_assets(
            warm_cache.assets, au.AssetQuery(search="BTC", active_only=False)
        )
        assert all("BTC" in a.symbol.upper() for a in hits)

    def test_each_filter_narrows_monotonically(self, warm_cache):
        """`design.md`'s loop invariant: every result is a subset of the live universe."""
        whole = {a.symbol for a in warm_cache.assets}
        previous = whole
        for query in (
            au.AssetQuery(active_only=False),
            au.AssetQuery(active_only=False, quote="USDT"),
            au.AssetQuery(active_only=False, quote="USDT", market_type="spot"),
            au.AssetQuery(active_only=False, quote="USDT", market_type="spot", search="t"),
        ):
            current = {a.symbol for a in au.filter_assets(warm_cache.assets, query)}
            assert current <= previous <= whole
            previous = current

    def test_an_unmatched_filter_is_an_empty_page_not_an_error(self, warm_cache):
        assert au.filter_assets(warm_cache.assets, au.AssetQuery(base="NOSUCHCOIN")) == []


# ---------------------------------------------------------------------------
# 7. Pagination: a keyset cursor, not an offset (Requirement 11.3)
# ---------------------------------------------------------------------------


class TestPagination:
    @pytest.mark.parametrize("limit", [1, 2, 3, 4, 5, 7, 100])
    def test_pages_are_disjoint_and_their_union_is_the_whole_ordered_set(
        self, warm_cache, limit
    ):
        query = au.AssetQuery(active_only=False, limit=limit)
        expected = au.filter_assets(warm_cache.assets, query)

        seen = []
        cursor = None
        for _ in range(len(expected) + 2):
            page, cursor = au.paginate(
                expected,
                au.AssetQuery(active_only=False, limit=limit, cursor=cursor),
                warm_cache.universe_hash,
            )
            seen.extend(page)
            if cursor is None:
                break
        assert cursor is None
        assert [a.symbol for a in seen] == [a.symbol for a in expected]
        assert len(seen) == len(set((a.symbol, a.market_type) for a in seen))

    def test_the_cursor_is_opaque_and_carries_no_offset(self, warm_cache):
        query = au.AssetQuery(active_only=False, limit=2)
        page, cursor = au.paginate(
            au.filter_assets(warm_cache.assets, query), query, warm_cache.universe_hash
        )
        assert cursor and isinstance(cursor, str)
        decoded = au.decode_cursor(cursor, query)
        assert decoded["k"] == list(page[-1].sort_key())
        assert "offset" not in json.dumps(decoded)

    def test_a_refresh_between_pages_produces_no_duplicate(self, warm_cache):
        """A keyset cursor survives an insert before it; an offset would not."""
        query = au.AssetQuery(active_only=False, limit=3)
        first = au.filter_assets(warm_cache.assets, query)
        page_one, cursor = au.paginate(first, query, warm_cache.universe_hash)

        # The universe changes: a new widely-listed market arrives (sorting FIRST, i.e.
        # before the cursor) and a late-alphabet one is delisted.
        changed_markets = dict(BINANCE_MARKETS)
        changed_markets["AAA/USDT"] = market("AAA/USDT", "AAA", "USDT")
        kraken = dict(KRAKEN_MARKETS)
        kraken["AAA/USDT"] = market("AAA/USDT", "AAA", "USDT")
        kraken.pop("XRP/USD")
        refreshed = build_universe([("binance", changed_markets), ("kraken", kraken)])

        second = au.filter_assets(refreshed.assets, query)
        page_two, _ = au.paginate(
            second,
            au.AssetQuery(active_only=False, limit=3, cursor=cursor),
            refreshed.universe_hash,
        )
        overlap = {(a.symbol, a.market_type) for a in page_one} & {
            (a.symbol, a.market_type) for a in page_two
        }
        assert not overlap, f"a refresh produced duplicated rows: {overlap}"
        # And every row on page two sorts strictly after the cursor.
        assert all(a.sort_key() > page_one[-1].sort_key() for a in page_two)

    def test_a_cursor_from_a_different_filter_set_is_refused(self, warm_cache):
        query = au.AssetQuery(active_only=False, limit=2)
        _page, cursor = au.paginate(
            au.filter_assets(warm_cache.assets, query), query, warm_cache.universe_hash
        )
        other = au.AssetQuery(active_only=False, limit=2, quote="USDT", cursor=cursor)
        with pytest.raises(au.InvalidAssetCursor):
            au.decode_cursor(cursor, other)

    @pytest.mark.parametrize(
        "bad", ["", "not-base64!!", "YWJj", "e30", "eyJ2Ijo5OTk5fQ"]
    )
    def test_an_unreadable_cursor_is_refused_rather_than_reinterpreted(self, bad):
        with pytest.raises(au.InvalidAssetCursor):
            au.decode_cursor(bad, au.AssetQuery())

    def test_the_last_page_carries_no_next_cursor(self, warm_cache):
        query = au.AssetQuery(active_only=False, limit=500)
        page, cursor = au.paginate(
            au.filter_assets(warm_cache.assets, query), query, warm_cache.universe_hash
        )
        assert cursor is None
        assert page


# ---------------------------------------------------------------------------
# 8. discover_assets: cache semantics and the unavailable case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDiscoverAssets:
    async def test_a_warm_cache_is_served_with_total_and_provenance(self, warm_cache):
        page = await au.discover_assets(au.AssetQuery(active_only=False, limit=3))
        assert len(page["assets"]) == 3
        assert page["total"] == len(warm_cache.assets)
        assert page["next_cursor"]
        meta = page["source_meta"]
        assert meta["ttl_seconds"] == au.UNIVERSE_TTL_SECONDS == 6 * 60 * 60
        assert meta["stale"] is False
        assert meta["universe_total"] == len(warm_cache.assets)
        assert meta["exchanges"] == ["binance", "kraken"]
        assert meta["cache_backend"] == "process"

    async def test_total_counts_the_filtered_set_not_the_page(self, warm_cache):
        page = await au.discover_assets(
            au.AssetQuery(active_only=False, quote="USDT", limit=1)
        )
        expected = len(
            au.filter_assets(warm_cache.assets, au.AssetQuery(active_only=False, quote="USDT"))
        )
        assert page["total"] == expected > 1
        assert len(page["assets"]) == 1

    async def test_an_empty_cache_with_a_failed_refresh_is_unavailable(self):
        au._local_universe = None
        with pytest.raises(au.AssetUniverseUnavailable) as excinfo:
            await au.discover_assets(au.AssetQuery())
        assert excinfo.value.code == "ASSET_UNIVERSE_UNAVAILABLE"

    async def test_an_empty_cache_schedules_a_refresh_rather_than_awaiting_one(self):
        au._local_universe = None
        with pytest.raises(au.AssetUniverseUnavailable):
            await au.discover_assets(au.AssetQuery())
        # A task was created for the refresh; the caller was not made to wait for it.
        assert au._refresh_task is not None

    async def test_a_stale_universe_is_served_and_labelled_stale(self):
        """The requirement's error condition is an EMPTY cache, not an old one."""
        au._local_universe = build_universe(
            generated_at=time.time() - (au.UNIVERSE_TTL_SECONDS + 600)
        )
        page = await au.discover_assets(au.AssetQuery(active_only=False, limit=2))
        assert page["assets"]
        assert page["source_meta"]["stale"] is True
        assert page["source_meta"]["age_seconds"] > au.UNIVERSE_TTL_SECONDS

    async def test_a_universe_that_changed_between_pages_is_reported(self, warm_cache):
        first = await au.discover_assets(au.AssetQuery(active_only=False, limit=2))
        cursor = first["next_cursor"]
        assert first["universe_changed"] is False

        changed = dict(BINANCE_MARKETS)
        changed["AAA/USDT"] = market("AAA/USDT", "AAA", "USDT")
        au._local_universe = build_universe([("binance", changed), ("kraken", KRAKEN_MARKETS)])

        second = await au.discover_assets(
            au.AssetQuery(active_only=False, limit=2, cursor=cursor)
        )
        assert second["universe_changed"] is True

    async def test_an_empty_cached_payload_is_treated_as_no_cache(self):
        au._local_universe = au.AssetUniverse(assets=[], generated_at=time.time())
        with pytest.raises(au.AssetUniverseUnavailable):
            await au.discover_assets(au.AssetQuery())


# ---------------------------------------------------------------------------
# 9. Refresh: partial failure, total failure, no cache clobbering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRefresh:
    async def test_one_unreachable_venue_does_not_blank_the_universe(self, monkeypatch):
        monkeypatch.setattr(au, "supported_exchange_ids", lambda: ["binance", "kraken"])

        async def loader(exchange_id):
            if exchange_id == "kraken":
                raise ConnectionError("kraken is down")
            return BINANCE_MARKETS

        monkeypatch.setattr(au, "load_exchange_markets", loader)
        universe = await au.refresh_universe()
        assert universe.exchanges == ["binance"]
        assert universe.exchanges_failed == ["kraken"]
        assert universe.assets

    async def test_every_venue_failing_raises_and_keeps_the_previous_universe(
        self, monkeypatch, warm_cache
    ):
        monkeypatch.setattr(au, "supported_exchange_ids", lambda: ["binance"])

        async def loader(exchange_id):
            raise ConnectionError("no network")

        monkeypatch.setattr(au, "load_exchange_markets", loader)
        with pytest.raises(au.AssetUniverseUnavailable):
            await au.refresh_universe()
        # Last-known-good survives a failed refresh.
        assert au._local_universe is warm_cache
        served = await au.discover_assets(au.AssetQuery(active_only=False, limit=1))
        assert served["assets"]

    async def test_a_successful_refresh_populates_the_cache(self, monkeypatch):
        monkeypatch.setattr(au, "supported_exchange_ids", lambda: ["binance"])

        async def loader(exchange_id):
            return BINANCE_MARKETS

        monkeypatch.setattr(au, "load_exchange_markets", loader)
        universe = await au.refresh_universe()
        assert au._local_universe is universe
        cached = await au.read_cached_universe()
        assert cached is universe

    async def test_a_redis_failure_degrades_to_the_local_copy(self, monkeypatch):
        """Redis absent or down must not become a 500 and must not fabricate a universe."""

        class BrokenManager:
            async def cache_get_json(self, key):
                raise RuntimeError("redis is gone")

            async def cache_set_json(self, key, value, ttl=0):
                raise RuntimeError("redis is gone")

        async def broken():
            return BrokenManager()

        monkeypatch.setattr(au, "_redis_cache", broken)
        universe = build_universe()
        assert await au.write_cached_universe(universe) is False
        assert await au.read_cached_universe() is universe

    async def test_no_redis_at_all_still_serves_after_a_refresh(self, monkeypatch):
        monkeypatch.setattr(au, "supported_exchange_ids", lambda: ["binance"])

        async def loader(exchange_id):
            return BINANCE_MARKETS

        monkeypatch.setattr(au, "load_exchange_markets", loader)
        await au.refresh_universe()
        page = await au.discover_assets(au.AssetQuery(active_only=False, limit=2))
        assert page["assets"]
        assert page["source_meta"]["cache_backend"] == "process"

    async def test_the_ttl_is_six_hours(self):
        """Requirement 25.5, stated as a number rather than as a comment."""
        assert au.UNIVERSE_TTL_SECONDS == 21600
        source = inspect.getsource(au.write_cached_universe)
        assert "ttl=UNIVERSE_TTL_SECONDS" in source

    async def test_the_scheduled_interval_stays_inside_the_ttl(self):
        """A refresh slower than the TTL would let a served entry expire."""
        assert 0 < au.REFRESH_INTERVAL_SECONDS <= au.UNIVERSE_TTL_SECONDS

    async def test_a_cached_payload_round_trips(self):
        universe = build_universe()
        restored = au.AssetUniverse.from_payload(
            json.loads(json.dumps(universe.to_payload())), cache_backend="redis"
        )
        assert [a.to_dict() for a in restored.assets] == [
            a.to_dict() for a in universe.assets
        ]
        assert restored.universe_hash == universe.universe_hash

    async def test_an_unreadable_cached_payload_is_rejected_whole(self):
        with pytest.raises(ValueError):
            au.AssetUniverse.from_payload({"schema_version": 999}, cache_backend="redis")
        with pytest.raises(ValueError):
            au.AssetUniverse.from_payload(
                {"schema_version": au.UNIVERSE_SCHEMA_VERSION}, cache_backend="redis"
            )

    async def test_the_refresher_starts_and_stops_without_touching_a_venue(self, monkeypatch):
        calls = []

        async def loader(exchange_id):
            calls.append(exchange_id)
            return BINANCE_MARKETS

        monkeypatch.setattr(au, "load_exchange_markets", loader)
        refresher = au.AssetUniverseRefresher(interval_seconds=3600)
        await refresher.start()
        assert refresher.running is True
        await refresher.stop()
        assert refresher.running is False
        # `supported_exchange_ids` is empty in this fixture, so a cycle reaches no venue.
        assert calls == []


# ---------------------------------------------------------------------------
# 10. The endpoint
# ---------------------------------------------------------------------------


class TestAssetsEndpoint:
    def test_the_route_is_mounted_where_the_design_says(self):
        from backend_app.main import app

        assert ASSETS_ENDPOINT in {route.path for route in app.routes}

    def test_a_page_carries_assets_total_cursor_and_provenance(self, client, warm_cache):
        response = client.get(ASSETS_ENDPOINT, params={"active_only": False, "limit": 3})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {
            "assets",
            "total",
            "limit",
            "next_cursor",
            "universe_changed",
            "source_meta",
        }
        assert len(payload["assets"]) == 3
        assert payload["total"] == len(warm_cache.assets)
        assert payload["next_cursor"]
        assert response.headers["X-Asset-Universe-Hash"] == warm_cache.universe_hash
        assert "private" in response.headers["Cache-Control"]

    def test_each_record_carries_precision_and_limit_metadata(self, client, warm_cache):
        payload = client.get(
            ASSETS_ENDPOINT, params={"active_only": False, "search": "SOL"}
        ).json()
        record = payload["assets"][0]
        assert record["symbol"] == "SOL/USDT"
        assert record["price_precision"] == 3
        assert record["amount_precision"] == 2
        assert record["min_amount"] == 0.0001
        assert record["min_notional"] == 10.0
        assert record["available_on"] == ["binance"]
        assert record["precision_source"] == "binance"

    def test_filters_reach_the_service(self, client, warm_cache):
        swap = client.get(
            ASSETS_ENDPOINT, params={"market_type": "swap", "active_only": False}
        ).json()
        assert [a["symbol"] for a in swap["assets"]] == ["BTC/USDT:USDT"]

        quoted = client.get(
            ASSETS_ENDPOINT, params={"quote": "USD", "active_only": False}
        ).json()
        assert {a["symbol"] for a in quoted["assets"]} == {"XRP/USD", "DOT/USD"}

    def test_active_only_defaults_to_true(self, client, warm_cache):
        payload = client.get(ASSETS_ENDPOINT, params={"limit": 500}).json()
        assert all(a["active"] is True for a in payload["assets"])
        assert "DOGE/USDT" not in {a["symbol"] for a in payload["assets"]}

    def test_pagination_walks_the_whole_universe_without_repeating(self, client, warm_cache):
        seen = []
        cursor = None
        for _ in range(20):
            params = {"active_only": False, "limit": 2}
            if cursor:
                params["cursor"] = cursor
            payload = client.get(ASSETS_ENDPOINT, params=params).json()
            seen.extend(a["symbol"] + "|" + a["market_type"] for a in payload["assets"])
            cursor = payload["next_cursor"]
            if not cursor:
                break
        assert cursor is None
        assert len(seen) == len(set(seen)) == len(warm_cache.assets)

    def test_an_invalid_cursor_is_422_and_names_the_problem(self, client, warm_cache):
        response = client.get(
            ASSETS_ENDPOINT, params={"cursor": "definitely-not-a-cursor"}
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["error"] == "ASSET_CURSOR_INVALID"

    def test_an_empty_cache_is_503_and_returns_no_symbols(self, client):
        """Requirement 11.6 - and the body is checked, not just the status code."""
        au._local_universe = None
        response = client.get(ASSETS_ENDPOINT)
        assert response.status_code == 503, response.text
        detail = response.json()["detail"]
        assert detail["error"] == "ASSET_UNIVERSE_UNAVAILABLE"
        assert response.headers["Retry-After"] == str(au.RETRY_AFTER_SECONDS)
        # No substitute universe, under any key or nesting.
        body = json.dumps(response.json())
        assert "assets" not in response.json()
        assert not SYMBOL_LITERAL.search(body), body

    def test_an_unmatched_filter_is_an_empty_200_not_a_503(self, client, warm_cache):
        response = client.get(ASSETS_ENDPOINT, params={"base": "NOSUCHCOIN"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["assets"] == []
        assert payload["total"] == 0
        assert payload["next_cursor"] is None

    def test_the_limit_is_bounded_server_side(self, client, warm_cache):
        assert client.get(ASSETS_ENDPOINT, params={"limit": 0}).status_code == 422
        assert client.get(ASSETS_ENDPOINT, params={"limit": 10_000}).status_code == 422


# ---------------------------------------------------------------------------
# 11. Controls
# ---------------------------------------------------------------------------


class TestControls:
    def test_an_unauthenticated_caller_is_refused(self, anonymous_client):
        response = anonymous_client.get(ASSETS_ENDPOINT)
        assert response.status_code in (401, 403), response.text
        assert "assets" not in response.json() or response.status_code != 200

    def test_the_handler_depends_on_the_current_user(self):
        from backend_app.routers import strategy_operations as ops

        params = inspect.signature(ops.discover_assets_endpoint).parameters
        assert "get_current_user" in str(params["user"].default)

    def test_the_handler_carries_a_slowapi_limit(self):
        from backend_app.routers import strategy_operations as ops

        source = inspect.getsource(ops)
        marker = "async def discover_assets_endpoint("
        assert marker in source
        decorators = source[: source.index(marker)].rsplit("@router.get", 1)[-1]
        assert "@limiter.limit(" in decorators

    def test_the_endpoint_returns_no_credential_or_exchange_account(self, client, warm_cache):
        """Reference data only. `available_on` is venue ids, never an account or a key."""
        body = json.dumps(
            client.get(ASSETS_ENDPOINT, params={"active_only": False, "limit": 500}).json()
        ).lower()
        for forbidden in ("api_key", "apikey", "secret", "passphrase", "exchange_account"):
            assert forbidden not in body

    def test_the_universe_is_identical_for_two_different_callers(self, warm_cache, user):
        """Recorded decision: this is global reference data with no per-tenant scoping."""
        from fastapi.testclient import TestClient

        from backend_app.main import app

        payloads = []
        for identity in (user, {**user, "id": "usr_other_tenant", "access_token": "t2"}):
            app.dependency_overrides[get_current_user] = lambda identity=identity: identity
            app.dependency_overrides[get_request_supabase] = lambda: None
            try:
                # No context manager: the app lifespan must not run in a unit test.
                tenant_client = TestClient(app)
                payloads.append(
                    tenant_client.get(
                        ASSETS_ENDPOINT, params={"active_only": False, "limit": 500}
                    ).json()["assets"]
                )
            finally:
                app.dependency_overrides.clear()
        assert payloads[0] == payloads[1]
