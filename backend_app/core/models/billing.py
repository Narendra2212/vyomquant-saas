# core/models/billing.py

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from backend_app.core.database import Base


# DEPRECATED: SubscriptionModel and InvoiceModel removed
# Supabase profiles table is the single source of truth for billing state
# PaymentMethodModel retained temporarily for payment method display endpoints


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
