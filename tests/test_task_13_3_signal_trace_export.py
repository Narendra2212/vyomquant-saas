"""
tests/test_task_13_3_signal_trace_export.py

Task 13.3 - ``GET /api/signal-trace/signals/export``.

Requirement 17.1 (every signal the user's deployments generated, none omitted), and -
because the export IS task 13.1's list walked to the end - Requirements 17.2 (the filter
semantics), 17.3 (the per-signal fields), 20.1/20.2 (a non-owner gets the missing-resource
answer) and 20.3 (no credential on the wire).

Six things are under test and nothing else:

  1. REACHABILITY, against the REAL ``backend_app.main.app``. The literal
     ``/signals/export`` must resolve to the export handler and not to
     ``/signals/{signal_id}``. This is the failure that killed
     ``GET /api/signals/export``, which is still dead in ``routers/signals.py`` today.

  2. THE WALK. A filtered result larger than one page is exported WHOLE (Requirement
     17.1's "SHALL NOT omit"), a row that lands on two pages because of a concurrent
     insert is emitted once, and the row ceiling - when it is reached - is DECLARED.

  3. THE CSV BODY. The header is the declared constant in the declared order, one line per
     signal, an empty export still has a header, and a cell a spreadsheet would read as a
     formula is defused without numbers being mangled.

  4. THE JSON BODY. The envelope's metadata plus the same items the CSV was rendered from.

  5. THE FILTERS AND THE REFUSALS. Every filter is the list's own (an exported view is the
     view), an unrecognised Order_Lifecycle_State is refused rather than ignored, and an
     unsupported format is refused rather than silently answered as JSON.

  6. THE RESPONSE. Content-Type, the ``attachment`` filename ``SignalTrace.jsx`` saves,
     the three ``X-Export-*`` headers a CSV has nowhere else to put, and the 400 for a bad
     format - asserted through the mounted app.

What is NOT tested here: the list's own filter and pagination semantics (task 13.1) and
the trace detail (task 13.2), both already covered.
"""

import csv
import io
import json

import pytest
from starlette.routing import Match

from backend_app.backend import signal_service as svc
from backend_app.backend.order_lifecycle_state import OrderLifecycleRejected
from backend_app.backend.signal_service import (
    SIGNAL_TRACE_EXPORT_COLUMNS,
    SIGNAL_TRACE_EXPORT_FORMATS,
    SIGNAL_TRACE_EXPORT_MAX_ROWS,
    SIGNAL_TRACE_PAGE_SIZE,
    SignalExportRefused,
    SignalService,
    normalise_export_format,
    render_signal_trace_csv,
)

OWNER = {"id": "user-aaaa", "access_token": "token-aaaa"}
INTRUDER = {"id": "user-zzzz", "access_token": "token-zzzz"}


# ══════════════════════════════════════════════════════════════════════════
# FIXTURES - the same fake PostgREST client task 13.1 uses: it APPLIES the
# predicates, so a filter or an ownership claim here is a real claim.
# ══════════════════════════════════════════════════════════════════════════


class _Result:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error


class FakeQuery:
    def __init__(self, client, columns):
        self.client = client
        self.columns = columns
        self.predicates = []
        self._negate = False
        self._range = None
        self._limit = None
        self._desc = False

    def eq(self, column, value):
        self.predicates.append(("eq", column, value))
        return self

    def in_(self, column, values):
        self.predicates.append(("in", column, list(values)))
        return self

    def gte(self, column, value):
        self.predicates.append(("gte", column, value))
        return self

    def lte(self, column, value):
        self.predicates.append(("lte", column, value))
        return self

    def is_(self, column, value):
        self.predicates.append(("is_not" if self._negate else "is", column, value))
        self._negate = False
        return self

    @property
    def not_(self):
        self._negate = True
        return self

    def order(self, column, desc=False):
        self._desc = desc
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        self.client.queries.append(self)
        if not self.client.lifecycle_columns and "order_lifecycle_state" in self.columns:
            raise Exception(
                "ERROR: 42703: column signals.order_lifecycle_state does not exist"
            )
        if self._limit is not None and not self.predicates:
            return _Result(data=[])  # the 005b probe

        rows = [row for row in self.client.rows if self._matches(row)]
        rows.sort(key=lambda r: r.get("generated_at") or "", reverse=self._desc)
        if self._range is not None:
            start, end = self._range
            rows = rows[start : end + 1]
        return _Result(data=[self._project(row) for row in rows])

    def _project(self, row):
        if self.client.lifecycle_columns:
            return dict(row)
        return {k: v for k, v in row.items() if k != "order_lifecycle_state"}

    def _matches(self, row):
        for kind, column, value in self.predicates:
            actual = row.get(column)
            if kind == "eq" and actual != value:
                return False
            if kind == "in" and actual not in value:
                return False
            if kind == "gte" and (actual or "") < value:
                return False
            if kind == "lte" and (actual or "") > value:
                return False
            if kind == "is" and actual is not None:
                return False
            if kind == "is_not" and actual is None:
                return False
        return True


class FakeSupabase:
    def __init__(self, rows, *, lifecycle_columns=True):
        self.rows = list(rows)
        self.lifecycle_columns = lifecycle_columns
        self.queries = []

    def table(self, name):
        assert name == "signals"
        return self

    def select(self, columns):
        return FakeQuery(self, columns)


def service_for(rows, *, lifecycle_columns=True):
    service = SignalService()
    client = FakeSupabase(rows, lifecycle_columns=lifecycle_columns)

    async def _client(_user):
        return client

    service._get_supabase = _client  # type: ignore[method-assign]
    return service, client


def signal_row(
    signal_id,
    *,
    user_id=OWNER["id"],
    strategy_id="strat-1",
    strategy_version="v1",
    deployment_id="dep-1",
    symbol="BTC/USDT",
    decision="BUY",
    state="EXECUTED",
    status="executed",
    generated_at="2024-05-01T12:00:00+00:00",
    risk_reason="within limits",
):
    """One ``public.signals`` row, in the shape ``Signal.to_row`` writes."""
    return {
        "id": signal_id,
        "user_id": user_id,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "deployment_id": deployment_id,
        "exchange_id": "kraken",
        "symbol": symbol,
        "timeframe": "1h",
        "worker_id": "worker-7",
        "decision": decision,
        "status": status,
        "order_lifecycle_state": state,
        "quantity": 0.25,
        "generated_at": generated_at,
        "indicators": {"rsi-1": 28.4},
        "market_info": {
            "signal_type": "ENTRY" if decision in ("BUY", "SELL") else "EXIT",
            "side": decision if decision in ("BUY", "SELL") else None,
            "mode": "paper",
            "strategy_version_id": "ver-1",
            "exchange_account_id": "acct-1",
            "source_node_ids": ["action-1"],
            "closure_ready": True,
            "sizing_intention": None,
            "price": 61234.5,
        },
        "ml_info": None,
        "risk_passed": True,
        "risk_reason": risk_reason,
        "position_size": 0.25,
        "risk_evaluated_at": "2024-05-01T12:00:01+00:00",
        "order_id": "ord-1",
        "order_status": "FILLED",
        "filled": 0.25,
        "remaining": 0.0,
        "average_price": 61000.0,
        "fees": 1.23,
        "slippage": 0.02,
        "latency_ms": 84.0,
        "trade_id": "trd-1",
        "pnl": -42.0,
        "realized_pnl": -42.0,
        "order_updated_at": "2024-05-01T12:00:03+00:00",
        "executed_at": "2024-05-01T12:00:04+00:00",
    }


def many_rows(count, *, prefix="s"):
    """``count`` rows with distinct generation times, so the DESC sort is total."""
    return [
        signal_row(
            f"{prefix}{index:04d}",
            generated_at=(
                f"2024-05-01T{12 + index // 3600:02d}:"
                f"{(index // 60) % 60:02d}:{index % 60:02d}+00:00"
            ),
        )
        for index in range(count)
    ]


def minted_signal():
    """A signal minted from a deployment that CARRIES a secret. Task 13.1's fixture.

    The containment claim is structural - ``Signal``'s field set is closed - so the test
    for it has to go through the mint, not through a hand-edited ``market_info``.
    """
    from backend_app.backend.signal_service import mint_signal

    deployment = {
        "id": "dep-1111",
        "user_id": OWNER["id"],
        "strategy_id": "strat-bbbb",
        "version": "v3",
        "version_id": "ver-cccc",
        "exchange_account_id": "acct-dddd",
        "exchange_id": "kraken",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "mode": "paper",
        "worker_id": "worker-7",
        "api_secret": "s3cr3t-shhh",
    }
    node_output = {
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
    return mint_signal(deployment, node_output)


@pytest.fixture(autouse=True)
def _forget_migration_verdict():
    svc.reset_signal_lifecycle_column_support()
    yield
    svc.reset_signal_lifecycle_column_support()


def csv_rows(content):
    """The CSV body parsed back, as ``(header, [row, ...])``."""
    reader = list(csv.reader(io.StringIO(content, newline="")))
    return reader[0], reader[1:]


# ══════════════════════════════════════════════════════════════════════════
# 1. REACHABILITY ON THE REAL APP
# ══════════════════════════════════════════════════════════════════════════


def _resolve(path, method="GET"):
    """The route the MOUNTED app would actually dispatch ``path`` + ``method`` to."""
    from backend_app.main import app

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }
    for route in app.routes:
        match, _ = route.matches(scope)
        if match is Match.FULL:
            return route
    return None


def test_the_export_route_resolves_to_the_export_handler():
    """Not to ``/signals/{signal_id}`` with ``signal_id="export"``, which 404s forever."""
    route = _resolve("/api/signal-trace/signals/export")
    assert route is not None, "the export path resolves to nothing"
    assert route.path == "/api/signal-trace/signals/export", (
        f"the export path is shadowed by {route.path!r} ({route.name})"
    )
    assert route.name == "export_signals"


def test_the_export_path_is_declared_in_the_literal_paths_constant():
    """So task 13.1's derived guard test covers it without being edited."""
    from backend_app.routers.signal_trace import SIGNAL_TRACE_LITERAL_PATHS

    assert "/api/signal-trace/signals/export" in SIGNAL_TRACE_LITERAL_PATHS


def test_the_export_route_is_registered_exactly_once():
    from fastapi.routing import APIRoute

    from backend_app.main import app

    handlers = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/signal-trace/signals/export"
        and "GET" in route.methods
    ]
    assert len(handlers) == 1, f"{len(handlers)} handlers; all but the first are dead code"


def test_the_export_accepts_the_same_filter_categories_the_list_does():
    """An export of a filtered view has to be able to RECEIVE the filter.

    Compared against the list route's own parameter names rather than a transcribed list,
    so a category added to one and not the other fails here.
    """
    list_params = {
        p.name for p in _resolve("/api/signal-trace/signals").dependant.query_params
    }
    export_params = {
        p.name
        for p in _resolve("/api/signal-trace/signals/export").dependant.query_params
    }

    # The export has no page: `limit`/`offset` are the list's alone, and `format` is the
    # export's alone.
    assert list_params - export_params == {"limit", "offset"}
    assert export_params - list_params == {"format"}


# ══════════════════════════════════════════════════════════════════════════
# 2. THE WALK (Requirement 17.1: no signal omitted)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_an_export_larger_than_one_page_carries_every_signal():
    """250 signals, 100 per page. A one-page export would omit 150 of them."""
    rows = many_rows(250)
    service, _ = service_for(rows)

    export = await service.export_signal_trace(OWNER, format="json")

    assert export["row_count"] == 250
    assert export["truncated"] is False
    assert {item["id"] for item in export["signals"]} == {row["id"] for row in rows}


@pytest.mark.asyncio
async def test_the_export_walks_pages_of_the_lists_own_page_size():
    rows = many_rows(250)
    service, client = service_for(rows)

    await service.export_signal_trace(OWNER, format="csv")

    listing = [q for q in client.queries if q.predicates and q._range is not None]
    # limit+1 per page: the extra row IS has_more, and is never emitted.
    spans = [q._range for q in listing]
    assert spans == [
        (0, SIGNAL_TRACE_PAGE_SIZE),
        (SIGNAL_TRACE_PAGE_SIZE, 2 * SIGNAL_TRACE_PAGE_SIZE),
        (2 * SIGNAL_TRACE_PAGE_SIZE, 3 * SIGNAL_TRACE_PAGE_SIZE),
    ], spans


@pytest.mark.asyncio
async def test_a_signal_appearing_on_two_pages_is_exported_once():
    """Offset pagination over a DESC list shifts when a signal is inserted mid-walk.

    The second page then legitimately repeats the first page's last row. Emitting it twice
    would put the same signal on two lines of an audit file.
    """
    service, _ = service_for([])
    first = many_rows(101)
    second = [first[99], first[100], signal_row("s9999")]

    async def _paged(user, **kwargs):
        return list(first) if kwargs.get("offset", 0) == 0 else list(second)

    service.list_signals = _paged  # type: ignore[method-assign]

    export = await service.export_signal_trace(OWNER, format="json")

    ids = [item["id"] for item in export["signals"]]
    assert len(ids) == len(set(ids)), "a signal was exported twice"
    assert ids[-2:] == [first[100]["id"], "s9999"]
    assert export["row_count"] == 102


@pytest.mark.asyncio
async def test_hitting_the_row_ceiling_declares_the_export_partial():
    """A partial file that says it is partial. Silence here would break Requirement 17.1."""
    service, _ = service_for(many_rows(250))

    export = await service.export_signal_trace(OWNER, format="json", max_rows=150)

    assert export["row_count"] == 150
    assert export["truncated"] is True
    assert export["max_rows"] == 150
    _header, lines = csv_rows(
        (await service.export_signal_trace(OWNER, format="csv", max_rows=150))["content"]
    )
    assert len(lines) == 150


@pytest.mark.asyncio
async def test_an_export_that_exactly_fills_the_ceiling_is_not_reported_as_truncated():
    service, _ = service_for(many_rows(100))

    export = await service.export_signal_trace(OWNER, format="json", max_rows=100)

    assert export["row_count"] == 100
    assert export["truncated"] is False


@pytest.mark.asyncio
async def test_a_caller_cannot_raise_the_ceiling_above_the_modules_own():
    service, _ = service_for(many_rows(5))

    export = await service.export_signal_trace(OWNER, format="json", max_rows=10_000_000)

    assert export["max_rows"] == SIGNAL_TRACE_EXPORT_MAX_ROWS


def test_the_module_ceiling_is_a_whole_number_of_pages():
    assert SIGNAL_TRACE_EXPORT_MAX_ROWS % SIGNAL_TRACE_PAGE_SIZE == 0
    assert SIGNAL_TRACE_EXPORT_MAX_ROWS > SIGNAL_TRACE_PAGE_SIZE


# ══════════════════════════════════════════════════════════════════════════
# 3. THE CSV BODY
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_csv_header_is_the_declared_constant_in_order():
    """Not ``signals[0].keys()``: a header must not depend on which signal sorted first."""
    service, _ = service_for([signal_row("s1")])

    export = await service.export_signal_trace(OWNER, format="csv")
    header, lines = csv_rows(export["content"])

    assert header == [name for name, _path in SIGNAL_TRACE_EXPORT_COLUMNS]
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_the_csv_carries_every_field_requirement_17_3_names():
    service, _ = service_for([signal_row("s1")])

    export = await service.export_signal_trace(OWNER, format="csv")
    header, (line,) = csv_rows(export["content"])
    cell = dict(zip(header, line))

    assert cell["signal_id"] == "s1"
    assert cell["generated_at"] == "2024-05-01T12:00:00+00:00"
    assert cell["strategy_id"] == "strat-1"
    assert cell["symbol"] == "BTC/USDT"
    assert cell["side"] == "BUY"
    assert cell["quantity"] == "0.25"
    assert cell["order_lifecycle_state"] == "EXECUTED"
    assert cell["order_id"] == "ord-1"
    assert cell["execution_id"] == "trd-1"
    assert cell["execution_price"] == "61000.0"
    assert cell["filled_quantity"] == "0.25"
    assert cell["remaining_quantity"] == "0.0"
    assert cell["fees"] == "1.23"
    # An EXECUTED signal has no failure reason; the cell is empty, not "None".
    assert cell["failure_reason"] == ""


@pytest.mark.asyncio
async def test_a_failed_signal_carries_its_failure_reason():
    service, _ = service_for(
        [signal_row("s1", state="REJECTED", status="rejected", risk_reason="max drawdown")]
    )

    export = await service.export_signal_trace(OWNER, format="csv")
    header, (line,) = csv_rows(export["content"])

    assert dict(zip(header, line))["failure_reason"] == "max drawdown"


@pytest.mark.asyncio
async def test_an_empty_export_is_still_a_valid_file_with_a_header():
    """A zero-byte download is indistinguishable from a failed request."""
    service, _ = service_for([])

    export = await service.export_signal_trace(OWNER, format="csv")
    header, lines = csv_rows(export["content"])

    assert header == [name for name, _path in SIGNAL_TRACE_EXPORT_COLUMNS]
    assert lines == []
    assert export["row_count"] == 0


def test_a_cell_a_spreadsheet_would_run_as_a_formula_is_defused():
    """A failure reason echoed from a venue is script injection in a downloaded file."""
    item = {
        "id": "s1",
        "symbol": "BTC/USDT",
        "execution": {"failure_reason": '=HYPERLINK("http://evil","click")'},
    }

    header, (line,) = csv_rows(render_signal_trace_csv([item]))
    cell = dict(zip(header, line))

    assert cell["failure_reason"].startswith("'="), cell["failure_reason"]


def test_a_negative_number_is_not_defused_into_text():
    """Prefixing a negative pnl would stop a spreadsheet summing the column."""
    item = {"id": "s1", "execution": {"pnl": -42.0, "realized_pnl": -1.5}}

    header, (line,) = csv_rows(render_signal_trace_csv([item]))
    cell = dict(zip(header, line))

    assert cell["pnl"] == "-42.0"
    assert cell["realized_pnl"] == "-1.5"


def test_the_csv_uses_rfc_4180_line_endings():
    content = render_signal_trace_csv([{"id": "s1"}])
    assert content.endswith("\r\n")
    assert content.count("\r\n") == 2  # header + one row


# ══════════════════════════════════════════════════════════════════════════
# 4. THE JSON BODY
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_json_body_is_the_envelope_plus_the_items():
    service, _ = service_for([signal_row("s1"), signal_row("s2", generated_at="2024-05-01T11:00:00+00:00")])

    export = await service.export_signal_trace(OWNER, format="json")
    body = json.loads(export["content"])

    assert body["format"] == "json"
    assert body["row_count"] == 2
    assert body["truncated"] is False
    assert body["max_rows"] == SIGNAL_TRACE_EXPORT_MAX_ROWS
    assert body["lifecycle_state_source"] == "canonical"
    assert body["degraded"] is None
    assert [item["id"] for item in body["signals"]] == ["s1", "s2"]
    assert body["exported_at"]


@pytest.mark.asyncio
async def test_the_json_body_is_not_truncated_at_the_jsonb_item_bound():
    """Requirement 17.1, against the rendering itself.

    ``_jsonable``'s 256-item ceiling is a bound on what may enter a JSONB COLUMN. Applied
    to the export it would drop every signal after the 256th and leave a
    ``"[truncated: N items]"`` string in their place - an omission that looks like a
    formatting detail.
    """
    rows = many_rows(300)
    service, _ = service_for(rows)

    export = await service.export_signal_trace(OWNER, format="json")
    body = json.loads(export["content"])

    assert export["row_count"] == 300
    assert len(body["signals"]) == 300
    assert all(isinstance(item, dict) for item in body["signals"])
    assert "truncated:" not in export["content"]


@pytest.mark.asyncio
async def test_the_csv_body_is_not_truncated_at_the_jsonb_item_bound():
    rows = many_rows(300)
    service, _ = service_for(rows)

    export = await service.export_signal_trace(OWNER, format="csv")
    _header, lines = csv_rows(export["content"])

    assert len(lines) == 300


@pytest.mark.asyncio
async def test_the_json_items_are_the_lists_own_projection():
    """One shape reaches the frontend: the export's entry IS the list's item."""
    row = signal_row("s1")
    service, _ = service_for([row])

    export = await service.export_signal_trace(OWNER, format="json")

    assert export["signals"] == [svc.signal_trace_item(row)]


@pytest.mark.asyncio
async def test_the_json_export_is_sorted_newest_first_like_the_list():
    rows = [
        signal_row("old", generated_at="2024-05-01T10:00:00+00:00"),
        signal_row("new", generated_at="2024-05-01T14:00:00+00:00"),
        signal_row("middle", generated_at="2024-05-01T12:00:00+00:00"),
    ]
    service, _ = service_for(rows)

    export = await service.export_signal_trace(OWNER, format="json")

    assert [item["id"] for item in export["signals"]] == ["new", "middle", "old"]


# ══════════════════════════════════════════════════════════════════════════
# 5. THE FILTERS, THE REFUSALS AND OWNERSHIP
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_export_applies_the_lists_filter_semantics():
    """AND across categories, OR within one - Requirement 17.2, not re-implemented."""
    rows = [
        signal_row("btc-buy", symbol="BTC/USDT", decision="BUY"),
        signal_row("btc-sell", symbol="BTC/USDT", decision="SELL"),
        signal_row("eth-buy", symbol="ETH/USDT", decision="BUY"),
        signal_row("sol-buy", symbol="SOL/USDT", decision="BUY"),
    ]
    service, _ = service_for(rows)

    export = await service.export_signal_trace(
        OWNER, format="json", symbol=["BTC/USDT", "ETH/USDT"], side=["BUY"]
    )

    assert {item["id"] for item in export["signals"]} == {"btc-buy", "eth-buy"}
    assert export["filters_active"] is True
    assert set(export["active_filters"]) == {"symbol", "side"}


@pytest.mark.asyncio
async def test_an_unfiltered_export_says_so():
    service, _ = service_for([signal_row("s1")])

    export = await service.export_signal_trace(OWNER, format="json")

    assert export["filters_active"] is False
    assert export["active_filters"] == []


@pytest.mark.asyncio
async def test_an_unrecognised_lifecycle_state_is_refused_not_ignored():
    """Ignoring it would export MORE signals than the caller asked for."""
    service, _ = service_for([signal_row("s1")])

    with pytest.raises(OrderLifecycleRejected) as excinfo:
        await service.export_signal_trace(
            OWNER, format="csv", order_lifecycle_state=["ALMOST_FILLED"]
        )

    assert excinfo.value.http_status == 400
    assert excinfo.value.code == "ORDER_LIFECYCLE_STATE_UNRECOGNISED"


@pytest.mark.asyncio
async def test_an_unsupported_format_is_refused_not_answered_as_json():
    service, _ = service_for([signal_row("s1")])

    with pytest.raises(SignalExportRefused) as excinfo:
        await service.export_signal_trace(OWNER, format="xlsx")

    assert excinfo.value.http_status == 400
    assert excinfo.value.code == "SIGNAL_EXPORT_FORMAT_UNSUPPORTED"
    assert excinfo.value.details["supported_formats"] == list(SIGNAL_TRACE_EXPORT_FORMATS)


def test_the_format_is_case_insensitive_and_defaults_to_json():
    assert normalise_export_format("CSV") == "csv"
    assert normalise_export_format(" Json ") == "json"
    assert normalise_export_format(None) == "json"
    assert normalise_export_format("") == "json"


@pytest.mark.asyncio
async def test_a_non_owner_exports_nothing_of_the_owners():
    service, _ = service_for([signal_row("s1"), signal_row("s2")])

    export = await service.export_signal_trace(INTRUDER, format="json")

    assert export["row_count"] == 0
    assert export["signals"] == []


@pytest.mark.asyncio
async def test_a_non_owner_filtering_a_real_strategy_gets_the_missing_resource_answer():
    """Requirement 20.2: nothing in the file may reveal that the strategy exists."""
    rows = [signal_row("s1", strategy_id="strat-1")]

    service, _ = service_for(rows)
    foreign = await service.export_signal_trace(
        INTRUDER, format="csv", strategy_id="strat-1"
    )
    service, _ = service_for(rows)
    nonexistent = await service.export_signal_trace(
        INTRUDER, format="csv", strategy_id="strategy-that-does-not-exist"
    )

    assert foreign["content"] == nonexistent["content"]
    assert foreign["row_count"] == nonexistent["row_count"] == 0


@pytest.mark.asyncio
async def test_the_owner_predicate_is_on_every_page_read():
    service, client = service_for(many_rows(150))

    await service.export_signal_trace(OWNER, format="csv")

    listing = [q for q in client.queries if q.predicates]
    assert listing
    for query in listing:
        assert ("eq", "user_id", OWNER["id"]) in query.predicates


@pytest.mark.asyncio
async def test_no_credential_reaches_the_exported_file():
    """Requirement 20.3. The exempt internal account reference is present; nothing else."""
    row = minted_signal().to_row()
    service, _ = service_for([row])

    csv_export = await service.export_signal_trace(OWNER, format="csv")
    json_export = await service.export_signal_trace(OWNER, format="json")

    assert "s3cr3t-shhh" not in csv_export["content"]
    assert "s3cr3t-shhh" not in json_export["content"]
    # The platform's internal Exchange_Account reference, exempted by name in 20.3.
    assert "acct-dddd" in csv_export["content"]


@pytest.mark.asyncio
async def test_without_005b_the_export_still_answers_and_names_the_migration():
    service, _ = service_for(
        [signal_row("s1", state=None, status="executed")], lifecycle_columns=False
    )

    export = await service.export_signal_trace(OWNER, format="json")
    body = json.loads(export["content"])

    assert export["row_count"] == 1
    assert body["lifecycle_state_source"] == "legacy_status_map"
    assert body["degraded"]["migration"] == svc.SIGNAL_LIFECYCLE_MIGRATION


# ══════════════════════════════════════════════════════════════════════════
# 6. THE RESPONSE, THROUGH THE MOUNTED APP
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def client(monkeypatch):
    """A ``TestClient`` on the real app, authenticated, with the fake service behind it.

    The rate limiter is suspended for the same reason other suites suspend it: a 429
    earned by another file's requests is not a statement about this one. The decorator
    itself stays on the endpoint.
    """
    from fastapi.testclient import TestClient

    from backend_app.core.dependencies import get_current_user, get_request_supabase
    from backend_app.main import app
    import backend_app.routers.signal_trace as router_module

    def _install(rows, **kwargs):
        service, fake = service_for(rows, **kwargs)

        async def _get_service():
            return service

        monkeypatch.setattr(router_module, "get_signal_service", _get_service)
        return service, fake

    limiter = getattr(app.state, "limiter", None)
    was_enabled = getattr(limiter, "enabled", None)
    if limiter is not None:
        limiter.enabled = False

    app.dependency_overrides[get_current_user] = lambda: OWNER
    app.dependency_overrides[get_request_supabase] = lambda: None
    try:
        test_client = TestClient(app)
        test_client.install = _install  # type: ignore[attr-defined]
        yield test_client
    finally:
        app.dependency_overrides.clear()
        if limiter is not None and was_enabled is not None:
            limiter.enabled = was_enabled


def test_the_csv_response_is_a_named_attachment_with_the_export_headers(client):
    client.install([signal_row("s1"), signal_row("s2", generated_at="2024-05-01T11:00:00+00:00")])

    response = client.get("/api/signal-trace/signals/export", params={"format": "csv"})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "charset=utf-8" in response.headers["content-type"]
    assert response.headers["content-disposition"] == (
        'attachment; filename="signal_trace.csv"'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-export-row-count"] == "2"
    assert response.headers["x-export-truncated"] == "false"
    assert response.headers["x-export-max-rows"] == str(SIGNAL_TRACE_EXPORT_MAX_ROWS)

    header, lines = csv_rows(response.text)
    assert header == [name for name, _path in SIGNAL_TRACE_EXPORT_COLUMNS]
    assert [line[0] for line in lines] == ["s1", "s2"]


def test_the_json_response_is_a_named_attachment_the_page_can_parse(client):
    """``SignalTrace.jsx`` calls ``res.json()`` on this response and saves it as a blob."""
    client.install([signal_row("s1")])

    response = client.get("/api/signal-trace/signals/export", params={"format": "json"})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == (
        'attachment; filename="signal_trace.json"'
    )
    body = response.json()
    assert body["row_count"] == 1
    assert body["signals"][0]["id"] == "s1"


def test_the_export_applies_a_filter_the_page_sends(client):
    """The exact query ``SignalTrace.jsx``'s export button builds from its filter form."""
    client.install(
        [
            signal_row("btc", symbol="BTC/USDT"),
            signal_row("eth", symbol="ETH/USDT", generated_at="2024-05-01T11:00:00+00:00"),
        ]
    )

    response = client.get(
        "/api/signal-trace/signals/export",
        params={"format": "csv", "symbol": "ETH/USDT", "strategy_id": "strat-1"},
    )

    assert response.status_code == 200, response.text
    _header, lines = csv_rows(response.text)
    assert [line[0] for line in lines] == ["eth"]


def test_an_empty_filter_value_from_a_cleared_form_field_is_not_a_filter(client):
    """``?symbol=`` is how the page sends a cleared field; it must not export nothing."""
    client.install([signal_row("s1")])

    response = client.get(
        "/api/signal-trace/signals/export",
        params={"format": "csv", "symbol": "", "status": "", "search": ""},
    )

    assert response.status_code == 200, response.text
    _header, lines = csv_rows(response.text)
    assert len(lines) == 1


def test_an_unsupported_format_is_a_400_naming_the_supported_ones(client):
    client.install([signal_row("s1")])

    response = client.get("/api/signal-trace/signals/export", params={"format": "xlsx"})

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["error"] == "SIGNAL_EXPORT_FORMAT_UNSUPPORTED"
    assert detail["supported_formats"] == list(SIGNAL_TRACE_EXPORT_FORMATS)


def test_an_unrecognised_lifecycle_state_is_a_400_through_the_route(client):
    client.install([signal_row("s1")])

    response = client.get(
        "/api/signal-trace/signals/export",
        params={"format": "csv", "order_lifecycle_state": "ALMOST_FILLED"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["error"] == "ORDER_LIFECYCLE_STATE_UNRECOGNISED"


def test_a_repeated_filter_parameter_is_ored_not_overwritten(client):
    """``?symbol=A&symbol=B`` - the reason every filter is declared as a list."""
    client.install(
        [
            signal_row("btc", symbol="BTC/USDT"),
            signal_row("eth", symbol="ETH/USDT", generated_at="2024-05-01T11:00:00+00:00"),
            signal_row("sol", symbol="SOL/USDT", generated_at="2024-05-01T10:00:00+00:00"),
        ]
    )

    response = client.get(
        "/api/signal-trace/signals/export?format=csv&symbol=BTC/USDT&symbol=SOL/USDT"
    )

    assert response.status_code == 200, response.text
    _header, lines = csv_rows(response.text)
    assert [line[0] for line in lines] == ["btc", "sol"]
