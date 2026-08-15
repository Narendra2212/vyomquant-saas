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
from uuid import UUID

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
    execution_id = Column(String, ForeignKey("execution_records.execution_id"), nullable=False)
    order_id = Column(String, nullable=False, index=True)
    position_id = Column(String, ForeignKey("positions.position_id"), nullable=True)
    
    # Trade details
    symbol = Column(String, nullable=False)
    side = Column(String, nullable=False)
    
    # Fee details (stored as strings for precision)
    fee_type = Column(SQLEnum(FeeType), nullable=False)
    fee_amount = Column(String, nullable=False)  # "0.00050000"
    fee_asset = Column(String, nullable=False)   # "USDT"
    fee_pct = Column(String, nullable=False)     # "0.00050000" (0.05%)
    
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
        # BUG-FIX FEE-01: Normalize fee to quote currency before calculating fee_pct and net_pnl.
        # Symbols can be formatted as "BTC/USDT", "BTC-USDT", or "BTCUSDT".
        sym_clean = symbol.upper().replace("-", "/").replace("_", "/")
        if "/" in sym_clean:
            base_asset, quote_asset = sym_clean.split("/", 1)
        else:
            # Common default quote asset assumption if no delimiter
            quote_asset = "USDT"
            base_asset = sym_clean.replace("USDT", "").replace("USD", "").replace("USDC", "")

        fee_asset_upper = fee_asset.upper()
        if fee_asset_upper == base_asset.upper():
            # Fee paid in base asset (e.g. BTC): Value in quote currency is fee_amount * price
            fee_cost_in_quote = (fee_amount * price).quantize(self.PRECISION, rounding=ROUND_HALF_UP)
        else:
            # Fee paid in quote asset (e.g. USDT) or third asset
            fee_cost_in_quote = fee_amount

        # Calculate trade volume
        trade_volume = size * price
        
        # Calculate fee percentage relative to trade volume
        if trade_volume > 0:
            fee_pct = (fee_cost_in_quote / trade_volume).quantize(self.PRECISION, rounding=ROUND_HALF_UP)
        else:
            fee_pct = Decimal('0')
        
        # Calculate net PnL if gross provided (deduct quote-denominated fee cost)
        net_pnl = None
        if gross_pnl is not None:
            net_pnl = (gross_pnl - fee_cost_in_quote).quantize(self.PRECISION, rounding=ROUND_HALF_UP)
        
        # Determine status
        status = FeeStatus.REBATED if fee_amount < 0 else FeeStatus.DEDUCTED
        
        # Create record
        fee_record = FeeRecordModel(
            fee_id=f"fee_{execution_id}_{int(datetime.utcnow().timestamp())}",
            tenant_id=self._get_tenant_id_from_execution(execution_id),
            execution_id=execution_id,
            order_id=order_id,
            position_id=position_id,
            symbol=symbol.upper(),
            side=side,
            fee_type=fee_type,
            fee_amount=str(fee_amount),
            fee_asset=fee_asset.upper(),
            fee_pct=str(fee_pct),
            trade_volume=str(trade_volume),
            gross_pnl=str(gross_pnl) if gross_pnl else None,
            net_pnl=str(net_pnl) if net_pnl else None,
            status=status,
            exchange_id=exchange_id
        )
        
        self.db.add(fee_record)
        self.db.commit()
        
        logger.info(
            f"FEE RECORDED: {fee_record.fee_id} | "
            f"{symbol} {side} | "
            f"type={fee_type.value} | "
            f"amount={fee_amount} {fee_asset} | "
            f"pct={fee_pct * 100:.4f}%"
        )
        
        return fee_record
    
    async def record_funding_fee(
        self,
        position_id: str,
        symbol: str,
        fee_amount: Decimal,
        fee_asset: str,
        exchange_id: str,
        funding_period: str  # e.g., "2026-05-02-0800"
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
        self.db.commit()
        
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
        
        # Aggregate by type
        totals = {
            FeeType.MAKER: Decimal('0'),
            FeeType.TAKER: Decimal('0'),
            FeeType.FUNDING: Decimal('0'),
            FeeType.LIQUIDATION: Decimal('0'),
        }
        
        total_volume = Decimal('0')
        
        for fee in fees:
            fee_amount = Decimal(fee.fee_amount)
            totals[fee.fee_type] += fee_amount
            total_volume += Decimal(fee.trade_volume)
        
        # Calculate total (fees are costs, so positive = paid)
        total_fees = sum(totals.values())
        
        # Calculate average fee rate
        avg_fee_rate = (
            (total_fees / total_volume * 100)
            if total_volume > 0 else Decimal('0')
        )
        
        return {
            "total_fees": str(total_fees),
            "total_volume": str(total_volume),
            "avg_fee_rate_pct": str(avg_fee_rate.quantize(self.PRECISION)),
            "by_type": {
                ft.value: str(amount) for ft, amount in totals.items()
            },
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
        """Look up tenant ID from execution record."""
        from backend_app.core.models.execution_record import \
            ExecutionRecordModel
        
        record = self.db.query(ExecutionRecordModel).filter(
            ExecutionRecordModel.execution_id == execution_id
        ).first()
        
        return record.tenant_id if record else UUID(int=0)
    
    def _get_tenant_id_from_position(self, position_id: str) -> UUID:
        """Look up tenant ID from position record."""
        from backend_app.core.position_model import PositionModel
        
        record = self.db.query(PositionModel).filter(
            PositionModel.position_id == position_id
        ).first()
        
        return record.tenant_id if record else UUID(int=0)


# Global instance
def get_fee_engine(db_session: Session) -> FeeEngine:
    """Get fee engine instance."""
    return FeeEngine(db_session)
