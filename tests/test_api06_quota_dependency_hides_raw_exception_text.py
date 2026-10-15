"""API-06 -- a quota dependency's 503 no longer echoes the raw upstream exception text.

THE DEFECT
    ``enforce_quota`` (``backend_app/core/subscription_dependencies.py``, the shared
    logic behind the ``Depends(check_bot_quota)`` / ``check_ml_quota`` / etc. family
    used across ``routers/strategies.py``, ``routers/strategy_operations.py`` and
    ``routers/library.py``) already answered a usage-read failure with the correct
    status, 503, and never admitted the request as a pass -- but its ``detail["message"]``
    was ``str(exc)``, the raw ``UsageReadFailed`` text. ``UsageReadFailed`` is frequently
    constructed directly from an upstream failure
    (``usage_ledger.py``'s own ``raise UsageReadFailed(str(exc)) from exc``), so a raw
    Supabase/PostgREST error string -- table names, constraint names, connection detail
    -- reached the caller verbatim inside an authenticated API response.

THE FIX
    The 503 and the structured ``{"error": "ENTITLEMENT_USAGE_UNREADABLE", ...}`` shape
    are unchanged (an existing test already pins the error code). Only the leaked
    ``message`` value is replaced with a fixed, generic string; the real exception is
    now logged server-side instead of being serialised into the response.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from backend_app.core.subscription_dependencies import check_strategy_quota


def _user(user_id: str = "u_broken", tier: str = "pro") -> dict:
    return {
        "id": user_id,
        "email": f"{user_id}@example.invalid",
        "app_metadata": {"subscription_tier": tier, "subscription_status": "active"},
    }


def _broken_supabase_with_distinctive_db_error(detail: str) -> MagicMock:
    """A Supabase double whose usage-count read raises, carrying ``detail`` in its text."""
    sb = MagicMock()

    broken = MagicMock()
    broken.execute.side_effect = RuntimeError(detail)
    for method in ("select", "eq", "in_", "is_", "limit"):
        getattr(broken, method).return_value = broken

    profile = MagicMock()
    profile.execute.return_value = MagicMock(
        data=[
            {
                "subscription_tier": "pro",
                "subscription_status": "active",
                "plan_limit_overrides": None,
            }
        ],
        error=None,
    )
    for method in ("select", "eq", "in_", "is_", "limit"):
        getattr(profile, method).return_value = profile

    sb.table.side_effect = lambda name: profile if name == "profiles" else broken
    return sb


@pytest.mark.asyncio
class TestTheRawExceptionTextNoLongerReachesTheCaller:
    async def test_the_503_message_is_generic_not_the_raw_db_error(self):
        distinctive = (
            "relation \"public.secret_internal_table\" does not exist (connection "
            "postgres://prod-db-host.internal:5432/trading)"
        )
        sb = _broken_supabase_with_distinctive_db_error(distinctive)

        with patch.dict("os.environ", {"ENV": "production"}):
            with pytest.raises(HTTPException) as exc_info:
                await check_strategy_quota(_user(), sb)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["error"] == "ENTITLEMENT_USAGE_UNREADABLE"
        message = exc_info.value.detail["message"]
        assert distinctive not in message, (
            f"the raw upstream exception text leaked into the response: {message!r}"
        )
        assert "secret_internal_table" not in message
        assert "prod-db-host" not in message

    async def test_the_message_is_still_informative_to_a_human(self):
        """NEGATIVE CONTROL: the fix must not turn the message into an empty or useless
        string -- it should still read as a real, actionable refusal.
        """
        sb = _broken_supabase_with_distinctive_db_error("anything")

        with patch.dict("os.environ", {"ENV": "production"}):
            with pytest.raises(HTTPException) as exc_info:
                await check_strategy_quota(_user(), sb)

        message = exc_info.value.detail["message"]
        assert isinstance(message, str) and len(message) > 10

    async def test_the_real_exception_is_still_logged_server_side(self, caplog):
        import logging

        distinctive = "a distinctive, loggable-only failure detail"
        sb = _broken_supabase_with_distinctive_db_error(distinctive)

        with patch.dict("os.environ", {"ENV": "production"}):
            with caplog.at_level(logging.ERROR, logger="SubscriptionDependencies"):
                with pytest.raises(HTTPException):
                    await check_strategy_quota(_user(), sb)

        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert distinctive in joined, (
            "the real exception detail is no longer logged anywhere -- an operator "
            "would have nothing to diagnose the failure from"
        )
