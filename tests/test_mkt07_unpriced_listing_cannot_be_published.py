"""MKT-07: an approved Submission whose Listing has no price cannot be published.

Checkout refuses an unpriced Listing (reason=no_price) and there is no free-access path, so a
published unpriced Listing was on the Marketplace with no way for anyone to subscribe.
"""

from __future__ import annotations

import uuid

import pytest

from backend_app.backend.marketplace.errors import (
    MARKETPLACE_LISTING_NOT_PURCHASABLE,
    MarketplaceError,
)
from backend_app.routers import library

SUB = str(uuid.uuid4())
LISTING = str(uuid.uuid4())


class _Q:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _Svc:
    def __init__(self, price_minor):
        self.tables = {
            "marketplace_submissions": [{"id": SUB, "listing_id": LISTING}],
            "library_strategies": [{"id": LISTING, "price_minor": price_minor}],
        }

    def table(self, name):
        return _Q(self.tables[name])


def test_an_unpriced_listing_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(library, "_require_service_client", lambda: _Svc(None))
    with pytest.raises(MarketplaceError) as err:
        library._require_priced_listing_for_publish(SUB)
    assert err.value.code == MARKETPLACE_LISTING_NOT_PURCHASABLE
    assert err.value.details["reason"] == "no_price"


def test_a_priced_listing_passes(monkeypatch) -> None:
    monkeypatch.setattr(library, "_require_service_client", lambda: _Svc(1000))
    library._require_priced_listing_for_publish(SUB)
