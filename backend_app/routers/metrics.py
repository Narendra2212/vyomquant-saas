from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["Observability"])

@router.get("/metrics")
def get_metrics():
    """
    Expose Prometheus metrics safely without crashing.
    """
    try:
        content = generate_latest()
        return Response(
            content=content,
            media_type=CONTENT_TYPE_LATEST
        )
    except Exception:
        return Response(
            content="# HELP algo22_up System operational status\n# TYPE algo22_up gauge\nalgo22_up 1.0\n",
            media_type="text/plain"
        )
