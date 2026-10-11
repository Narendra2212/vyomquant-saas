# core/models/billing.py

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Uuid

from backend_app.core.database import Base

#: `payment_methods.user_id` is `uuid` in the database since alembic `d4e5f6a7b8c9`
#: (BUG-049), which also declared `fk_bug049_payment_methods_user_id -> profiles(id)
#: ON DELETE RESTRICT`. The model has to agree or every read through it breaks.
#:
#: `as_uuid=False` is deliberate and is the same choice `core/models/execution_tables.py`
#: made for `TenantId`. With `as_uuid=True` SQLAlchemy returns `uuid.UUID` objects, and
#: every comparison of the form `row.user_id != user["id"]` -- where the right side is a
#: string from a JWT claim -- would silently become false. That exact mistake, with
#: `as_uuid=True`, would have made `routers/dag_tasks.py:149` reject every legitimate
#: owner. Returning `str` keeps those comparisons working and keeps the API's JSON shape
#: unchanged.
#:
#: Also note `as_uuid=False` still VALIDATES on write: a non-UUID string is rejected by
#: the database, which is the integrity guarantee BUG-049 was about.
CustomerId = Uuid(as_uuid=False)


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

    # `id` is still `character varying(36)` in the database. Left alone on purpose:
    # BUG-049 is about CUSTOMER IDENTITY columns, and a surrogate primary key on a local
    # fallback table is neither a customer identity nor referenced by anything. Widening
    # the change to cover it would mean altering a primary key for tidiness.
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(CustomerId, nullable=False, index=True)
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
