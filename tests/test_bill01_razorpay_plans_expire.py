"""BILL-01: a Razorpay plan purchase buys a fixed period, ends, and is revoked on full refund."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend_app.core import plan_expiry
from backend_app.core.billing_lifecycle import BillingLifecycle
from backend_app.core.subscription_dependencies import _one_time_period_has_lapsed
from backend_app.routers import billing


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# ── period arithmetic ──────────────────────────────────────────────────────
def test_add_billing_period_is_calendar_safe():
    assert BillingLifecycle.add_billing_period(_utc(2026, 1, 31), "month") == _utc(2026, 2, 28)
    assert BillingLifecycle.add_billing_period(_utc(2028, 1, 31), "month") == _utc(2028, 2, 29)
    assert BillingLifecycle.add_billing_period(_utc(2026, 12, 15), "month") == _utc(2027, 1, 15)
    assert BillingLifecycle.add_billing_period(_utc(2028, 2, 29), "year") == _utc(2029, 2, 28)
    assert BillingLifecycle.add_billing_period(_utc(2026, 5, 5), None) == _utc(2026, 6, 5)


# ── grant records the end of the paid period ───────────────────────────────
class _SB:
    def __init__(self, existing_end=None):
        self.updates, self.existing_end = [], existing_end
    def table(self, _): return self
    def select(self, *_): return self
    def eq(self, *_): return self
    def update(self, row): self.updates.append(row); return self
    def execute(self):
        return SimpleNamespace(data=[{"next_billing_date": self.existing_end}])


def test_razorpay_grant_records_period_end_and_provider():
    sb = _SB()
    before = datetime.now(timezone.utc)
    billing._record_plan_period(sb, "u1", "pro_quant", "free", "month", "razorpay")
    row = sb.updates[-1]
    end = datetime.fromisoformat(row["next_billing_date"])
    assert row["payment_provider"] == "razorpay" and row["subscription_status"] == "active"
    assert before + timedelta(days=27) < end < before + timedelta(days=32)


def test_early_renewal_of_same_plan_extends_from_current_end_not_from_now():
    cur = (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()
    sb = _SB(existing_end=cur)
    billing._record_plan_period(sb, "u1", "pro_quant", "pro_quant", "month", "razorpay")
    end = datetime.fromisoformat(sb.updates[-1]["next_billing_date"])
    assert end > datetime.now(timezone.utc) + timedelta(days=45)


def test_stripe_grant_records_only_the_provider_not_a_period():
    sb = _SB()
    billing._record_plan_period(sb, "u1", "pro_quant", "free", "month", "stripe")
    assert sb.updates[-1] == {"payment_provider": "stripe"}


def test_missing_columns_do_not_fail_the_grant_but_are_loud(caplog):
    sb = MagicMock(); sb.table.return_value.update.return_value.eq.return_value.execute.side_effect = RuntimeError("42703")
    with caplog.at_level("CRITICAL"):
        billing._record_plan_period(sb, "u1", "pro_quant", "free", "month", "razorpay")  # must not raise
    assert "will NOT expire" in caplog.text


# ── enforcement on every request ───────────────────────────────────────────
def test_lapsed_razorpay_plan_is_not_honoured_but_stripe_and_unrecorded_are_left_alone():
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    assert _one_time_period_has_lapsed({"payment_provider": "razorpay", "next_billing_date": past}) is True
    assert _one_time_period_has_lapsed({"payment_provider": "razorpay", "next_billing_date": future}) is False
    assert _one_time_period_has_lapsed({"payment_provider": "stripe", "next_billing_date": past}) is False
    assert _one_time_period_has_lapsed({"payment_provider": "razorpay", "next_billing_date": None}) is False
    assert _one_time_period_has_lapsed({"payment_provider": "razorpay", "next_billing_date": "garbage"}) is False


def test_get_plan_context_downgrades_a_lapsed_razorpay_plan():
    from backend_app.core import subscription_dependencies as sd
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[{
        "subscription_tier": "pro_quant", "subscription_status": "active", "plan_limit_overrides": {},
        "payment_provider": "razorpay", "next_billing_date": past}])
    with patch.object(sd, "redis_manager") as rm:
        rm.get = AsyncMock(return_value=None); rm.set = AsyncMock(); rm.setex = AsyncMock()
        ctx = asyncio.run(sd.get_plan_context("u1", sb))
    assert ctx.plan == "free" and ctx.status == "expired"


# ── refund revokes ─────────────────────────────────────────────────────────
def _refund_sb(invoice, update_rows):
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = SimpleNamespace(data=invoice)
    sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=update_rows)
    return sb


def _revoke(sb, payment):
    with patch.object(billing, "_background_sb", return_value=sb), \
         patch.object(billing, "_fetch_razorpay_payment", new=AsyncMock(return_value=payment)), \
         patch.object(billing, "invalidate_profile_cache", new=AsyncMock()):
        return asyncio.run(billing._revoke_plan_for_refunded_razorpay_payment("pay_1"))


def test_full_refund_revokes_the_plan():
    sb = _refund_sb([{"user_id": "u1", "plan": "pro_quant"}], [{"id": "u1"}])
    assert _revoke(sb, {"amount": 1000, "amount_refunded": 1000, "status": "refunded"}) is True
    upd = sb.table.return_value.update.call_args[0][0]
    assert upd["subscription_tier"] == "free"


def test_partial_refund_keeps_the_plan():
    sb = _refund_sb([{"user_id": "u1", "plan": "pro_quant"}], [{"id": "u1"}])
    assert _revoke(sb, {"amount": 1000, "amount_refunded": 400, "status": "captured"}) is False
    sb.table.return_value.update.assert_not_called()


def test_marketplace_and_unknown_payments_are_not_touched():
    assert _revoke(_refund_sb([{"user_id": "u1", "plan": "marketplace_x"}], []), {"amount": 1, "amount_refunded": 1}) is False
    assert _revoke(_refund_sb([], []), {"amount": 1, "amount_refunded": 1}) is False


# ── the sweep, against real Postgres ───────────────────────────────────────
URL = os.environ.get("AERORA_TEST_DATABASE_URL", "")
pg = pytest.mark.skipif(not URL.startswith("postgres"), reason="needs provisioned PostgreSQL")


class _PgClient:
    """Just enough PostgREST-shaped client over SQLAlchemy to run the sweep's real queries."""
    def __init__(self, conn): self.c, self._f, self._op, self._set = conn, [], "select", {}
    def table(self, _): n = _PgClient(self.c); return n
    def select(self, *_): self._op = "select"; return self
    def update(self, row): self._op, self._set = "update", row; return self
    def eq(self, k, v): self._f.append((k, "=", v)); return self
    def neq(self, k, v): self._f.append((k, "<>", v)); return self
    def lt(self, k, v): self._f.append((k, "<", v)); return self
    def limit(self, *_): return self
    def execute(self):
        from sqlalchemy import text
        where = " AND ".join(f"{k} {o} :f{i}" for i, (k, o, _) in enumerate(self._f))
        params = {f"f{i}": v for i, (_, _, v) in enumerate(self._f)}
        if self._op == "select":
            r = self.c.execute(text(f"SELECT id::text AS id, subscription_tier, next_billing_date::text AS next_billing_date FROM public.profiles WHERE {where}"), params)
            return SimpleNamespace(data=[dict(x._mapping) for x in r])
        sets = ", ".join(f"{k} = :s{i}" for i, k in enumerate(self._set))
        params.update({f"s{i}": v for i, v in enumerate(self._set.values())})
        r = self.c.execute(text(f"UPDATE public.profiles SET {sets} WHERE {where} RETURNING id::text AS id"), params)
        return SimpleNamespace(data=[dict(x._mapping) for x in r])


@pg
def test_sweep_expires_only_lapsed_razorpay_plans_on_real_postgres():
    import uuid
    from sqlalchemy import create_engine, text
    eng = create_engine(URL, future=True)
    with eng.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            ids = {k: str(uuid.uuid4()) for k in ("lapsed", "future", "stripe", "free")}
            rows = [("lapsed", "pro_quant", "razorpay", "now() - interval '1 day'"),
                    ("future", "pro_quant", "razorpay", "now() + interval '5 days'"),
                    ("stripe", "pro_quant", "stripe", "now() - interval '1 day'"),
                    ("free", "free", "razorpay", "now() - interval '1 day'")]
            for k, tier, prov, end in rows:
                conn.execute(text("INSERT INTO auth.users (id) VALUES (:i)"), {"i": ids[k]})
                conn.execute(text(f"INSERT INTO public.profiles (id, subscription_tier, payment_provider, next_billing_date) "
                                  f"VALUES (:i, :t, :p, {end})"), {"i": ids[k], "t": tier, "p": prov})
            conn.execute(text("SET LOCAL session_replication_role = origin"))
            out = asyncio.run(plan_expiry.expire_lapsed_one_time_plans(_PgClient(conn)))
            assert set(out) >= {ids["lapsed"]} and not ({ids["future"], ids["stripe"], ids["free"]} & set(out))
            got = {k: conn.execute(text("SELECT subscription_tier, subscription_status FROM public.profiles WHERE id=:i"), {"i": v}).one() for k, v in ids.items()}
            assert tuple(got["lapsed"]) == ("free", "expired")
            assert got["future"][0] == "pro_quant" and got["stripe"][0] == "pro_quant"
        finally:
            tx.rollback()
