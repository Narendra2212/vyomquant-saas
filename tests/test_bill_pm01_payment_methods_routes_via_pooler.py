"""BILL-PM-01: the payment-methods connection could never reach the database in production.

Found during the launch new-user run: ``GET /api/billing/payment-methods`` answered 500 on the
Billing page for every user (Sentry VYOMQUANT-API-PRODUCTION-E): ``connection to server at
"db.<ref>.supabase.co" ... port 5432 failed: Network is unreachable``. That direct host is
IPv6-only and the ECS tasks have no IPv6 route; the main engine works because its URL names the
Supavisor pooler. The restricted engine now takes the same route.
"""

from __future__ import annotations

from sqlalchemy.engine import make_url

from backend_app.core.restricted_db import route_via_supabase_pooler

POOLER = "postgresql://postgres.abcref:pw@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres"


def test_a_direct_supabase_url_is_routed_through_the_pooler():
    out = make_url(route_via_supabase_pooler(
        "postgresql://payment_methods_app:s3cret@db.abcref.supabase.co:5432/postgres", POOLER))
    assert out.host == "aws-1-ap-southeast-1.pooler.supabase.com"
    assert out.port == 6543
    assert out.username == "payment_methods_app.abcref"
    assert out.password == "s3cret"
    assert out.database == "postgres"


def test_an_already_suffixed_user_is_not_suffixed_twice():
    out = make_url(route_via_supabase_pooler(
        "postgresql://payment_methods_app.abcref:pw@db.abcref.supabase.co:5432/postgres", POOLER))
    assert out.username == "payment_methods_app.abcref"


def test_other_urls_are_left_alone():
    for url in (
        "postgresql://u:p@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres",
        "postgresql://u:p@localhost:5432/test",
        "sqlite:///./x.db",
    ):
        assert route_via_supabase_pooler(url, POOLER) == url


def test_no_pooler_to_copy_means_unchanged():
    url = "postgresql://u:p@db.abcref.supabase.co:5432/postgres"
    assert route_via_supabase_pooler(url, "") == url
    assert route_via_supabase_pooler(url, "postgresql://u:p@localhost/x") == url


def test_a_password_with_special_characters_survives():
    out = make_url(route_via_supabase_pooler(
        "postgresql://payment_methods_app:p%40ss%2Fw0rd@db.abcref.supabase.co:5432/postgres", POOLER))
    assert out.password == "p@ss/w0rd"
