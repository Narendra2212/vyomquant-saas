"""MKT-06: a published listing whose creator has no display alias must still be served.

Production, 2026-11-11: the first listing ever published (31530d24) answered 503
MARKETPLACE_READ_FAILED on browse and detail - "creator_alias must be a non-empty display
alias" - because no profile has a display_name (0 of 184). ``aliases.resolve_aliases`` omits
such creators by design and its contract says the creator field is omitted, never substituted.
"""

from __future__ import annotations

import pytest

from backend_app.backend.marketplace.listing_projection import project_listing

ROW = {
    "id": "31530d24-d5ff-469c-afe3-66e07d9ef6cc",
    "name": "claude-audit-ema-sma",
    "author_id": "7169eb77-cccd-4f14-b3dc-5d13cfd386f4",
    "category": "trend_following",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "price_minor": None,
    "currency": "USD",
    "published_at": "2026-11-11T11:20:45+00:00",
    "subscriber_count": 0,
    "source_cloning_enabled": False,
}


@pytest.mark.parametrize("alias", [None, "", "   "])
def test_a_listing_without_an_alias_is_projected_with_the_field_omitted(alias) -> None:
    out = project_listing(ROW, None, alias)
    assert "creator_alias" not in out
    assert ROW["author_id"] not in str(out), "nothing may be substituted for the alias"


def test_a_real_alias_is_still_served() -> None:
    assert project_listing(ROW, None, "QuantNinja")["creator_alias"] == "QuantNinja"


def test_an_alias_that_is_the_author_id_is_still_refused() -> None:
    with pytest.raises(ValueError, match="author_id"):
        project_listing(ROW, None, ROW["author_id"])
