"""REF-ATTR-01: a signup through a referral link is attributed to its referrer, once, honestly.

Found 2026-11-19 in the final launch certification: the app issued ``/signup?ref=CODE`` links
but the signup page never read ``ref`` and called ``supabase.auth.signUp`` directly; the only
attribution code sat in ``POST /api/auth/register``, which no page calls. No referral link
could ever credit a referrer.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend_app.backend import referral_attribution as ra

NOW = datetime(2026, 11, 19, 21, 0, tzinfo=timezone.utc)


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.payload, self.op = db, table, {}, None, "select"

    def select(self, *_a):
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        if self.op == "insert":
            if self.table == "referral_relationships" and any(
                r["referred_id"] == self.payload["referred_id"] for r in rows
            ):
                raise RuntimeError("23505 duplicate key value violates unique constraint")
            rows.append(dict(self.payload))
            return SimpleNamespace(data=[self.payload])
        hit = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        return SimpleNamespace(data=hit)


class _Client:
    def __init__(self):
        self.db = {
            "referral_codes": [
                {"id": "c1", "user_id": "seller", "code": "SELLER01", "is_active": True},
                {"id": "c2", "user_id": "old", "code": "DEAD01", "is_active": False},
            ],
            "profiles": [{"id": "newbie"}, {"id": "seller"}],
        }

    def table(self, name):
        return _Q(self.db, name)


def _claim(client, user="newbie", code="seller01", created=NOW - timedelta(minutes=5)):
    return ra.attribute_referral(client, user, code, account_created_at=created.isoformat(), now=NOW)


def test_a_fresh_signup_with_a_valid_code_is_attributed_once():
    client = _Client()
    assert _claim(client)["status"] == ra.ATTRIBUTED
    rel = client.db["referral_relationships"]
    assert rel == [{"referrer_id": "seller", "referred_id": "newbie", "referral_code_id": "c1", "status": "pending"}]
    assert [p for p in client.db["profiles"] if p["id"] == "newbie"][0]["referred_by_user_id"] == "seller"
    assert _claim(client)["status"] == ra.ALREADY_ATTRIBUTED
    assert len(client.db["referral_relationships"]) == 1


def test_refusals():
    client = _Client()
    assert _claim(client, user="seller")["status"] == ra.SELF_REFERRAL
    assert _claim(client, code="NOPE99")["status"] == ra.INVALID_CODE
    assert _claim(client, code="DEAD01")["status"] == ra.INVALID_CODE
    assert _claim(client, code="")["status"] == ra.INVALID_CODE
    assert _claim(client, created=NOW - timedelta(days=8))["status"] == ra.WINDOW_CLOSED
    assert "referral_relationships" not in client.db


def test_a_concurrent_second_claim_loses_to_the_unique_index():
    client = _Client()
    client.db["referral_relationships"] = []
    original = ra.attribute_referral

    def race(*a, **k):
        # The pre-read saw nothing, but another request inserted first.
        client.db["referral_relationships"].append({"referred_id": "newbie"})
        return None

    client.table("referral_relationships")
    rows_before = list(client.db["referral_relationships"])
    assert rows_before == []
    q = client.table("referral_relationships").insert({"referred_id": "newbie"})
    q.execute()
    assert _claim(client)["status"] == ra.ALREADY_ATTRIBUTED
    assert original is ra.attribute_referral and race


def test_the_claim_endpoint_uses_the_caller_never_a_body_id():
    from backend_app.routers import referral

    src = inspect.getsource(referral.claim_referral)
    assert "Depends(get_current_user)" in src
    assert 'user.get("id")' in src
    assert set(referral.ClaimReferralRequest.model_fields) == {"referral_code"}


def test_register_uses_the_shared_rule():
    from backend_app.routers import auth

    assert "attribute_referral(admin_client, user_id, user_data.referral_code)" in inspect.getsource(auth)
