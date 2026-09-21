import logging

from fastapi import APIRouter, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["Observability"])
logger = logging.getLogger(__name__)


def _collector_exposition() -> str:
    """``backend/metrics.py``'s collector rendered for this endpoint.

    THE ONE COLLECTOR (marketplace-subscriptions-paper-trading task 33.5, Requirement 26.6)
    ---------------------------------------------------------------------------------------
    ``design.md`` says the Requirement 26.6 metrics are "emitted through the existing
    ``routers/metrics.py`` collector". They are declared on ``MetricsCollector``, which owns the
    strategy-builder and paper-feed vocabularies too, and this function is what puts that
    collector's exposition on the same ``/metrics`` response as the ``prometheus_client``
    default registry. There is ONE collector and ONE scrape endpoint; a second registry with a
    second endpoint is what an operator would have to remember to scrape, and would not.

    Raises:
        Anything the collector's rendering raises. Deliberately NOT swallowed here: the caller's
        handler turns it into a 500, which is this endpoint's existing contract - a broken
        metrics endpoint must not answer 200 with a payload that silently omits half the
        platform's series. That is the opposite of the ``never_fails`` rule on the RECORDING
        path, and for the opposite reason: a recording call sits inside the act it measures and
        must not fail it, while this call IS the act.
    """
    from backend_app.backend.metrics import metrics_collector

    return metrics_collector.get_prometheus_metrics()


@router.get("/metrics")
def get_metrics():
    """
    Expose Prometheus metrics for scraping.
    Returns HTTP 500 if metrics generation fails — never fabricates a fake
    healthy payload, because a broken metrics endpoint must not silently
    lie to operators about system state.
    """
    try:
        content = generate_latest()
        collector_text = _collector_exposition()
        if collector_text:
            content = content + b"\n" + collector_text.encode("utf-8") + b"\n"
        return Response(
            content=content,
            media_type=CONTENT_TYPE_LATEST,
        )
    except Exception as exc:
        logger.error(f"Prometheus metrics generation failed: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Metrics generation failed. Check server logs for details.",
        )

