# core/models/billing.py

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, Integer, String

from backend_app.core.database import Base


class SubscriptionModel(Base):
    """SQLAlchemy model for local fallback subscriptions table."""
    __tablename__ = "subscriptions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False, unique=True, index=True)
    plan_id = Column(String(32), nullable=False, default="free")  # free, starter, pro, enterprise
    name = Column(String(64), nullable=False, default="Free")
    priceUSD = Column(Float, nullable=False, default=0.0)
    priceINR = Column(Float, nullable=False, default=0.0)
    features = Column(JSON, nullable=True)
    nextBillingDate = Column(String(64), nullable=True)
    autoRenew = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.plan_id,  # Map plan_id to id for frontend compatibility
            "name": self.name,
            "priceUSD": self.priceUSD,
            "priceINR": self.priceINR,
            "features": self.features or [],
            "nextBillingDate": self.nextBillingDate,
            "autoRenew": self.autoRenew,
        }


class InvoiceModel(Base):
    """SQLAlchemy model for local fallback invoices table."""
    __tablename__ = "invoices"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False, index=True)
    date = Column(String(64), nullable=False)
    amtUSD = Column(Float, nullable=False, default=0.0)
    amtINR = Column(Float, nullable=False, default=0.0)
    status = Column(String(32), nullable=False, default="pending")  # paid, pending, failed
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.date,
            "amtUSD": self.amtUSD,
            "amtINR": self.amtINR,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PaymentMethodModel(Base):
    """SQLAlchemy model for local fallback payment_methods table.
    
    SECURITY: No hardcoded defaults for card metadata fields (brand, last4,
    expiry_month, expiry_year). Values MUST come from the Stripe PaymentMethod
    object. Hardcoding test values (e.g., '4242', 'Visa') was a P0 data
    integrity issue that could persist fake card data to production.
    """
    __tablename__ = "payment_methods"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False, index=True)
    payment_method_id = Column(String(64), nullable=False)
    # Real card metadata — sourced from Stripe, no defaults allowed
    brand = Column(String(32), nullable=False)
    last4 = Column(String(4), nullable=False)
    expiry_month = Column(Integer, nullable=False)
    expiry_year = Column(Integer, nullable=False)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(String(64), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "payment_method_id": self.payment_method_id,
            "brand": self.brand,
            "last4": self.last4,
            "expiry_month": self.expiry_month,
            "expiry_year": self.expiry_year,
            "is_default": self.is_default,
            "created_at": self.created_at,
        }
