"""
routers/admin.py — God Mode / Admin endpoints.

FIXES:
  ADMIN-1: Uses get_supabase() singleton
  ADMIN-2: freeze prefix uses f'{user_id}_' (with underscore) to avoid matching user_id prefixes
  ADMIN-3: list_users() requires search >= 2 chars to prevent full enumeration
  ADMIN-4: /health actually pings QuestDB instead of returning hardcoded 'connected'
"""

import asyncio
import inspect
import logging

import json
import time
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from backend_app.core.dependencies import (require_aal2, get_admin_user, get_operator_user, get_alert,
                                           get_fleet, get_service_supabase,
                                           get_telemetry, get_ws_manager,
                                           invalidate_profile_cache)
from backend_app.core.background_tasks import fire_and_forget_task
from backend_app.core.models import (GlobalKillRequest,
                                     PlatformReinitializeRequest,
                                     UserStatusRequest)
from supabase import Client as SupabaseClient

router = APIRouter()
logger = logging.getLogger("AdminRouter")


async def _log_admin_audit_event(
    action: str,
    target: str,
    operator: dict,
    reason: str,
    details: dict = None
):
    audit_record = {
        "audit_id": f"ADMIN-AUDIT-{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}",
        "action": action,
        "target": target,
        "operator_id": operator.get("id"),
        "operator_email": operator.get("email", "unknown"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "details": details or {},
    }

    logger.info(
        f"OPERATOR SECURITY AUDIT: {action} on {target} by {audit_record['operator_email']} - Reason: '{reason}'",
        extra={"audit": audit_record}
    )

    try:
        from backend_app.core.cache.redis_manager import redis_manager
        redis_client = await redis_manager.get_client()
        if redis_client:
            await redis_client.rpush("audit:admin_actions", json.dumps(audit_record))
    except Exception as e:
        logger.warning(f"Failed to persist admin audit record to Redis: {e}")


@router.get("/health")
async def system_health(
    admin: dict = Depends(get_admin_user),
    fleet=Depends(get_fleet),
    telemetry=Depends(get_telemetry),
):
    fleet_status = fleet.get_status()

    # ADMIN-4: Actually ping QuestDB
    questdb_status = "connected"
    try:
        result = await telemetry.execute_query("SELECT 1;")
        questdb_status = "connected" if result else "unreachable"
    except Exception:
        questdb_status = "unreachable"

    pods_result = await telemetry.execute_query(
        "SELECT pod_name, cpu_pct, ram_pct, latency_ms, status FROM pod_status ORDER BY pod_name;"
    )
    pods = []
    if pods_result and pods_result.get("dataset"):
        cols = [c["name"] for c in pods_result["columns"]]
        pods = [dict(zip(cols, row)) for row in pods_result["dataset"]]

    return {"fleet": fleet_status, "pods": pods, "questdb": questdb_status}


@router.get("/metrics")
async def system_metrics(
    admin: dict = Depends(get_admin_user),
    telemetry=Depends(get_telemetry),
):
    result = await telemetry.execute_query(
        "SELECT timestamp, cpu, ram, lat FROM system_metrics "
        "WHERE timestamp > dateadd('h', -24, now()) ORDER BY timestamp ASC;"
    )
    if result and result.get("dataset"):
        cols = [c["name"] for c in result["columns"]]
        return [dict(zip(cols, row)) for row in result["dataset"]]
    return []


@router.get("/users")
async def list_users(
    search: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_admin_user),
    supabase: SupabaseClient = Depends(get_service_supabase),
):
    # SEC-08: runs under the service-role client (after get_admin_user), otherwise RLS
    # restricts the listing to the admin's own row.
    # ADMIN-3: Require at least 2 chars to prevent full enumeration with empty string
    if search and len(search) < 2:
        raise HTTPException(400, "Search query must be at least 2 characters.")

    if not supabase:
        return [{"id": admin["id"], "email": admin.get("email"), "subscription_tier": "admin", "is_frozen": False}]

    # `volume_usd` was in this projection and `public.profiles` does not have it
    # (production answers 42703; `subscription_tier` on the same table answers OK). The
    # chain is not wrapped, so this endpoint 500ed unconditionally and had never once
    # answered. Removed rather than declared: no migration declares the column, nothing
    # computes a trading volume anywhere in the tree, and no caller reads it - declaring
    # an empty column would have put a figure on an admin screen that nothing produces.
    # The no-supabase branch above already returns a row without it.
    query = (
        supabase.table("profiles")
        .select("id, username, email, subscription_tier, is_frozen, created_at")
        .limit(limit)
    )
    if search:
        query = query.ilike("username", f"%{search}%")
    resp = await query.order("created_at", desc=True).execute()
    return resp.data


@router.post("/users/{user_id}/status")
async def set_user_status(
    user_id: str,
    body: UserStatusRequest,
    admin: dict = Depends(get_operator_user),
    _mfa: dict = Depends(require_aal2),  # SEC-15: freeze/unfreeze any account needs MFA
    fleet=Depends(get_fleet),
    alert=Depends(get_alert),
    supabase: SupabaseClient = Depends(get_service_supabase),
):
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(400, "Reason for user status change must not be empty.")

    is_frozen = body.status == "frozen"

    if is_frozen:
        # ADMIN-2: Use exact prefix 'user_id_' to avoid matching other user IDs that share a prefix
        exact_prefix = f"{user_id}_"
        fleet_ids = [bid for bid in fleet._active_fleet if bid.startswith(exact_prefix)]
        for bot_id in fleet_ids:
            parts = bot_id.split("_", 1)
            if len(parts) == 2:
                await fleet.stop_bot(parts[0], parts[1])

    # SEC-08: service-role client + verify the row was actually changed. Under the
    # operator's own RLS identity this updated 0 rows and still reported success.
    if supabase is None:
        raise HTTPException(503, "Platform database is unavailable; user status not changed.")
    update_res = supabase.table("profiles").update({"is_frozen": is_frozen}).eq(
        "id", user_id
    ).execute()
    if inspect.isawaitable(update_res):
        update_res = await update_res
    if not getattr(update_res, "data", None):
        raise HTTPException(404, "User not found; no status change was applied.")
    await invalidate_profile_cache(user_id)
    action = "FROZEN" if is_frozen else "RESTORED"

    # Immutable Audit Log
    await _log_admin_audit_event(
        action=f"USER_{action}",
        target=user_id,
        operator=admin,
        reason=reason,
        details={"status": body.status}
    )

    fire_and_forget_task(
        alert.send_info(f"Operator action: User {user_id} {action} | Reason: {reason}"),
        name=f"admin_alert_user_status_{user_id}"
    )
    return {"status": "ok", "user_id": user_id, "is_frozen": is_frozen, "reason": reason}


@router.post("/kill-all")
async def global_kill_switch(
    body: GlobalKillRequest,
    admin: dict = Depends(get_operator_user),
    _mfa: dict = Depends(require_aal2),  # SEC-15: platform-wide halt needs MFA
    fleet=Depends(get_fleet),
    alert=Depends(get_alert),
    ws_mgr=Depends(get_ws_manager),
):
    if body.confirm_code != "HALT":
        raise HTTPException(400, "Invalid confirmation code. Must be 'HALT'.")

    reason = body.reason.strip()
    if not reason:
        raise HTTPException(400, "Reason for global kill switch must not be empty.")

    try:
        # ═══════════════════════════════════════════════════════════════════
        # KILL-03: ENGAGE THE AUTHORITATIVE SWITCH *FIRST*
        # ═══════════════════════════════════════════════════════════════════
        # This endpoint used to call `fleet.shutdown_all()` and nothing else.
        # `fleet_manager.shutdown_all` (fleet_manager.py:311-325) iterates a snapshot of
        # the IN-PROCESS `_active_fleet` dict and calls `stop_bot` on each member. Its
        # docstring says "Global kill switch". It is not one:
        #
        #   * bots running in another pod or worker process are untouched;
        #   * nothing durable records that trading is stopped, so a restarted worker, a
        #     newly scheduled bot or a second replica resumes immediately;
        #   * the endpoint never referenced `GlobalKillSwitch`, so the one mechanism that
        #     IS read on the live order path was left unengaged.
        #
        # Measured: zero references to `GlobalKillSwitch`, `get_global_kill_switch` or the
        # Redis key anywhere in this file before this change.
        #
        # ORDER IS LOAD-BEARING. The authoritative state is written BEFORE the local
        # fleet is torn down. If `shutdown_all()` raised partway through (it iterates and
        # awaits per bot), the previous code left some bots running and nothing recorded
        # — and this handler's `except` turns that into a 500, so the operator is told the
        # halt failed while some bots keep trading. Engaging the switch first means every
        # order path is refused even if the teardown then fails: `activate()` latches
        # in-process immediately and writes Redis for the other processes.
        #
        # `activate()` also registers an independent HOLD keyed on this operator, so a
        # later reset cannot clear this halt without acknowledging it (KILL-02).
        from backend_app.core.global_safety import get_global_kill_switch

        kill_switch = get_global_kill_switch()
        await kill_switch.activate(
            reason=reason,
            triggered_by=f"operator:{admin.get('email') or admin.get('id') or 'unknown'}",
            operator_id=admin.get("id"),
            operator_email=admin.get("email"),
            # The dependency that established this caller's authority. `get_operator_user`
            # requires `app_metadata.role == "operator"` exactly — `admin` and `support`
            # are refused. Recorded so the trail distinguishes an authorized halt from a
            # claimed one, which is the KILL-02 defect in its activation half.
            authorization_source="fastapi:get_operator_user",
        )

        # Still done, and still useful: stopping the local fleet promptly frees resources
        # and halts this pod's bots without waiting for each to notice the switch on its
        # next order attempt. It is no longer the ONLY effect.
        await fleet.shutdown_all()

        # Immutable Audit Log
        await _log_admin_audit_event(
            action="GLOBAL_KILL_SWITCH",
            target="PLATFORM",
            operator=admin,
            reason=reason,
            details={"confirm_code": body.confirm_code}
        )

        fire_and_forget_task(
            alert.send_critical(
                "GLOBAL KILL SWITCH",
                f"Platform halted by operator {admin.get('email', 'unknown')}. Reason: {reason}",
            ),
            name="admin_alert_global_kill"
        )
        # The response now reports the AUTHORITATIVE state rather than asserting an
        # outcome. `get_status()` re-reads the switch and the hold set, so an operator
        # sees what every worker will see and what a reset would have to acknowledge —
        # instead of a fixed "HALTED" string that was true of this pod only.
        status = await kill_switch.get_status()
        return {
            "status": "HALTED",
            "message": (
                "Global kill switch ENGAGED. New order submissions are refused at the "
                "venue boundary in every process. Resting orders and open positions are "
                "NOT closed — this platform's emergency policy is to refuse new orders."
            ),
            "reason": reason,
            "kill_switch_active": status["kill_switch_active"],
            "holds": status["holds"],
            "holds_readable": status["holds_readable"],
        }
    except Exception as e:
        logger.critical(f"Global kill switch failed: {e}")
        raise HTTPException(500, f"Kill switch error: {e}")


@router.get("/kill-switch")
async def kill_switch_status(
    admin: dict = Depends(get_admin_user),
):
    """The authoritative halt state, and what a reset would have to acknowledge.

    KILL-03, the observability half. The brief for this control requires "a way to verify
    that every execution worker observes the halt", and before this endpoint there was no
    way to read the global switch over HTTP at all — `/api/admin/fleet-status` reports
    this pod's in-process `_active_fleet`, which is the very thing that made
    `/kill-all` look like it worked.

    WHAT THIS DOES AND DOES NOT PROVE. It returns the authoritative state as read from the
    shared backend, plus the registered holds. Because every worker reads that same
    backend on its own order path (`execution_engine.py:770`) and again at the venue
    boundary (`exchange_executor.place_order`), agreement here is agreement everywhere —
    the state is not replicated per worker, so there is nothing to diverge. What it cannot
    do is prove a given worker process is alive and looping; that is what
    `/api/admin/fleet-status` and the heartbeat surfaces are for.

    `holds_readable: false` means the hold set could not be read. Reported rather than
    rendered as an empty list, because "no holds" and "I do not know" are precisely the
    two facts KILL-01 conflated.

    Read-only, and deliberately `get_admin_user` rather than `get_operator_user`: seeing
    whether the platform is halted is a strictly weaker capability than halting it, and
    `admin`/`support` need it to answer "why is nothing trading".
    """
    from backend_app.core.global_safety import get_global_kill_switch

    status = await get_global_kill_switch().get_status()
    return {
        "kill_switch_active": status["kill_switch_active"],
        "local_latch_active": status["local_latch_active"],
        "redis_failure_at": status["redis_failure_at"],
        "probe_interval_seconds": status["probe_interval_seconds"],
        "holds": status["holds"],
        "holds_readable": status["holds_readable"],
        "enforcement": {
            "gateway": "backend_app/core/execution_engine.py::execute_with_idempotency",
            "venue_boundary": (
                "backend_app/backend/exchange_executor.py::place_order "
                "(enforce_kill_switch, immediately before create_order)"
            ),
            "policy": "refuse new orders; resting orders and open positions untouched",
            "fail_closed": True,
        },
    }


@router.post("/reinitialize")
async def reinitialize_platform(
    body: PlatformReinitializeRequest,
    admin: dict = Depends(get_operator_user),
    _mfa: dict = Depends(require_aal2),  # SEC-15: clearing a platform-wide halt needs MFA
    alert=Depends(get_alert),
):
    """ADM-01. Clear the global kill switch — the real "reinitialize platform" action.

    THE DEFECT
        This endpoint sent an info alert and unconditionally returned
        ``{"status": "ACTIVE", "message": "Platform reinitialized. Bots can now be
        deployed."}`` — whether or not the platform was ever halted, whatever the halt's
        cause, and with no interaction with `GlobalKillSwitch` at all (the same
        `get_global_kill_switch()` singleton `/kill-all` and `/kill-switch` use). An
        operator calling this after a real halt would be told bots can deploy while the
        authoritative switch — the thing every order path actually reads
        (`execution_engine.py`, `exchange_executor.place_order`) — stayed engaged, or,
        worse, could clear a halt nobody had reviewed, since nothing here ever reached
        `deactivate()`'s hold-acknowledgment check.

    THE FIX
        Route this directly to `GlobalKillSwitch.deactivate()`, the same mechanism
        `/kill-all` engages. `deactivate()` itself enforces everything a reset needs: a
        stated reason, an authenticated operator, and every currently-registered hold
        named in `acknowledge_holds` — see `core/global_safety.py`. A
        `KillSwitchResetRefused` is surfaced as 409 with the refusal code and detail
        rather than swallowed into a false "reinitialized".

        `confirm_code` mirrors `/kill-all`'s guard against a one-click accidental call to
        the dangerous direction; `get_operator_user` (not `get_admin_user`) matches
        `/kill-all`'s requirement that the dangerous direction needs the stronger role.
    """
    if body.confirm_code != "REINITIALIZE":
        raise HTTPException(400, "Invalid confirmation code. Must be 'REINITIALIZE'.")

    from backend_app.core.global_safety import (KillSwitchResetRefused,
                                                 get_global_kill_switch)

    kill_switch = get_global_kill_switch()
    try:
        await kill_switch.deactivate(
            reason=body.reason,
            operator_id=admin.get("id"),
            operator_email=admin.get("email"),
            authorization_source="fastapi:get_operator_user",
            acknowledge_holds=body.acknowledge_holds,
        )
    except KillSwitchResetRefused as e:
        raise HTTPException(
            409,
            {"error": "kill_switch_reset_refused", "code": e.code, "detail": e.detail},
        )
    except RuntimeError as e:
        logger.critical(f"Kill switch reset authorized but failed to apply: {e}")
        raise HTTPException(500, f"Reinitialize failed: {e}")

    await _log_admin_audit_event(
        action="PLATFORM_REINITIALIZE",
        target="PLATFORM",
        operator=admin,
        reason=body.reason,
        details={"acknowledge_holds": body.acknowledge_holds},
    )

    fire_and_forget_task(
        alert.send_info(
            f"Platform reinitialized by operator {admin.get('email', 'unknown')}. "
            f"Reason: {body.reason}"
        ),
        name="admin_alert_reinitialize"
    )

    status = await kill_switch.get_status()
    return {
        "status": "ACTIVE",
        "message": "Global kill switch cleared. New order submissions are allowed again.",
        "reason": body.reason,
        "kill_switch_active": status["kill_switch_active"],
        "holds": status["holds"],
        "holds_readable": status["holds_readable"],
    }


@router.get("/fleet-status")
async def fleet_status(
    admin: dict = Depends(get_admin_user),
    fleet=Depends(get_fleet),
):
    return fleet.get_status()


@router.get("/paper/sessions/{session_id}/reconcile")
async def reconcile_paper_session(
    session_id: str,
    user_id: str = Query(..., description="The id of the Paper_Session's owner."),
    admin: dict = Depends(get_admin_user),
):
    """PAPER-05's repair half: compare one Paper_Session's live rows to its replayed log.

    ``paper_simulator.apply_fill`` applies a fill through seven separate, deliberately
    resumable PostgREST writes rather than one transaction (its own docstring, "THE
    WRITES, AND THE ORDERING THE MISSING TRANSACTION FORCES", explains why at length). A
    crash past the point of no return is reported to the one HTTP caller of the one
    request that hit it as ``PaperConcurrencyExhausted(partial_write=True)`` and nothing
    durable records it, so nobody could previously find an affected session again. This
    endpoint is that durable check, on demand: it replays the session from its
    append-only event and order log (``paper_replay.replay``, which writes nothing) and
    reports whether the live rows agree with it.

    `get_admin_user` rather than `get_operator_user`, matching `/kill-switch` above:
    reading whether a session's books agree with its own log is a strictly weaker
    capability than anything that could change them, which this endpoint does not do
    either — see `backend/paper/paper_reconciliation.py`'s own docstring.

    Runs under the service-role client, after this route's own `get_admin_user` check,
    so an admin can reconcile any user's session rather than only their own — `user_id`
    is the predicate every read underneath is still scoped by, exactly as
    `paper_repository` requires of every caller (Requirements 21.2, 21.5), so this still
    cannot read more of the account than the one named.

    404 for "no such session" and "not this user's session" alike — the same
    non-disclosure `paper_replay.replay` itself already gives, carried through unchanged
    rather than re-decided here.
    """
    from backend_app.backend.paper import paper_reconciliation as recon
    from backend_app.backend.paper import paper_replay as replay
    from backend_app.backend.paper.paper_repository import PaperRepositoryError

    supabase = get_service_supabase()
    if supabase is None:
        raise HTTPException(503, "Service-role Supabase client is not configured.")

    try:
        report = recon.reconcile_session(supabase, user_id, session_id)
    except replay.ReplayRefused as exc:
        raise HTTPException(
            503,
            {
                "error": "PAPER_REPLAY_REFUSED",
                "reason": exc.reason,
                "detail": exc.detail,
            },
        )
    except PaperRepositoryError as exc:
        raise HTTPException(503, f"Paper trading read did not complete: {exc}")

    if report is None:
        raise HTTPException(404, "No such Paper_Session for this user.")
    return report.as_dict()
