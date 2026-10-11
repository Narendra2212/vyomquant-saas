"""
Fee Engine

STEP 7.4 — PRODUCTION HARDENING

Track trading fees and deduct from PnL.

Fee Types:
┌─────────────────────────────────────────────────────────────────┐
│  Maker Fee:       Rebate for adding liquidity                  │
│                    Usually negative (you get paid)               │
├─────────────────────────────────────────────────────────────────┤
│  Taker Fee:       Charge for removing liquidity                │
│                    Usually 0.05% - 0.1%                        │
├─────────────────────────────────────────────────────────────────┤
│  Funding Fee:     Periodic payment for holding positions        │
│                    Every 8 hours (perpetual futures)         │
└─────────────────────────────────────────────────────────────────┘

Fee Impact on PnL:
Realized PnL = (exit_price - entry_price) × size - fees
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from backend_app.core.database import Base

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# FEE TYPES
# ═══════════════════════════════════════════════════════════════════════════════

class FeeType(Enum):
    """Types of trading fees."""
    MAKER = "maker"              # Liquidity maker fee (often negative = rebate)
    TAKER = "taker"              # Liquidity taker fee
    FUNDING = "funding"          # Funding fee (perpetual futures)
    LIQUIDATION = "liquidation"  # Liquidation fee
    WITHDRAWAL = "withdrawal"    # Withdrawal fee (not trading)
    DEPOSIT = "deposit"        # Deposit fee (not trading)


class FeeStatus(Enum):
    """Fee record status."""
    PENDING = "pending"          # Not yet deducted
    DEDUCTED = "deducted"        # Deducted from balance
    REBATED = "rebated"         # Rebate paid (maker)


class TenantResolutionError(Exception):
    """The owning tenant of a fee could not be established.

    FEE-01. `_get_tenant_id_from_execution` and `_get_tenant_id_from_position` used to
    return `UUID(int=0)` -- the all-zero uuid -- when the parent row was missing, so a fee
    whose execution or position could not be found was written against a tenant that
    belongs to no customer. Under `fee_records`'s row-level security
    (`fee_records_owner_access USING auth.uid() = tenant_id`) such a row is invisible to
    every authenticated caller and legible only to `service_role`: fail-closed, but
    silently unreadable rather than loudly wrong, and the all-zero id is a plausible value
    a bug elsewhere could collide with.

    Raising instead means an unattributable fee is refused at the point of recording, with
    the reason named, rather than written to a tenant that does not exist. Measured: NO
    production code path calls the fee engine (only tests and the attack scripts), and
    every current caller already holds a real tenant uuid, so this breaks no live call
    site. `UUID(int=0)` was never an established sentinel convention here -- it appeared
    only in these two fallbacks.
    """
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FeeBreakdown:
    """Complete fee breakdown for a trade."""
    # Identifiers
    execution_id: str
    order_id: str
    symbol: str
    
    # Fee details
    fee_type: FeeType
    fee_amount: Decimal
    fee_asset: str  # BTC, USDT, etc.
    
    # Calculations
    fee_pct: Decimal  # Fee as percentage of trade
    trade_volume: Decimal
    
    # Totals
    gross_pnl: Decimal  # Before fees
    net_pnl: Decimal    # After fees
    
    # Metadata
    timestamp: datetime
    exchange_id: str


# ═══════════════════════════════════════════════════════════════════════════════
# SQLALCHEMY MODEL
# ═══════════════════════════════════════════════════════════════════════════════

class FeeRecordModel(Base):
    """
    SQLAlchemy model for fee_records table.
    
    Tracks all trading fees for accurate PnL.
    """
    __tablename__ = "fee_records"
    
    # Primary key
    fee_id = Column(String, primary_key=True)
    
    # Relationships
    tenant_id = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    execution_id = Column(String, ForeignKey("execution_records.execution_id"), nullable=True)
    order_id = Column(String, nullable=False, index=True)
    position_id = Column(String, ForeignKey("positions.position_id"), nullable=True)
    
    # Trade details
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    
    # Fee details (stored as strings for precision)
    fee_type = Column(SQLEnum(FeeType), nullable=False)
    fee_amount = Column(String, nullable=False)  # "0.00050000" — the fee ACTUALLY charged
    fee_asset = Column(String, nullable=False)   # "USDT" — the asset it was charged in
    # NULLABLE, Phase 10 observation A. `fee_pct` is the fee as a fraction of trade volume,
    # which only has meaning when the fee and the volume are in the SAME currency. For a fee
    # denominated in a THIRD asset (e.g. a BNB fee on BTC/USDT) no conversion rate is
    # available in `record_fee`, so the percentage is genuinely NOT COMPUTABLE — and
    # `record_fee` correctly writes `fee_pct=None` in that case. The column was `NOT NULL`,
    # so committing a third-asset fee raised a NOT NULL violation and LOST the fee record,
    # even though the fee itself (amount + asset, both still NOT NULL) was known exactly.
    # NULL is the honest representation of "not computable": a zero would falsely state a
    # free trade, and fabricating a conversion price is forbidden. The fee amount and asset
    # remain mandatory, so the money charged is never lost.
    fee_pct = Column(String, nullable=True)      # "0.00050000" (0.05%), or NULL if third-asset
    
    # Trade volume
    trade_volume = Column(String, nullable=False)
    
    # PnL impact
    gross_pnl = Column(String, nullable=True)    # PnL before fees
    net_pnl = Column(String, nullable=True)      # PnL after fees
    
    # Status
    status = Column(SQLEnum(FeeStatus), nullable=False, default=FeeStatus.DEDUCTED)
    
    # Exchange info
    exchange_id = Column(String, nullable=False)
    
    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Indexes
    __table_args__ = (
        Index('idx_fee_records_tenant_symbol', 'tenant_id', 'symbol'),
        Index('idx_fee_records_execution', 'execution_id'),
        Index('idx_fee_records_position', 'position_id'),
        Index('idx_fee_records_type', 'fee_type'),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# FEE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class FeeEngine:
    """
    STEP 7.4: Fee tracking and calculation engine.
    
    Features:
    - Track maker/taker/funding fees
    - Calculate fee impact on PnL
    - Store fee records for audit
    - Support fee rebates (maker)
    
    Usage:
        fee_engine = FeeEngine(db_session)
        
        # Record fee from fill
        await fee_engine.record_fill_fee(
            execution_id="exec_123",
            order_id="12345",
            symbol="BTCUSD",
            side="buy",
            size=Decimal("0.1"),
            price=Decimal("50000"),
            fee_amount=Decimal("0.025"),  # 0.05% of $50000 = $25
            fee_asset="USDT",
            fee_type=FeeType.TAKER,
            exchange_id=os.getenv("DEFAULT_EXCHANGE", "binance")
        )
        
        # Get fee summary
        summary = fee_engine.get_fee_summary(tenant_id)
    """
    
    PRECISION = Decimal('0.00000001')
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    async def record_fill_fee(
        self,
        execution_id: str,
        order_id: str,
        symbol: str,
        side: str,
        size: Decimal,
        price: Decimal,
        fee_amount: Decimal,
        fee_asset: str,
        fee_type: FeeType,
        exchange_id: str,
        position_id: Optional[str] = None,
        gross_pnl: Optional[Decimal] = None
    ) -> FeeRecordModel:
        """
        Record a trading fee from a fill.
        
        Args:
            execution_id: Our execution ID
            order_id: Exchange order ID
            symbol: Trading symbol
            side: buy/sell
            size: Fill size
            price: Fill price
            fee_amount: Fee charged
            fee_asset: Asset fee paid in
            fee_type: MAKER/TAKER/FUNDING
            exchange_id: Exchange name
            position_id: Related position (optional)
            gross_pnl: PnL before fees (optional)
            
        Returns:
            Created fee record
        """
        return await self.record_fee(
            execution_id=execution_id,
            order_id=order_id,
            symbol=symbol,
            side=side,
            size=size,
            price=price,
            fee_amount=fee_amount,
            fee_asset=fee_asset,
            fee_type=fee_type,
            exchange_id=exchange_id,
            position_id=position_id,
            gross_pnl=gross_pnl
        )

    async def record_fee(
        self,
        execution_id: str,
        order_id: str,
        symbol: str,
        side: str,
        size: Decimal,
        price: Decimal,
        fee_amount: Decimal,
        fee_asset: str,
        fee_type: FeeType = FeeType.MAKER,
        position_id: Optional[str] = None,
        gross_pnl: Optional[Decimal] = None,
        exchange_id: str = "binance",
        auto_commit: bool = True
    ) -> FeeRecordModel:
        """
        Record a fee for an execution with proper quote-asset valuation.
        """
        # ═══════════════════════════════════════════════════════════════════
        # FEE DENOMINATION — THREE CASES, NOT TWO
        # ═══════════════════════════════════════════════════════════════════
        # This block used to read:
        #
        #     if fee_asset.upper() == base_asset.upper():
        #         fee_cost_in_quote = fee_amount * price
        #     else:
        #         # Fee paid in quote asset (e.g. USDT) or third asset
        #         fee_cost_in_quote = fee_amount
        #
        # The comment on that `else` names the defect. It lumps together two cases that
        # are not alike:
        #
        #   QUOTE asset  - the fee is ALREADY in quote terms, so 1:1 is correct.
        #   THIRD asset  - the fee is in a currency nobody has converted, and 1:1 is a
        #                  category error. A BNB fee on a BTC/USDT trade is the common
        #                  case, not an exotic one: paying fees in BNB is the Binance
        #                  default and is incentivised with a 25% discount. `0.0005 BNB`
        #                  was recorded as `$0.0005` - understating the fee by roughly
        #                  three orders of magnitude, and with it every net PnL figure
        #                  derived from it.
        #
        # Separating them is strictly more correct and needs no new data for the quote
        # case. For a genuine third asset there is no conversion rate available here -
        # `record_fee` has no market-data handle - and this module's surrounding
        # convention is that a figure nobody measured is not substituted with a
        # plausible one. So the third-asset case is recorded with its true amount and
        # its true denomination, and `fee_cost_in_quote` is left UNSET rather than
        # wrong: `net_pnl` is then reported as absent rather than as a number that
        # silently omits the fee.
        #
        # A caller that needs third-asset netting should convert before calling and pass
        # the quote-denominated amount, or supply the rate. Reporting "not computed" is
        # recoverable; reporting a PnL that is wrong by 1000x is not.
        base_asset, quote_asset = self._parse_symbol(symbol)
        fee_asset_upper = (fee_asset or "").upper()

        #: None when the fee is denominated in an asset this method cannot convert.
        fee_cost_in_quote: Optional[Decimal]

        if fee_asset_upper == base_asset.upper():
            # Base asset (e.g. a BTC fee on BTC/USDT) — the trade's own price converts it.
            fee_cost_in_quote = fee_amount * price
        elif fee_asset_upper == quote_asset.upper():
            # Already quote-denominated (e.g. a USDT fee on BTC/USDT).
            fee_cost_in_quote = fee_amount
        else:
            # A third asset (e.g. BNB on BTC/USDT). No rate is available here.
            fee_cost_in_quote = None
            logger.warning(
                "FEE NOT CONVERTED: %s fee of %s on %s is denominated in neither the "
                "base (%s) nor the quote (%s) asset, and no conversion rate is "
                "available. The fee amount is recorded exactly; fee_pct and net_pnl are "
                "recorded as absent rather than computed from an unconverted amount.",
                fee_asset_upper, fee_amount, symbol, base_asset, quote_asset,
            )

        # Calculate trade volume
        trade_volume = size * price

        # Fee percentage relative to trade volume. Both figures must be in the same
        # currency for the ratio to mean anything, so an unconverted fee yields no
        # percentage rather than a ratio of two different units.
        if fee_cost_in_quote is not None and trade_volume > 0:
            fee_pct = (fee_cost_in_quote / trade_volume).quantize(self.PRECISION, rounding=ROUND_HALF_UP)
        else:
            fee_pct = None

        # Net PnL, only where the fee could actually be deducted from it. Previously an
        # unconverted third-asset fee was subtracted as though it were quote currency,
        # which produced a net_pnl essentially equal to the gross.
        net_pnl = None
        if gross_pnl is not None and fee_cost_in_quote is not None:
            net_pnl = (gross_pnl - fee_cost_in_quote).quantize(self.PRECISION, rounding=ROUND_HALF_UP)
        
        # Determine status
        status = FeeStatus.REBATED if fee_amount < 0 else FeeStatus.DEDUCTED
        
        # Create record
        fee_record = FeeRecordModel(
            # UNIQUE PER CALL, NOT PER SECOND (Phase 10 / WS-D).
            #
            # This was `f"fee_{execution_id}_{int(utcnow().timestamp())}"` -- a
            # WHOLE-SECOND timestamp and no order or fill component. So two fees recorded
            # against the SAME execution inside the same second produced the same primary
            # key, and the second one died on `fee_records_pkey` with a UniqueViolation
            # that no caller catches: the fee was lost and the session's transaction was
            # left aborted, poisoning any later write in it.
            #
            # That is not a corner case. One execution has as many fees as it has fills,
            # and a market order sweeping a liquid book fills several times in well under
            # a second. The collision is also not idempotency: the identical duplicate
            # call one second later succeeds, so the old id deduplicated by luck of the
            # clock rather than by intent. `record_fee` is append-only -- the behaviour
            # `tests/test_fee_third_asset_pct_is_nullable_postgres.py` pins, which had to
            # `time.sleep(1.1)` to get two rows.
            #
            # `record_funding_fee` below deliberately KEEPS a deterministic id
            # (`fee_funding_{position_id}_{funding_period}`): there is exactly one funding
            # fee per position per period, so there the primary key IS the idempotency
            # key and a collision is the correct outcome. The two cases differ and are now
            # treated differently.
            # Regression: tests/test_p10d_fee_summary_does_not_mix_denominations_postgres.py
            fee_id=(
                f"fee_{execution_id}_{int(datetime.utcnow().timestamp())}"
                f"_{uuid4().hex[:12]}"
            ),
            tenant_id=self._get_tenant_id_from_execution(execution_id),
            execution_id=execution_id,
            order_id=order_id,
            position_id=position_id,
            symbol=symbol.upper(),
            side=side,
            fee_type=fee_type,
            fee_amount=str(fee_amount),
            fee_asset=fee_asset.upper(),
            # `is not None`, NOT truthiness. `Decimal('0')` is falsy, so
            # `str(x) if x else None` stored a break-even trade's PnL as NULL -
            # indistinguishable from "never computed". A fee of exactly zero (a full
            # rebate) and a PnL of exactly zero are both real measurements.
            fee_pct=str(fee_pct) if fee_pct is not None else None,
            trade_volume=str(trade_volume),
            gross_pnl=str(gross_pnl) if gross_pnl is not None else None,
            net_pnl=str(net_pnl) if net_pnl is not None else None,
            status=status,
            exchange_id=exchange_id
        )
        
        self.db.add(fee_record)
        if auto_commit:
            self.db.commit()
            if hasattr(self.db, "refresh"):
                try:
                    self.db.refresh(fee_record)
                except Exception:
                    pass
        else:
            if hasattr(self.db, "flush"):
                self.db.flush()
        
        # `fee_pct` is None for a fee denominated in a third asset, so it cannot be
        # formatted as a percentage. Saying "not converted" is the accurate rendering;
        # `f"{None * 100}"` would raise and take the whole fee record down with it.
        pct_text = f"{fee_pct * 100:.4f}%" if fee_pct is not None else "not converted"
        logger.info(
            f"FEE RECORDED: {fee_record.fee_id} | "
            f"{symbol} {side} | "
            f"type={fee_type.value} | "
            f"amount={fee_amount} {fee_asset} | "
            f"pct={pct_text}"
        )
        
        return fee_record
    
    async def record_funding_fee(
        self,
        position_id: str,
        symbol: str,
        fee_amount: Decimal,
        fee_asset: str,
        exchange_id: str,
        funding_period: str,  # e.g., "2026-05-02-0800"
        auto_commit: bool = True
    ) -> FeeRecordModel:
        """
        Record a funding fee for perpetual futures.
        
        Funding fees are charged every 8 hours.
        """
        fee_record = FeeRecordModel(
            fee_id=f"fee_funding_{position_id}_{funding_period}",
            tenant_id=self._get_tenant_id_from_position(position_id),
            execution_id=None,  # Funding not tied to execution
            order_id=f"funding_{funding_period}",
            position_id=position_id,
            symbol=symbol.upper(),
            side="hold",  # Funding charged for holding
            fee_type=FeeType.FUNDING,
            fee_amount=str(fee_amount),
            fee_asset=fee_asset.upper(),
            fee_pct="0",  # Fixed amount, not percentage
            trade_volume="0",
            status=FeeStatus.DEDUCTED,
            exchange_id=exchange_id
        )
        
        self.db.add(fee_record)
        if auto_commit:
            self.db.commit()
            if hasattr(self.db, "refresh"):
                try:
                    self.db.refresh(fee_record)
                except Exception:
                    pass
        else:
            if hasattr(self.db, "flush"):
                self.db.flush()
        
        direction = "paid" if fee_amount > 0 else "received"
        logger.info(
            f"FUNDING FEE {direction}: {fee_record.fee_id} | "
            f"{symbol} | "
            f"amount={abs(fee_amount)} {fee_asset} | "
            f"period={funding_period}"
        )
        
        return fee_record
    
    def calculate_net_pnl(
        self,
        gross_pnl: Decimal,
        fees: List[Decimal]
    ) -> Decimal:
        """
        Calculate net PnL after deducting fees.
        
        Args:
            gross_pnl: PnL before fees
            fees: List of fee amounts
            
        Returns:
            Net PnL
        """
        total_fees = sum(fees, Decimal('0'))
        net = gross_pnl - total_fees
        return net.quantize(self.PRECISION, rounding=ROUND_HALF_UP)
    
    def get_fee_summary(
        self,
        tenant_id: UUID,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Get fee summary for tenant.
        
        Returns:
            Dict with fee totals by type
        """
        query = self.db.query(FeeRecordModel).filter(
            FeeRecordModel.tenant_id == tenant_id
        )
        
        if start_time:
            query = query.filter(FeeRecordModel.created_at >= start_time)
        if end_time:
            query = query.filter(FeeRecordModel.created_at <= end_time)
        
        fees = query.all()

        # ═══════════════════════════════════════════════════════════════════
        # TWO DEFECTS THIS AGGREGATION USED TO HAVE (Phase 10 / WS-D)
        # ═══════════════════════════════════════════════════════════════════
        # 1. IT ADDED AMOUNTS IN DIFFERENT CURRENCIES. The loop was
        #
        #        totals[fee.fee_type] += Decimal(fee.fee_amount)
        #
        #    and `fee_amount` is denominated in `fee_asset`. A 30 USDT taker fee and a
        #    0.0005 BNB taker fee totalled `30.0005` -- a number in no currency at all --
        #    and `avg_fee_rate_pct` then divided that by a quote-denominated
        #    `trade_volume`. This is the SAME category error `record_fee` above was fixed
        #    for: it stopped treating a third-asset fee as quote currency per row, and the
        #    summary went on doing it for the figure an operator actually reads.
        #
        #    Paying fees in BNB is the Binance default, so a mixed-asset ledger is the
        #    normal case, not a corner one.
        #
        # 2. A RECORDED FEE COULD MAKE THE LEDGER UNREADABLE. `totals` was seeded with
        #    four of `FeeType`'s six members, so `totals[fee.fee_type] += ...` raised
        #    `KeyError: <FeeType.WITHDRAWAL>` on a withdrawal or deposit fee and took the
        #    whole summary down. Now derived from the enum, so a member added later is
        #    included automatically.
        #
        # THE RULE APPLIED HERE: a figure that requires a single denomination is reported
        # as ABSENT when there isn't one, rather than computed from a mixed sum -- the
        # convention `record_fee` already follows for `fee_pct` and `net_pnl`. Nothing is
        # lost: `totals_by_asset` and `by_type_and_asset` always carry every fee in its
        # own unit, and `count` always counts them all.
        #
        # Converting to a common currency is deliberately NOT done. There is no
        # market-data handle here and no rate the caller supplied, which is exactly why
        # `record_fee` leaves `fee_cost_in_quote` unset for a third asset.
        # Regression: tests/test_p10d_fee_summary_does_not_mix_denominations_postgres.py
        totals: Dict[FeeType, Decimal] = {ft: Decimal('0') for ft in FeeType}
        totals_by_asset: Dict[str, Decimal] = {}
        by_type_and_asset: Dict[str, Dict[str, Decimal]] = {
            ft.value: {} for ft in FeeType
        }

        total_volume = Decimal('0')

        for fee in fees:
            fee_amount = Decimal(fee.fee_amount)
            asset = (fee.fee_asset or "UNKNOWN").upper()

            totals[fee.fee_type] += fee_amount
            totals_by_asset[asset] = totals_by_asset.get(asset, Decimal('0')) + fee_amount

            per_type = by_type_and_asset[fee.fee_type.value]
            per_type[asset] = per_type.get(asset, Decimal('0')) + fee_amount

            total_volume += Decimal(fee.trade_volume)

        fee_assets = sorted(totals_by_asset)

        # One denomination (or an empty ledger, where zero is a known quantity) means the
        # single-currency figures are real and are reported exactly as before.
        denomination: Optional[str] = fee_assets[0] if len(fee_assets) == 1 else None
        expressible = len(fee_assets) <= 1

        total_fees: Optional[Decimal] = None
        avg_fee_rate: Optional[Decimal] = None
        if expressible:
            # Fees are costs, so positive = paid. `sum` over the per-asset totals rather
            # than over `totals`, so the figure is explicitly a single-asset sum.
            total_fees = sum(totals_by_asset.values(), Decimal('0'))
            avg_fee_rate = (
                (total_fees / total_volume * 100)
                if total_volume > 0 else Decimal('0')
            )

        return {
            # None when the ledger spans more than one fee asset.
            "total_fees": str(total_fees) if total_fees is not None else None,
            "total_volume": str(total_volume),
            "avg_fee_rate_pct": (
                str(avg_fee_rate.quantize(self.PRECISION))
                if avg_fee_rate is not None else None
            ),
            # The unit `total_fees` / `avg_fee_rate_pct` / `by_type` are in. None means
            # those three are absent because no single unit applies.
            "denomination": denomination,
            "fee_assets": fee_assets,
            # Always populated, always per-unit — nothing is ever dropped from here.
            "totals_by_asset": {a: str(v) for a, v in totals_by_asset.items()},
            "by_type_and_asset": {
                ft: {a: str(v) for a, v in per_asset.items()}
                for ft, per_asset in by_type_and_asset.items()
            },
            # Flat per-type totals, valid only under a single denomination. None rather
            # than a cross-currency sum, so the figure cannot be read as money it isn't.
            "by_type": (
                {ft.value: str(amount) for ft, amount in totals.items()}
                if expressible else None
            ),
            "count": len(fees),
            "period": {
                "start": start_time.isoformat() if start_time else None,
                "end": end_time.isoformat() if end_time else None
            }
        }
    
    def get_fees_for_execution(self, execution_id: str) -> List[FeeRecordModel]:
        """Get all fees for a specific execution."""
        return self.db.query(FeeRecordModel).filter(
            FeeRecordModel.execution_id == execution_id
        ).all()
    
    def get_fees_for_position(self, position_id: str) -> List[FeeRecordModel]:
        """Get all fees for a specific position."""
        return self.db.query(FeeRecordModel).filter(
            FeeRecordModel.position_id == position_id
        ).all()
    
    def _get_tenant_id_from_execution(self, execution_id: str) -> UUID:
        """Look up tenant ID from execution record, or refuse.

        FEE-01. Returned `UUID(int=0)` when the execution was not found, writing the fee
        against a non-existent tenant. Now raises `TenantResolutionError`: an
        unattributable fee is not recorded against a tenant that does not exist. See that
        exception's docstring for why this breaks no production caller.
        """
        from backend_app.core.models.execution_record import \
            ExecutionRecordModel

        record = self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.execution_id == execution_id
        ).first()

        if record is None or record.tenant_id is None:
            raise TenantResolutionError(
                f"no execution record for execution_id={execution_id!r}, so the owning "
                f"tenant of this fee cannot be established. Refusing to record it rather "
                f"than attributing it to the all-zero tenant (FEE-01)."
            )
        return record.tenant_id

    def _get_tenant_id_from_position(self, position_id: str) -> UUID:
        """Look up tenant ID from position record, or refuse. See FEE-01 above."""
        from backend_app.core.position_model import PositionModel

        record = self.db.query(PositionModel).filter(
            PositionModel.position_id == position_id
        ).first()

        if record is None or record.tenant_id is None:
            raise TenantResolutionError(
                f"no position record for position_id={position_id!r}, so the owning "
                f"tenant of this funding fee cannot be established. Refusing to record "
                f"it rather than attributing it to the all-zero tenant (FEE-01)."
            )
        return record.tenant_id

    @staticmethod
    def _parse_symbol(symbol: str) -> tuple[str, str]:
        """Parse trading symbol into base and quote assets."""
        sym_clean = symbol.upper().replace("-", "/").replace("_", "/")
        if "/" in sym_clean:
            base_asset, quote_asset = sym_clean.split("/", 1)
            return base_asset, quote_asset
        for quote in ["USDT", "USDC", "BUSD", "USD", "EUR", "BTC", "ETH"]:
            if sym_clean.endswith(quote) and len(sym_clean) > len(quote):
                return sym_clean[:-len(quote)], quote
        return sym_clean, "USDT"


# Global instance
def get_fee_engine(db_session: Session) -> FeeEngine:
    """Get fee engine instance."""
    return FeeEngine(db_session)
