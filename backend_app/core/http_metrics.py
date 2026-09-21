"""HTTP latency and error-rate metrics for the routes this specification introduced.

Feature: marketplace-subscriptions-paper-trading, task 33.5.
Requirements 26.6 ("latency and error-rate metrics for each API endpoint ... introduced by this
specification"), 27.6 ("SHALL measure and record the observed latency ... rather than asserting
them without measurement"), 26.4 (nothing caller-supplied reaches a label).

WHY A MIDDLEWARE AND NOT A DECORATOR
------------------------------------
The alternative was a decorator on each of the thirty-odd handlers in ``routers/library.py`` and
``routers/paper_trading.py``. Three reasons it is not that:

1. The six pre-existing ``/api/paper/*`` handlers are pinned by ``tests/regression`` - their
   recorded ``decorators`` list is part of the Requirement 25.7 baseline. Adding a decorator to
   one of them is a baseline change, and instrumentation is not a reason to take one.
2. A decorator has to be remembered. A route added later would be silently unmeasured, and
   "unmeasured" and "no traffic" look identical on a dashboard.
3. A handler-level timer measures the handler. Requirement 26.6 asks about the ENDPOINT, which
   includes the dependency resolution, the body parse and the response serialisation - the parts
   a caller waits for and a handler decorator does not see.

WHAT REACHES A LABEL
--------------------
The route TEMPLATE that FastAPI matched (``/api/paper/sessions/{session_id}``), never the
resolved path. A resolved path carries a session or listing identifier, and that would be both
an unbounded series set and another user's identifier in the exposition (Requirement 26.4). A
request that matched no route is filed under :data:`UNMATCHED_ROUTE` rather than under its own
path, for the same two reasons.

WHAT THIS MIDDLEWARE CANNOT BREAK
---------------------------------
The response. The recording happens in a ``finally`` after the response object already exists,
through :func:`_record`, which reaches the collector lazily and guarded - the same shape
``market_data_contract._metrics`` uses, and for the same reason. A collector that cannot be
imported, or a recording call that raises, costs a sample and nothing else.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

#: URL prefix -> the metric family it belongs to. ``/api/library`` is where this spec's
#: Marketplace endpoints live (``main.py`` mounts ``routers/library.py`` there) and ``/api/paper``
#: is where the Paper_Trading_API's do. Longest prefix wins, so a future ``/api/library/admin``
#: family could be split out without touching the matcher.
METRIC_DOMAIN_BY_PREFIX: Tuple[Tuple[str, str], ...] = (
    ("/api/library", "marketplace"),
    ("/api/paper", "paper"),
)

#: The ``route`` label for a request that matched no route at all. A 404's path is
#: caller-controlled, so it is NOT used as a label value.
UNMATCHED_ROUTE = "unmatched"


def metric_domain(path: str) -> Optional[str]:
    """``'/api/paper/sessions'`` -> ``'paper'``; an unrelated path -> ``None``.

    ``None`` means "not a route this specification introduced or modified", and such a request is
    not recorded here at all - ``main.py``'s pre-existing ``PrometheusMiddleware`` already counts
    every request on the platform, and duplicating that for unrelated routes would be a second
    figure for the same thing.
    """
    text = str(path or "")
    best: Optional[str] = None
    best_length = -1
    for prefix, domain in METRIC_DOMAIN_BY_PREFIX:
        if (text == prefix or text.startswith(prefix + "/")) and len(prefix) > best_length:
            best, best_length = domain, len(prefix)
    return best


def route_template(request: Any) -> str:
    """The matched route's template, or :data:`UNMATCHED_ROUTE`.

    Starlette puts the matched ``APIRoute`` on the request scope during routing, so this is
    available after the response has been produced and is the templated form - which is what
    keeps an identifier out of the label.
    """
    scope = getattr(request, "scope", None) or {}
    route = scope.get("route") if hasattr(scope, "get") else None
    template = getattr(route, "path", None)
    if not template:
        return UNMATCHED_ROUTE
    return str(template)


def _metrics() -> Any:
    """``backend/metrics.py``'s collector, or ``None``. Lazy and guarded.

    The same shape ``market_data_contract._metrics`` uses, for
    the same two reasons. **Lazy**, so a test can install a fresh collector on
    ``metrics.metrics_collector`` and this module picks it up. **Guarded**, because
    instrumentation must never be what breaks a response: if the collector cannot be imported,
    the caller still gets its answer.
    """
    try:
        from backend_app.backend.metrics import metrics_collector

        return metrics_collector
    except Exception:  # noqa: BLE001 - instrumentation never breaks its caller
        return None


def _record(domain: str, route: str, status_code: Any, duration_ms: float) -> None:
    """One measurement onto the one collector. Never raises."""
    collector = _metrics()
    if collector is None:
        return
    collector.record_http_request(domain, route, status_code, duration_ms)


class MarketplacePaperHttpMetricsMiddleware(BaseHTTPMiddleware):
    """Records ``marketplace.http.*`` / ``paper.http.*`` for every request to those surfaces.

    A request outside both prefixes passes through untouched and unmeasured; see
    :func:`metric_domain`.

    An exception from the handler is recorded as a ``5xx`` and then RE-RAISED unchanged, so the
    registered structured-error handlers still decide the response body. Swallowing it to keep
    the measurement tidy would turn a failure into a 200, which is the one thing instrumentation
    must never do.
    """

    async def dispatch(self, request: Any, call_next: Any) -> Any:
        domain = metric_domain(getattr(getattr(request, "url", None), "path", ""))
        if domain is None:
            return await call_next(request)

        started = time.perf_counter()
        status_code: Any = 500
        try:
            response = await call_next(request)
            status_code = getattr(response, "status_code", 500)
            return response
        finally:
            _record(
                domain,
                route_template(request),
                status_code,
                (time.perf_counter() - started) * 1000.0,
            )


__all__ = [
    "METRIC_DOMAIN_BY_PREFIX",
    "MarketplacePaperHttpMetricsMiddleware",
    "UNMATCHED_ROUTE",
    "metric_domain",
    "route_template",
]
