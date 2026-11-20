"""backend/platform_stats.py -- API-03: the real figures behind GET /api/stats.

THE DEFECT
    ``main.py``'s ``GET /api/stats`` is, by design, public and platform-wide --
    ``algo22-terminal/src/design/pageFields.js``'s own extensive documentation of
    ``pages/Profile.jsx`` confirms the frontend already treats it that way, labels all
    four figures as "the platform's aggregate, not yours", and is NOT asking for this
    endpoint to require login. That part was correct.

    What was wrong is HOW the four figures were computed: the handler called
    ``dashboard_aggregation_service.get_dashboard_data(user={"id": "public"})`` -- the
    PER-USER dashboard aggregator, fed a sentinel identity that owns nothing. Three
    concrete consequences, all recorded in ``pageFields.js`` before this fix:

    1. Every per-user-scoped sub-query inside that aggregator filters by ``user_id``
       (RLS and/or an explicit predicate), and no real account has the id ``"public"``,
       so most of the aggregator's own figures came back as the EMPTY/zero case for a
       brand-new account -- not a platform aggregate at all, coincidentally rendering
       *some* number that looked plausible.
    2. ``total_pnl`` was populated from ``overview["today_pnl"]`` -- TODAY's P&L under a
       key spelled "total" -- and ``daily_pnl``/``weekly_pnl``/``monthly_pnl`` all read
       the exact same value. Four distinctly-labelled figures, one number, wrongly
       labelled on top of being the wrong number.
    3. The handler's ``except`` returned 200 with every figure forced to ``0`` -- a
       failed read is indistinguishable on the wire from a genuinely idle platform.

THE FIX
    A dedicated, genuinely platform-wide aggregation, under the SERVICE role (there is no
    tenant to scope by -- that is the whole point of this endpoint), over three tables
    that are safe to aggregate without exposing anything about any one account: a row
    count or a sum reveals nothing about which account it came from.

    * ``total_strategies`` -- COUNT of ``public.strategies`` where ``archived_at IS
      NULL`` (the same "live strategy" predicate ``005a_strategy_archive.sql``'s own
      reads use).
    * ``active_bots`` -- COUNT of ``strategy_deployments`` where ``status = 'running'``.
    * ``total_trades`` / ``win_rate`` -- COUNT, and COUNT where ``realized_pnl > 0``,
      over ``public.trade_history`` (migration 040; RT-04). This table did not exist
      before this session and currently holds no rows anywhere this has been run against
      -- so a freshly-deployed or still-quiet platform HONESTLY reports zero, which is
      the correct answer for "nothing has closed a trade yet", not a defect in this
      module.
    * ``total_pnl`` (lifetime), ``daily_pnl``, ``weekly_pnl``, ``monthly_pnl`` -- four
      DISTINCT sums of ``trade_history.realized_pnl``, windowed by ``closed_at``, which
      is what fixes defect (2) above: four labels now mean four different things.

    A statement that does not complete RAISES (:class:`PlatformStatsUnavailable`) rather
    than substituting ``0`` -- the main.py handler turns that into a 503, not a 200 that
    looks like an idle platform (defect 3 above).

WHAT THIS DOES NOT DO
    It does not page or cap ``trade_history`` -- the PnL sums are computed by summing
    every row's ``realized_pnl`` client-side rather than through a SQL ``SUM()`` (this
    codebase's ``supabase-py`` table builder has no aggregate-function seam; only
    ``count="exact"`` row counts are available without a server-side view or RPC). This
    is sized for what the table holds today (nothing, platform-wide) and is a known,
    stated scaling limit for whenever that stops being true -- not a silent one.
"""
from __future__ import annotations

import inspect
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from backend_app.backend.strategy_archive import is_missing_archived_at_error

logger = logging.getLogger("PlatformStats")


class PlatformStatsUnavailable(RuntimeError):
    """A statement needed to compute the platform-wide figures did not complete."""


async def _run(query: Any) -> Any:
    result = query.execute()
    return await result if inspect.isawaitable(result) else result


async def get_platform_stats(supabase: Any) -> Dict[str, Any]:
    """The genuine, platform-wide figures ``GET /api/stats`` reports.

    Raises :class:`PlatformStatsUnavailable` if any statement does not complete -- the
    caller's job is to turn that into an honest 503, never a 200 of zeros.
    """
    if supabase is None:
        raise PlatformStatsUnavailable("no service-role client is available")

    try:
        try:
            resp = await _run(
                supabase.table("strategies")
                .select("id", count="exact")
                .is_("archived_at", "null")
            )
        except Exception as exc:  # noqa: BLE001 - narrowed below
            # 005a_strategy_archive.sql (which adds `archived_at`) is applied by hand and
            # may not exist yet in a given environment -- `strategies.py`'s own list route
            # has the identical landmine and the identical disposition: "column absent"
            # means "nothing is archived", so every row counts.
            if not is_missing_archived_at_error(exc):
                raise
            resp = await _run(supabase.table("strategies").select("id", count="exact"))
        total_strategies = getattr(resp, "count", None)
        if total_strategies is None:
            raise PlatformStatsUnavailable("strategies count query returned no count")

        resp = await _run(
            supabase.table("strategy_deployments")
            .select("id", count="exact")
            .eq("status", "running")
        )
        active_bots = getattr(resp, "count", None)
        if active_bots is None:
            raise PlatformStatsUnavailable(
                "strategy_deployments count query returned no count"
            )

        resp = await _run(
            supabase.table("trade_history").select("trade_id", count="exact")
        )
        total_trades = getattr(resp, "count", None)
        if total_trades is None:
            raise PlatformStatsUnavailable("trade_history count query returned no count")

        resp = await _run(
            supabase.table("trade_history")
            .select("trade_id", count="exact")
            .gt("realized_pnl", 0)
        )
        winning_trades = getattr(resp, "count", None)
        if winning_trades is None:
            raise PlatformStatsUnavailable(
                "trade_history winning-count query returned no count"
            )
    except PlatformStatsUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as PlatformStatsUnavailable
        raise PlatformStatsUnavailable(f"a count query failed: {exc}") from exc

    win_rate = (winning_trades / total_trades) if total_trades > 0 else 0.0

    now = datetime.now(timezone.utc)
    today_cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_cutoff = now - timedelta(days=7)
    month_cutoff = now - timedelta(days=30)

    try:
        resp = await _run(
            supabase.table("trade_history").select("realized_pnl, closed_at")
        )
        rows = getattr(resp, "data", None)
        if rows is None:
            raise PlatformStatsUnavailable("trade_history pnl query returned no data")
    except PlatformStatsUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as PlatformStatsUnavailable
        raise PlatformStatsUnavailable(f"trade_history pnl query failed: {exc}") from exc

    def _pnl_since(cutoff: datetime) -> float:
        total = 0.0
        for row in rows:
            closed_at = row.get("closed_at")
            if not closed_at:
                continue
            try:
                closed = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
            except ValueError:
                continue
            if closed >= cutoff:
                total += float(row.get("realized_pnl") or 0.0)
        return total

    total_pnl = sum(float(row.get("realized_pnl") or 0.0) for row in rows)

    return {
        "total_trades": total_trades,
        "total_pnl": total_pnl,
        "win_rate": win_rate,
        "active_bots": active_bots,
        "total_strategies": total_strategies,
        "daily_pnl": _pnl_since(today_cutoff),
        "weekly_pnl": _pnl_since(week_cutoff),
        "monthly_pnl": _pnl_since(month_cutoff),
    }
