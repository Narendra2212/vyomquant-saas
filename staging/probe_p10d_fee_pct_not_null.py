"""PHASE 10 WORKSTREAM D — Stage 1 probe. Third-asset fee vs `fee_records.fee_pct NOT NULL`.

Observation 1 claims `fee_records.fee_pct` is NOT NULL in the 026 DDL while `record_fee`
writes None for a third-asset fee. This probe establishes what the LIVE database does when
the application's own code path runs, rather than reasoning from the DDL.

Why this probe is necessary: `tests/test_fee_denomination_is_not_assumed.py` proves the
Python-level semantics with a `_FakeSession` whose `commit()` is `pass`. No statement ever
reaches a server, so a NOT NULL violation is invisible to it. That is exactly the gap.

EVERYTHING RUNS INSIDE A TRANSACTION THAT IS ROLLED BACK. Writes nothing durable.
Uses `auto_commit=False` so `record_fee` flushes rather than commits -- the INSERT still
reaches the server and is adjudicated by it, which is the measurement we need.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DSN = (ROOT / "staging" / "pg_dsn.txt").read_text(encoding="utf-8").strip()


def _sqlalchemy_url(dsn: str) -> str:
    return dsn.replace("postgresql://", "postgresql+psycopg2://", 1)


def _seed_tenant(session, text, tenant) -> None:
    """auth.users -> public.profiles, the chain `execution_records.tenant_id` requires."""
    session.execute(
        text(
            "INSERT INTO auth.users (id, email) VALUES (:t, :e) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {"t": str(tenant), "e": f"p10d-{str(tenant)[:8]}@example.invalid"},
    )
    session.execute(
        text("INSERT INTO public.profiles (id) VALUES (:t) ON CONFLICT (id) DO NOTHING"),
        {"t": str(tenant)},
    )


def main() -> int:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from backend_app.backend.fee_engine import FeeEngine, FeeType

    # `FeeRecordModel` declares FKs to `execution_records` and `positions`. Both owning
    # models must be in the same declarative registry before any mapper configures, or
    # SQLAlchemy raises NoReferencedTableError and we would measure an import defect
    # instead of the schema behaviour under test.
    import backend_app.core.models.execution_record  # noqa: F401
    import backend_app.core.position_model  # noqa: F401

    engine = create_engine(_sqlalchemy_url(DSN), future=True)
    Session = sessionmaker(bind=engine, future=True)

    print("=" * 78)
    print("PHASE 10 / WS-D / STEP 1 — third-asset fee against the LIVE fee_records")
    print("=" * 78)

    tenant = uuid.uuid4()
    exec_id = f"exec_p10d_{uuid.uuid4().hex[:10]}"

    session = Session()
    # One explicit transaction for the whole probe; rolled back in `finally`.
    try:
        # `execution_records.tenant_id` -> profiles(id) -> auth.users(id). Both created
        # inside the same rolled-back transaction, the way the PG-gated suites do it.
        _seed_tenant(session, text, tenant)
        session.execute(
            text(
                "INSERT INTO public.execution_records "
                "(execution_id, tenant_id, strategy_id, symbol, side, size, status, "
                " created_at, updated_at) "
                "VALUES (:e, :t, 'p10d-probe', 'BTC/USDT', 'buy', '1', "
                " 'EXECUTING'::executionstatus, now(), now())"
            ),
            {"e": exec_id, "t": str(tenant)},
        )
        session.flush()
        print(f"setup: execution_records row {exec_id} (tenant {tenant}) — flushed, not committed")

        fee = FeeEngine(session)

        # ── CONTROL: a quote-denominated fee. fee_pct IS computable. ──────────────
        print("-" * 78)
        print("CONTROL  — USDT fee on BTC/USDT (quote asset): fee_pct is computable")
        try:
            rec = asyncio.run(
                fee.record_fee(
                    execution_id=exec_id,
                    order_id="ord_p10d_control",
                    symbol="BTC/USDT",
                    side="buy",
                    size=Decimal("1"),
                    price=Decimal("60000"),
                    fee_amount=Decimal("30"),
                    fee_asset="USDT",
                    fee_type=FeeType.TAKER,
                    gross_pnl=Decimal("100"),
                    exchange_id="binance",
                    auto_commit=False,
                )
            )
            print(f"  RESULT: accepted by the server. fee_pct={rec.fee_pct!r} "
                  f"net_pnl={rec.net_pnl!r}")
            control_ok = True
        except Exception as e:  # noqa: BLE001
            print(f"  RESULT: UNEXPECTEDLY REJECTED — {type(e).__name__}: {e}")
            control_ok = False
            session.rollback()

        # ── THE CASE UNDER TEST: a third-asset fee. fee_pct is None. ──────────────
        print("-" * 78)
        print("SUBJECT  — BNB fee on BTC/USDT (third asset): record_fee sets fee_pct=None")
        # Re-establish the setup rows if the control rolled the transaction back.
        if not control_ok:
            _seed_tenant(session, text, tenant)
            session.execute(
                text(
                    "INSERT INTO public.execution_records "
                    "(execution_id, tenant_id, strategy_id, symbol, side, size, status, "
                    " created_at, updated_at) "
                    "VALUES (:e, :t, 'p10d-probe', 'BTC/USDT', 'buy', '1', "
                    " 'EXECUTING'::executionstatus, now(), now())"
                ),
                {"e": exec_id, "t": str(tenant)},
            )
            session.flush()

        third_asset_rejected = None
        try:
            rec = asyncio.run(
                fee.record_fee(
                    execution_id=exec_id,
                    order_id="ord_p10d_subject",
                    symbol="BTC/USDT",
                    side="buy",
                    size=Decimal("1"),
                    price=Decimal("60000"),
                    fee_amount=Decimal("0.0005"),
                    fee_asset="BNB",
                    fee_type=FeeType.TAKER,
                    gross_pnl=Decimal("100"),
                    exchange_id="binance",
                    auto_commit=False,
                )
            )
            third_asset_rejected = False
            print(f"  RESULT: ACCEPTED. fee_pct={rec.fee_pct!r} net_pnl={rec.net_pnl!r}")
            print("  => Observation 1 is a FALSE POSITIVE on this substrate.")
        except Exception as e:  # noqa: BLE001
            third_asset_rejected = True
            print(f"  RESULT: REJECTED BY THE SERVER — {type(e).__name__}")
            msg = str(e).split("\n")[0][:300]
            print(f"          {msg}")
            print("  => The fee is NOT RECORDED. The amount the venue actually charged")
            print("     is lost, and the caller sees a database exception instead.")

        # ── What the caller sees: is the loss silent? ─────────────────────────────
        print("-" * 78)
        print("BLAST RADIUS — is the rejection caught anywhere above record_fee?")
        print("  record_fee has no try/except around db.add/flush/commit.")
        print("  record_fill_fee is a thin delegate (fee_engine.py:247) — no handler.")
        print("  So the exception propagates to whichever fill handler called it, and the")
        print("  session's transaction is left in a failed state: any later write in the")
        print("  same transaction also fails (25P02 InFailedSqlTransaction).")

        print("=" * 78)
        print("VERDICT")
        print("=" * 78)
        print(f"  live fee_records.fee_pct is NOT NULL ........ {'yes' if third_asset_rejected else 'see above'}")
        print(f"  quote-asset fee accepted (control) .......... {control_ok}")
        print(f"  third-asset fee rejected => fee record lost . {third_asset_rejected}")
        if third_asset_rejected:
            print("\n  OBSERVATION 1: CONFIRMED REAL DEFECT.")
            print("  A fee the venue charged produces no fee_records row at all.")
            return 0
        print("\n  OBSERVATION 1: NOT REPRODUCED.")
        return 1
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        print("\n[rolled back — nothing durable was written]")


if __name__ == "__main__":
    raise SystemExit(main())
