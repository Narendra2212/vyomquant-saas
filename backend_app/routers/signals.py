"""
routers/signals.py — audit retrieval for a stored signal record.

WHAT THIS ROUTER IS, AFTER TASK 13.19
-------------------------------------
One endpoint: ``POST /api/signals/{signal_id}/replay``.

It had four. Three of them — ``GET /`` (list), ``GET /{signal_id}`` (detail) and
``GET /export`` — were superseded by ``routers/signal_trace.py``, mounted at
``/api/signal-trace``, which serves ``GET /signals``, ``GET /signals/{id}``,
``GET /signals/export`` and five more off the signal-trace read model. Nothing in
``algo22-terminal/`` called ``/api/signals`` at all; the Signal_Trace page calls
``/api/signal-trace/signals``. So the three were not a second opinion, they were a
second implementation nobody used, and they were all broken:

  * ``list_signal_traces`` projected twenty columns off ``execution_records``, of which
    **twelve are not columns of that table** — ``id``, ``indicators``, ``ml_inputs``,
    ``ml_outputs``, ``confidence``, ``risk_verdict``, ``filled_quantity``, ``quantity``,
    ``latency_ms``, ``pnl``, ``failure_reason``, ``timeframe``. Production answers
    ``42703`` for each (``user_id`` and ``symbol`` on the same table answer OK). The
    handler's blanket ``except`` turned that into ``503 SIGNAL_FETCH_FAILED``, so the
    endpoint refused every caller regardless of stored data. It also defaulted the
    missing names to literals — ``confidence`` to ``0.88``, ``latency_ms`` to ``42.5``,
    ``indicators`` to a fixed RSI/SMA/EMA dict — which is the fabrication
    ``bugfix.md`` §Bug condition exists to forbid.
  * ``get_signal_trace`` read the same twelve names off a ``select("*")`` and got
    ``None`` for every one of them.
  * ``export_signal_traces`` called the list, and was unreachable anyway: it was
    declared AFTER ``GET /{signal_id}``, which matches any single segment, so
    ``GET /api/signals/export`` resolved to the detail handler with
    ``signal_id="export"``. ``signal_trace.py`` declares its own
    ``GET /signals/export`` BEFORE ``GET /signals/{signal_id}``, so the surviving
    export is correctly ordered.

``POST /{signal_id}/replay`` is the one capability ``signal_trace.py`` has no equivalent
for, so it stays — at its original path, in its original module, under its original
name, because ``tests/test_validation_sweep.py`` carries it in its route register and
``tests/test_strategy_analysis_endpoint_accuracy.py`` drives it by import.

It now reads ``public.signals`` and nothing else. That table is the one with the columns
this payload needs — ``decision``, ``indicators``, ``market_info``, ``ml_info``,
``generated_at`` — which is what the removed ``# Try to query from signals table (proper
schema)`` comment was already saying. The old ``execution_records`` fallback, the
``decision or side`` shim and the ``created_at or generated_at`` shim existed only to
straddle the two tables; ``signals`` has no ``side`` and no ``created_at`` (production:
``42703`` for both), so every one of those alternatives was dead on the surviving path.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from backend_app.core.dependencies import create_request_supabase_async, get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


async def _sb(user: dict):
    return await create_request_supabase_async(user.get("access_token"))


@router.post("/{signal_id}/replay")
async def replay_signal_trace(
    signal_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Retrieve the stored signal trace record for audit/review purposes.

    IMPORTANT: This endpoint is labelled 'replay' in the URL but does NOT
    perform an independent re-execution of the signal DAG against historical
    inputs. It returns the stored signal record fields for audit purposes.

    GENUINE SIGNAL REPLAY LIMITATION:
    Implementing true DAG re-execution would require:
    1. Storing complete indicator/market inputs at signal generation time
    2. Storing the complete strategy DAG state at that time
    3. Historical market data access for the exact signal timestamp
    4. DAG execution engine integration for re-running logic
    5. Comparison framework for fresh output vs stored decision

    This architectural change would require substantial modifications to the
    signal generation pipeline, database schema, and execution infrastructure.

    This endpoint provides stored signal data for compliance and audit review,
    not independent verification of signal generation logic.

    For signal verification, use the research report analysis which includes
    signal consistency metrics across historical data.
    """
    try:
        sb = await _sb(user)
        if sb is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Signal trace '{signal_id}' not found.")

        # ``public.signals`` is the signal record's table, and the only one read here.
        # The ``execution_records`` fallback this replaced could not carry the payload
        # below: that table is an order-execution log with no ``indicators``,
        # ``market_info`` or ``ml_info``, so the fallback's rows answered this endpoint
        # with three nulls and a decision read off ``side``.
        res = await sb.table("signals").select("*").eq("id", signal_id).eq("user_id", user["id"]).execute()

        if not res.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Signal trace '{signal_id}' not found.")

        rec = res.data[0]
        # No default. The previous expression ended ``or "BUY"``, so a row whose
        # ``decision`` was NULL was reported to an auditor as a buy — a fabricated fact
        # in a compliance payload. An absent decision is reported as absent.
        stored_decision = rec["decision"].upper() if rec.get("decision") else None

        return {
            "status": "audit_retrieval",
            "signal_id": signal_id,
            "stored_decision": stored_decision,
            "replay_implemented": False,
            "replay_note": (
                "Independent DAG re-execution against stored inputs is not implemented. "
                "This endpoint provides audit retrieval of stored signal records for compliance purposes. "
                "True signal replay requires architectural changes to signal generation pipeline, "
                "database schema for storing complete inputs, and DAG execution engine integration. "
                "For signal consistency analysis, use the research report endpoint with appropriate analysis configuration."
            ),
            "audit_functionality": "stored_record_retrieval",
            "retrieval_timestamp": datetime.now(timezone.utc).isoformat(),
            "execution_metadata": {
                # ``signals`` records generation time as ``generated_at``; it has no
                # ``created_at`` column at all (production: 42703).
                "timestamp": rec.get("generated_at"),
                "symbol": rec.get("symbol"),
                "strategy_id": rec.get("strategy_id"),
                "user_id": rec.get("user_id"),
                "indicators": rec.get("indicators"),
                "market_info": rec.get("market_info"),
                "ml_info": rec.get("ml_info")
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving signal trace {signal_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": "SIGNAL_RETRIEVAL_FAILED", "message": str(e)}
        )
