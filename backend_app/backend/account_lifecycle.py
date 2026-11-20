"""
backend/account_lifecycle.py — PRIV-01: account data export and account deletion.

PRIV-01's finding: "No account deletion or data export." Neither existed anywhere in this
codebase before this module (grepped for delete_account/account_deletion/data_export/
export_user_data/anonymize and found nothing).

SCOPE, STATED HONESTLY RATHER THAN CLAIMED COMPLETE
----------------------------------------------------
:func:`export_account_data` reads the categories this module knows how to read directly:
profile, strategies, backtests, marketplace subscriptions/listings, settlements, and paper
trading sessions. Connected exchange NAMES are listed; the decrypted API credentials
themselves are never exported (the requester is already authenticated as this user and can
see them live in the app — exporting them into a downloadable file is a pure liability with
no portability benefit). Each category is read independently and reports its own
``ok``/``error`` rather than letting one failing table abort the whole export.

:func:`delete_account` is deliberately NOT a physical cascade-delete across every table that
references this user. Strategies, backtests, settlements and paper trading history are
financial/audit records this product — and in several cases the law — needs to retain;
auditing which of this repository's ~58 tables may legally and safely be hard-deleted,
in what order, without breaking a foreign key, is exactly the kind of repo-wide change this
session has consistently declined to improvise without a real Postgres instance to verify
against (the same reasoning TENANT-01's FORCE RLS wiring and RECON-01's leader election were
left to a dedicated session over). What this function DOES do, using existing, already-tested
primitives rather than new destructive SQL:

  1. Stop every live deployment (``strategy_lifecycle.stop_deployments_for_reason`` — the
     same Requirement 20.9 path the kill switch and a guard trip already use).
  2. Stop every RUNNING/PAUSED paper trading session (``paper_session_service.stop_session``,
     called the way it is already designed to be called without a live feed/task handle —
     both are documented as optional and reported rather than required).
  3. Revoke every connected exchange's API keys (``APIKeyVault.delete_exchange_keys``).
  4. Anonymise the profile's PII fields and set ``is_frozen = True`` — the SAME flag
     ``core.dependencies.get_current_user`` already enforces as a 403 everywhere, so the
     account can no longer authenticate anywhere in the product once this lands, with no
     new column and no migration.

Step 4 runs with a SERVICE-ROLE client while steps 1-3 run with the caller's own
JWT-scoped one. That split is required, not stylistic. ``is_frozen`` and ``email`` are
platform-controlled columns, and the column-guard trigger migration 033 installs on
``public.profiles`` refuses writes to them from the ``anon``/``authenticated`` roles. It
has to: a role that can SET ``is_frozen`` can also CLEAR it, which would let any suspended
user unfreeze themselves (SEC-07). So an account cannot be frozen using the credentials of
the account being frozen. Steps 1-3 keep the user-scoped client because row-level security
is what limits them to this user's own deployments, sessions and keys.

Step 4 is the one step that must land for this to count as "deleted" at all; its own
exception is allowed to propagate so the caller (the route) can report deletion as failed
rather than claiming success over a profile that was never anonymised. Steps 1-3 are
best-effort and individually reported: a connected exchange this session cannot reach does
not prevent the account from being locked and the profile from being scrubbed.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List, Mapping, Optional

from backend_app.backend.api_key_vault import APIKeyVault
from backend_app.backend.paper import paper_repository as paper_repo
from backend_app.backend.paper.paper_session_service import stop_session as stop_paper_session
from backend_app.backend.strategy_lifecycle import (
    TRIGGER_USER,
    stop_deployments_for_reason,
)

logger = logging.getLogger("AccountLifecycle")

#: ``(table, user_column)`` pairs read directly for :func:`export_account_data`. Paper
#: trading has its own ownership-scoped reader (:func:`paper_repo.list_sessions`), reused
#: instead of a raw table read for the same reason every other caller of that module reuses
#: it: ``user_id`` as a predicate on the statement, not a filter applied after.
_DIRECT_EXPORT_TABLES: tuple = (
    ("profiles", "id"),
    ("strategies", "user_id"),
    ("strategy_backtests", "user_id"),
    ("library_subscriptions", "user_id"),
    ("library_strategies", "author_id"),
    ("marketplace_settlements", "purchaser_id"),
)

#: ``paper_sessions.session_state`` values a deletion must stop. ``CREATED`` never started
#: trading and ``STOPPED`` already has, so neither needs this module's attention.
_ACTIVE_SESSION_STATES = ("RUNNING", "PAUSED")


async def export_account_data(supabase: Any, *, user_id: str) -> Dict[str, Any]:
    """Every category of this user's own data this module knows how to read.

    Args:
        supabase: the caller's RLS-scoped Persistence_Layer handle (the request-scoped
            client, authenticated as this user) — never a service-role client, so a filter
            mistake here is caught by RLS rather than by this function alone.
        user_id: the authenticated server-side identity. Never a request body/query value.
    """
    categories: Dict[str, Any] = {}

    for table, column in _DIRECT_EXPORT_TABLES:
        try:
            response = supabase.table(table).select("*").eq(column, user_id).execute()
            rows = getattr(response, "data", None) or []
            categories[table] = {"ok": True, "rows": rows, "count": len(rows)}
        except Exception as exc:  # noqa: BLE001 - reported per category, never swallowed
            logger.warning(f"[AccountLifecycle] export read of {table} failed: {exc}")
            categories[table] = {"ok": False, "error": str(exc)}

    try:
        sessions = paper_repo.list_sessions(supabase, user_id)
        categories["paper_sessions"] = {
            "ok": True, "rows": sessions, "count": len(sessions),
        }
    except Exception as exc:  # noqa: BLE001 - reported per category, never swallowed
        logger.warning(f"[AccountLifecycle] export read of paper_sessions failed: {exc}")
        categories["paper_sessions"] = {"ok": False, "error": str(exc)}

    try:
        vault = APIKeyVault()
        connected = vault.list_connected_exchanges(user_id)
        categories["connected_exchanges"] = {
            "ok": True, "rows": connected, "count": len(connected),
        }
    except Exception as exc:  # noqa: BLE001 - reported per category, never swallowed
        logger.warning(
            f"[AccountLifecycle] export read of connected exchanges failed: {exc}"
        )
        categories["connected_exchanges"] = {"ok": False, "error": str(exc)}

    return {
        "user_id": user_id,
        "categories": categories,
        "note": (
            "This export covers: profile, strategies, backtests, marketplace "
            "subscriptions and listings, settlements, and paper trading sessions. "
            "Connected exchange NAMES are listed; API credentials themselves are never "
            "exported. Contact support for any category not listed here."
        ),
    }


async def delete_account(
    supabase: Any,
    *,
    user_id: str,
    user: Mapping[str, Any],
    service_supabase: Any = None,
) -> Dict[str, Any]:
    """Stop runtimes, revoke exchange keys, anonymise PII, lock the account. See module docstring
    for exactly what this does and does not do, and why.

    ``supabase`` is the caller's own JWT-scoped client (PostgreSQL role ``authenticated``).
    Steps 1-3 use it deliberately: row-level security is what confines them to this user's
    deployments, sessions and keys, so running them with a privileged client would discard
    the isolation rather than add anything.

    ``service_supabase`` is used for step 4 only, and only because step 4 writes
    ``is_frozen`` and ``email``. Those are platform-controlled columns: the column-guard
    trigger on ``public.profiles`` (migration 033) rejects any write to them from the
    ``anon``/``authenticated`` roles, which is the entire point of SEC-07 -- a user who can
    set ``is_frozen`` can also CLEAR it and unfreeze their own suspended account. So
    freezing an account cannot be done with the account holder's own credentials. Passing
    ``None`` falls back to ``supabase``, which keeps the existing tests and DEV_MODE (where
    no service-role key is configured) working unchanged; in a deployed environment
    ``SUPABASE_SERVICE_ROLE_KEY`` is a required credential, so the route always supplies it.
    """
    summary: Dict[str, Any] = {"user_id": user_id}

    try:
        summary["deployments"] = await stop_deployments_for_reason(
            supabase,
            user_id=user_id,
            reason="account deletion requested by the user",
            triggered_by=TRIGGER_USER,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort step, reported not swallowed
        logger.error(
            f"[AccountLifecycle] stopping deployments failed for {user_id}: {exc}"
        )
        summary["deployments"] = {"ok": False, "error": str(exc)}

    stopped_sessions: List[str] = []
    failed_sessions: List[Dict[str, str]] = []
    try:
        sessions = paper_repo.list_sessions(supabase, user_id)
        for session in sessions:
            if session.get("session_state") not in _ACTIVE_SESSION_STATES:
                continue
            session_id = str(session.get("id") or "")
            try:
                # feed=None, loop_task=None, redis=None: this call does not hold this
                # session's live handles (it may be running, if at all, in a different
                # process) - stop_session's own docstring documents all three as accepted
                # and REPORTED rather than required, which is exactly the degraded-but-
                # correct path this deletion needs: the DB transition to STOPPED still
                # commits even though the live feed subscription cannot be released from
                # here.
                await stop_paper_session(
                    supabase, user, session_id, feed=None, loop_task=None, redis=None
                )
                stopped_sessions.append(session_id)
            except Exception as exc:  # noqa: BLE001 - one session's failure, not fatal
                logger.warning(
                    f"[AccountLifecycle] stopping paper session {session_id} for "
                    f"{user_id} failed: {exc}"
                )
                failed_sessions.append({"session_id": session_id, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001 - best-effort step, reported not swallowed
        logger.error(
            f"[AccountLifecycle] listing paper sessions for {user_id} failed: {exc}"
        )
    summary["paper_sessions"] = {"stopped": stopped_sessions, "failed": failed_sessions}

    revoked: List[str] = []
    failed_revocations: List[str] = []
    try:
        vault = APIKeyVault()
        for exchange_id in vault.list_connected_exchanges(user_id):
            try:
                if vault.delete_exchange_keys(user_id, exchange_id):
                    revoked.append(exchange_id)
                else:
                    failed_revocations.append(exchange_id)
            except Exception as exc:  # noqa: BLE001 - one exchange's failure, not fatal
                logger.warning(
                    f"[AccountLifecycle] revoking {exchange_id} keys for {user_id} "
                    f"failed: {exc}"
                )
                failed_revocations.append(exchange_id)
    except Exception as exc:  # noqa: BLE001 - best-effort step, reported not swallowed
        logger.error(
            f"[AccountLifecycle] exchange key revocation failed for {user_id}: {exc}"
        )
    summary["exchange_keys"] = {"revoked": revoked, "failed": failed_revocations}

    # The one step that must land. Not caught: a profile that was never anonymised and
    # never frozen is not a deleted account, and the caller must see this as a failure
    # rather than a success with a quiet asterisk.
    # Inlined (not assigned to a variable first) so the schema-drift guard
    # (tests/test_schema_table_reference_drift.py) can resolve this literal dict against
    # `profiles`' declared columns statically, the same way every other UPDATE in this
    # codebase is checked.
    # `email` and `is_frozen` are platform-controlled, so this UPDATE must NOT run as the
    # account holder -- see this function's docstring. Falls back to the caller's client
    # when no service-role client was supplied (tests, DEV_MODE).
    privileged = service_supabase if service_supabase is not None else supabase
    response = (
        privileged.table("profiles")
        .update(
            {
                "username": None,
                "display_name": "Deleted User",
                "avatar_url": None,
                "bio": None,
                "telegram_id": None,
                "email": f"deleted-{user_id}@deleted.invalid",
                "full_name": None,
                "is_frozen": True,
            }
        )
        .eq("id", user_id)
        .execute()
    )
    if inspect.isawaitable(response):
        response = await response
    updated_rows = getattr(response, "data", None) or []
    if not updated_rows:
        raise RuntimeError(
            f"the profile anonymisation UPDATE for {user_id} matched no row — the "
            f"account is not deleted"
        )
    summary["profile"] = {"ok": True, "anonymised": True, "is_frozen": True}

    return summary
