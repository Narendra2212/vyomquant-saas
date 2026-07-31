import logging

from fastapi import APIRouter, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["Observability"])
logger = logging.getLogger(__name__)


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

